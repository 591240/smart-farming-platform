"""C1~C3 冒烟：喂食中心（配方大脑/执行工作台/数据闭环）+ 气象域后端化。

运行：
    cd backend && python run.py            # 另开窗口
    python smoke_feeding.py [base_url]

覆盖口径（都是"错了就会误导养殖户"的地方）：
    配方比例合计 100% / 原料价格完备；计划量三级优先（生效配方 → 标准兜底 → 无计划）；
    EX-14 超 3 倍确认、EX-15 均重骤降确认、planned_kg 快照不被后续改配方回写；
    料肉比四种缺数据情形各自拒算（绝不返回 0）；母猪妊娠/哺乳按在栏母畜状态分档；
    改价联动重算生效配方成本；气象无 key/无缓存 → 50003，降级带 degraded 且省略号键位；
    climate_risk 规则引擎（≥3 命中日升 high、官方预警同因合并取高、未映射预警透传）。
末行 PASS n / FAIL n，有失败退出码 1。
"""

from __future__ import annotations

import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

from app.core.config import settings

# Windows 控制台默认 GBK：断言名里的「−」等非 GBK 字符会在 print 时抛
# UnicodeEncodeError 直接中断整组测试（与业务无关的假失败）。统一按 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else f"http://{settings.ai_host}:8001/api").rstrip("/")

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        FAILED.append(f"{name} | {detail}")
        print(f"  FAIL {name} | {str(detail)[:110]}")


def dstr(days_ago: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=60, trust_env=False)
    phone = "136" + "".join(random.choice("0123456789") for _ in range(8))
    c.post("/auth/register", json={"phone": phone, "password": "Farm123456"})
    tk = c.post("/auth/login", json={"phone": phone, "password": "Farm123456"}).json()["data"]["token"]
    H = {"Authorization": f"Bearer {tk}"}
    c.put("/user/main-species", json={"main_species_code": "pig"}, headers=H)
    farm = c.post("/farm", json={"farm_name": "喂食冒烟场", "province": "湖南省", "city": "长沙市",
                                 "county": "长沙县", "adcode": "430121"}, headers=H).json()["data"]["farm_id"]
    pen = c.post("/pen", json={"farm_id": farm, "pen_name": "1号育肥圈", "species_code": "pig",
                               "variety_code": "pig_fattening", "male_count": 20, "female_count": 20,
                               "batch_stage": "fattening"}, headers=H).json()["data"]["pen_id"]

    print("\n== 配方大脑 ==")
    g = c.post("/feed/formula/generate", json={"pen_id": pen}, headers=H).json()
    check("生成配方成功", g["code"] == 0, g)
    f = g["data"]
    total = sum(i["ratio_pct"] for i in f["ingredients"])
    check("原料比例合计≈100", 97 <= total <= 101, total)
    check("每头每天喂量已落到原料", all(i["daily_kg_per_head"] > 0 for i in f["ingredients"]), f["ingredients"][:2])
    check("成本已按原料价核算", (f["cost_per_head_day"] or 0) > 0, f["cost_per_head_day"])
    check("营养达标逐项给出", len(f["nutrition_check"]) >= 3, list(f["nutrition_check"]))
    check("附溯源依据与免责标注", bool(f["basis"]) and f["disclaimer"] == "AI 生成，仅供参考", f["basis"][:40])
    check("生成即候选态（未生效）", f["is_active"] is False, f["is_active"])

    alt = c.post("/feed/formula/alternatives", json={"pen_id": pen, "constraints": {"exclude_ingredients": ["豆粕"]}}, headers=H).json()
    check("排除豆粕后给出替代方案", alt["code"] == 0 and len(alt["data"]["options"]) >= 2, alt)
    if alt["code"] == 0:
        opts = alt["data"]["options"]
        check("候选均不再含豆粕", all("豆粕" not in [i["name"] for i in o["formula"]["ingredients"]] for o in opts),
              [o["formula"]["ingredients"] for o in opts[:1]])
        check("每套都带成本与达标标记", all(o["cost_per_head_day"] and "nutrition_pass" in o for o in opts), opts[:1])
        check("方案标题带序号", all(o["label"].startswith("方案") for o in opts), [o["label"] for o in opts])

    bad = c.post("/feed/formula/alternatives", json={"pen_id": pen, "constraints": {"exclude_ingredients": ["玉米", "豆粕", "麸皮", "预混料", "菜籽粕", "鱼粉", "稻谷", "青贮玉米", "干草粉"]}}, headers=H).json()
    check("排除到无解时明确报错而非硬凑", bad["code"] == 42201, bad)

    act = c.post(f"/feed/formula/{f['formula_id']}/activate", headers=H).json()
    check("配方生效成功", act["code"] == 0 and act["data"]["is_active"] is True, act)
    check("生效落台账（更换配方留痕）", (act["data"].get("ledger_record_id") or 0) > 0, act)

    print("\n== 饲喂计划与执行 ==")
    plan = c.get(f"/pen/{pen}/feed-plan", headers=H).json()["data"]
    check("计划来源=生效配方", plan["plan_source"] == "formula", plan["plan_source"])
    # 计划量必须等于「每头日喂量 × 存栏」再乘气象调整系数。系数从 basis 里回读：
    # 气象缓存预热进维护任务后，只要当天预报真超阈值就一定带"高温下调 X%"，
    # 断言不能假设"永远不调整"（那等于把 F06 FR-05 的高温联动写死不生效）。
    import re as _re

    _m = _re.search(r"高温下调 (\d+(?:\.\d+)?)%", plan.get("basis") or "")
    _factor = 1 - (float(_m.group(1)) / 100) if _m else 1.0
    _expect = round(sum(i["daily_kg_per_head"] for i in f["ingredients"]) * 40 * _factor, 2)
    check(
        f"计划量=每头日喂量×存栏 40×气象系数 {_factor:g}",
        abs(plan["daily_plan_kg"] - _expect) < 1.5,
        {"expect": _expect, **{k: plan[k] for k in ("daily_plan_kg", "basis") if k in plan}},
    )
    check("每顿喂量与顿数自洽", plan["per_meal_kg"] and plan["meals_per_day"] == f["feeds_per_day"], plan)
    check("高温下调有依据标注（若命中）", "高温下调" in plan["basis"] or "依据气象阈值" not in plan["basis"], plan["basis"])

    over = c.post("/feeding/record", json={"pen_id": pen, "feed_date": dstr(1), "feed_type": "concentrate", "amount_kg": 9999}, headers=H).json()
    check("EX-14 超 3 倍要求确认", over["code"] == 40005 and over["data"]["confirm_type"] == "feed_over", over)
    ok2 = c.post("/feeding/record", json={"pen_id": pen, "feed_date": dstr(1), "feed_type": "concentrate", "amount_kg": 9999, "confirm": True}, headers=H).json()
    check("确认后允许登记", ok2["code"] == 0, ok2)
    snap = ok2["data"]["planned_kg"]
    c.post("/feeding/record", json={"pen_id": pen, "feed_date": dstr(1), "feed_type": "concentrate", "amount_kg": 80}, headers=H)
    lst = c.get(f"/feeding/record?pen_id={pen}&from={dstr(1)}&to={dstr(1)}", headers=H).json()["data"]
    check("同日同类型覆盖更新（不产生第二行）", lst["total"] == 1, lst["total"])
    check("计划快照随登记写入", lst["items"][0]["planned_kg"] == snap, lst["items"][0])

    ov = c.get(f"/feeding/overview?date={dstr(1)}", headers=H).json()["data"]
    check("日历汇总含该圈且已登记", ov["items"] and ov["items"][0]["registered"] is True, ov)
    check("实际量按日累加", ov["items"][0]["actual_kg"] == 80.0, ov["items"][0])

    print("\n== 称重 → 日增重 → 料肉比 ==")
    w1 = c.post("/feeding/weight", json={"pen_id": pen, "weigh_date": dstr(30), "avg_weight_kg": 50, "sample_count": 5}, headers=H).json()
    check("首次称重无日增重", w1["code"] == 0 and w1["data"]["daily_gain_kg"] is None, w1)
    w2 = c.post("/feeding/weight", json={"pen_id": pen, "weigh_date": dstr(16), "avg_weight_kg": 62, "sample_count": 5}, headers=H).json()
    # (62-50) / 14 天 = 0.8571：口径是"相邻两次称重差 ÷ 间隔天数"，不是与品种标准比
    check("日增重自动计算", abs((w2["data"]["daily_gain_kg"] or 0) - 0.8571) < 0.001, w2["data"])
    check("vs 品种标准给出 ratio/level", w2["data"]["vs_standard"]["level"] == "normal", w2["data"]["vs_standard"])
    drop = c.post("/feeding/weight", json={"pen_id": pen, "weigh_date": dstr(2), "avg_weight_kg": 40, "sample_count": 5}, headers=H).json()
    check("EX-15 均重骤降需确认", drop["code"] == 40005 and drop["data"]["confirm_type"] == "weight_drop", drop)
    confirmed = c.post("/feeding/weight", json={"pen_id": pen, "weigh_date": dstr(2), "avg_weight_kg": 40,
                                                "sample_count": 5, "confirm": True}, headers=H).json()
    check("确认后允许录入（真掉膘也要留痕）", confirmed["code"] == 0, confirmed)
    check("掉膘日增重为负值", (confirmed["data"]["daily_gain_kg"] or 0) < 0, confirmed["data"])
    dg = c.get(f"/pen/{pen}/daily-gain", headers=H).json()["data"]
    check("曲线点数与标准线齐备", len(dg["points"]) == 3 and dg["standard_line"] == 0.75, dg)

    fc0 = c.get(f"/pen/{pen}/feed-conversion", headers=H).json()["data"]
    check("未出栏时明确拒算而非给 0", fc0["fcr"] is None and fc0["reason"] == "批次未出栏", fc0)
    c.post("/feeding/record", json={"pen_id": pen, "feed_date": dstr(0), "feed_type": "concentrate", "amount_kg": 100}, headers=H)
    # F14 强关联：部分出栏必须指定耳标，否则 42201——先确认这道闸在喂食链路上也生效
    no_tag = c.post(f"/pen/{pen}/slaughter", json={"male_count": 10, "female_count": 10, "avg_weight_kg": 110}, headers=H).json()
    check("部分出栏未带耳标被拦（F14 强关联）", no_tag["code"] == 42201, no_tag)
    tags = c.get(f"/individual/list?pen_id={pen}&status=active&page_size=100", headers=H).json()["data"]["items"]
    pick = [t["ear_tag_no"] for t in tags if t["gender"] == "male"][:10] + [t["ear_tag_no"] for t in tags if t["gender"] == "female"][:10]
    sl = c.post(f"/pen/{pen}/slaughter", json={"male_count": 10, "female_count": 10, "avg_weight_kg": 110,
                                               "destination": "冒烟屠宰点", "tag_nos": pick}, headers=H).json()
    check("出栏登记成功（含耳标）", sl["code"] == 0, sl)
    fc = c.get(f"/pen/{pen}/feed-conversion", headers=H).json()["data"]
    check("出栏后可算料肉比", fc.get("fcr") and fc["fcr"] > 0, fc)
    check("期初体重=首次均重×出栏头数", fc["first_avg_weight_kg"] == 50.0 and fc["head_count"] == 20, fc)
    check("总增重=出栏总重−期初", abs(fc["total_gain_kg"] - (2200.0 - 1000.0)) < 0.01, fc)

    print("\n== 原料价 ==")
    prices = c.get("/feed/price", headers=H).json()["data"]["items"]
    check("九种原料价齐备", len(prices) >= 9, len(prices))
    check(
        "按价格陈旧度倒序",
        [p["price_age_days"] for p in prices] == sorted((p["price_age_days"] for p in prices), reverse=True),
        [(p["ingredient_name"], p["price_age_days"]) for p in prices[:3]],
    )
    cost_before = c.get(f"/pen/{pen}/feed-plan", headers=H).json()["data"]["active_formula"]["cost_per_head_day"]
    corn_now = next(p["unit_price"] for p in prices if p["ingredient_name"] == "玉米")
    # 动态加价而非写死 4.4：feed_price 是**全平台共享表**（02-数据库无 user_id 维度），
    # 上一次冒烟运行改过的价会留下来，写死同值就成了"无变化"。共享语义见 08 文档 C-18。
    corn_new = round(corn_now * 2 + 0.11, 2)
    upd = c.post("/feed/price", json={"ingredient_name": "玉米", "unit_price": corn_new}, headers=H).json()
    check("改价成功并联动重算生效配方", upd["code"] == 0 and len(upd["data"]["affected"]) >= 1, upd)
    cost_after = c.get(f"/pen/{pen}/feed-plan", headers=H).json()["data"]["active_formula"]["cost_per_head_day"]
    check("玉米涨价 → 生效配方成本上升", (cost_after or 0) > (cost_before or 0), (cost_before, cost_after, corn_now, corn_new))
    hist = c.get("/feed/price", headers=H).json()["data"]["items"]
    check("历史价保留（追加式，不覆盖）", next(p for p in hist if p["ingredient_name"] == "玉米")["unit_price"] == corn_new, hist[:1])

    print("\n== 气象域 ==")
    # 不断言"有没有 key"：key 只在服务端进程里（后端 .env），冒烟进程看不到。
    # 改为对两种合法结果都成立：拿到数据就校验结构与降级标注，拿不到就必须是 50003（绝不编造）
    w = c.get("/weather", headers=H).json()
    if w["code"] == 50003:
        check("不可用时明确 50003（绝不编造假天气）", True)
    else:
        d = w["data"]
        check("有 key → 取到真实预报", d["available"] is True, w)
        check("预报≤3 天且字段齐", 1 <= len(d["forecast"]) <= 3 and {"date", "temp_min", "temp_max", "text_day", "precip_mm", "wind_level"} <= set(d["forecast"][0]), d.get("forecast"))
        check("risk 结构完整或为 none", d["risk"]["level"] in ("none", "low", "mid", "high"), d["risk"])
        check("未降级时不出现 degraded 键", "degraded" not in d, list(d))
        second = c.get("/weather", headers=H).json()["data"]
        check("6h 内二次请求不再打外部接口（source=cache）", second["source"] == "cache", second["source"])
        check("缓存复用不改变数据时间", second["data_time"] == d["data_time"], (d["data_time"], second["data_time"]))
    nof = httpx.Client(base_url=BASE, timeout=30, trust_env=False)
    p2 = "136" + "".join(random.choice("0123456789") for _ in range(8))
    nof.post("/auth/register", json={"phone": p2, "password": "Farm123456"})
    t2 = nof.post("/auth/login", json={"phone": p2, "password": "Farm123456"}).json()["data"]["token"]
    r = nof.get("/weather", headers={"Authorization": f"Bearer {t2}"}).json()
    check("无养殖场 → available:false reason=no_farm", r["data"].get("reason") == "no_farm", r)
    f2 = nof.post("/farm", json={"farm_name": "无区码场"}, headers={"Authorization": f"Bearer {t2}"}).json()["data"]["farm_id"]
    r2 = nof.get("/weather", headers={"Authorization": f"Bearer {t2}"}).json()
    check("缺 adcode → 引导补地区而非报错", r2["data"].get("reason") == "no_adcode" and r2["data"].get("farm_id") == f2, r2)

    print("\n== climate_risk 规则引擎（纯函数）==")
    from app.api.rules import climate_risk
    from app.services.weather import _match_risks, aggregate_risk

    def fc(tmax, tmin=15, mm=0, wind=3, day="2026-10-15"):
        return {"date": day, "temp_min": tmin, "temp_max": tmax, "text_day": "晴", "precip_prob": 0, "precip_mm": mm, "wind_level": wind}

    hot3 = [fc(36), fc(35.5), fc(35.2)]
    m = _match_risks(climate_risk("pig"), hot3, [], "猪")
    check("连续 3 天高温 → high", len(m) == 1 and m[0]["level"] == "high" and m[0]["risk_type"] == "heat_stress", m)
    check("规则命中加物种前缀", m[0]["title"] == "猪·高温风险", m[0]["title"])
    m2 = _match_risks(climate_risk("pig"), [fc(36), fc(20), fc(20)], [], "猪")
    check("仅 1 天命中 → mid", m2[0]["level"] == "mid", m2[0])
    m3 = _match_risks(climate_risk("pig"), [fc(36), fc(36), fc(36)], [{"type": "大风", "level": "orange", "title": "大风橙色预警"}], "猪")
    check("官方预警同因合并取高", any(x["risk_type"] == "gale" and x["level"] == "high" and x["warning_title"] for x in m3), m3)
    gale = next((x for x in m3 if x["risk_type"] == "gale"), {})
    check("官方预警标题原文保留（展示取 warning_title，不加物种前缀）",
          gale.get("warning_title") == "大风橙色预警" and not gale.get("title", "").startswith("猪"), gale)
    m4 = _match_risks(climate_risk("pig"), [fc(30)], [{"type": "森林火险", "level": "red", "title": "森林火险红色预警"}], "猪")
    check("未映射预警原样透传", any("森林火险" in x["title"] for x in m4), [x["title"] for x in m4])
    m5 = _match_risks(climate_risk("cattle"), [fc(31), fc(31), fc(31)], [], "牛")
    check("牛高温阈值 30℃ 生效", any(x["risk_type"] == "heat_stress" for x in m5), m5)
    m6 = _match_risks(climate_risk("unknown_species"), [fc(45)], [], "猪")
    check("EX-W7 未知物种零命中不报错", m6 == [], m6)
    agg = aggregate_risk([{**m[0], "pen_id": 1, "pen_name": "1号圈", "species": "猪", "data_hint": "x"}], {"forecast": hot3})
    check("聚合 risk 取最高并出 items", agg["level"] == "high" and agg["items"][0]["pen_name"] == "1号圈", agg)
    check("无风险时 level=none", aggregate_risk([], {"forecast": []}) == {"level": "none"})

    print("\n== 母猪妊娠/哺乳分档（F07 遗留项①收口）==")
    sow_pen = c.post("/pen", json={"farm_id": farm, "pen_name": "2号母猪圈", "species_code": "pig",
                                   "variety_code": "pig_sow", "male_count": 0, "female_count": 3}, headers=H).json()["data"]["pen_id"]
    p_g = c.get(f"/pen/{sow_pen}/feed-plan", headers=H).json()["data"]
    check("无哺乳母畜 → 妊娠档", "妊娠" in p_g["basis"], p_g["basis"][:70])
    kg_gest = p_g["daily_plan_kg"]
    fid = c.post("/breeding-female", json={"pen_id": sow_pen, "tag_no": "S-9", "parity": 1}, headers=H).json()["data"]["female_id"]
    c.post("/lifecycle/event", json={"event_type": "breeding", "female_id": fid, "event_date": dstr(120)}, headers=H)
    c.post("/lifecycle/event", json={"event_type": "pregnancy_check", "female_id": fid, "detail": {"result": "阳性"}}, headers=H)
    c.post("/lifecycle/event", json={"event_type": "birth", "female_id": fid, "detail": {"live_male": 2, "live_female": 2, "dead_count": 0}}, headers=H)
    p_l = c.get(f"/pen/{sow_pen}/feed-plan", headers=H).json()["data"]
    check("分娩后自动切哺乳档", "哺乳" in p_l["basis"], p_l["basis"][:70])
    check("哺乳档计划量高于妊娠档", (p_l["daily_plan_kg"] or 0) > (kg_gest or 0), (kg_gest, p_l["daily_plan_kg"]))

    print("\n== 越权与角色 ==")
    other = "136" + "".join(random.choice("0123456789") for _ in range(8))
    oc = httpx.Client(base_url=BASE, timeout=30, trust_env=False)
    oc.post("/auth/register", json={"phone": other, "password": "Farm123456"})
    ot = oc.post("/auth/login", json={"phone": other, "password": "Farm123456"}).json()["data"]["token"]
    OH = {"Authorization": f"Bearer {ot}"}
    check("读他人圈饲喂记录 40302", oc.get(f"/feeding/record?pen_id={pen}", headers=OH).json()["code"] == 40302)
    check("对他人生效配方 40302", oc.post(f"/feed/formula/{f['formula_id']}/activate", headers=OH).json()["code"] == 40302)
    check("他人可读到自己的空日历（不受他人数据污染）", c.get("/feeding/overview", headers=OH).json()["data"]["items"] == [], c.get("/feeding/overview", headers=OH).json())
    check("本人喂食接口正常可用", c.get("/feeding/overview", headers=H).json()["code"] == 0)

    print("\n== 演示数据注入（真后端版 /feeding/seed-demo）==")
    # 单独开一个干净账号：注入选择"有存栏且无饲喂记录"的圈，已有数据的账号会被幂等跳过
    dc = httpx.Client(base_url=BASE, timeout=30, trust_env=False)
    dphone = "139" + "".join(random.choices("0123456789", k=8))
    dc.post("/auth/register", json={"phone": dphone, "password": "Farm123456"})
    dh = {"Authorization": "Bearer " + dc.post("/auth/login", json={"phone": dphone, "password": "Farm123456"}).json()["data"]["token"]}
    dc.put("/user/main-species", json={"main_species_code": "pig"}, headers=dh)
    dfarm = dc.post("/farm", json={
        "farm_name": "注入冒烟场", "province": "湖南省", "city": "长沙市", "county": "长沙县",
        "adcode": "430121", "address": "注入路 1 号", "contact_person": "注", "phone": dphone,
    }, headers=dh).json()["data"]["farm_id"]
    dpen = dc.post("/pen", json={
        "farm_id": dfarm, "pen_name": "注入圈", "species_code": "pig", "variety_code": "pig_fattening",
        "male_count": 12, "female_count": 0, "batch_stage": "fattening",
    }, headers=dh).json()["data"]["pen_id"]

    seed1 = dc.post("/feeding/seed-demo", headers=dh).json()["data"]
    check("seed-demo 选中无记录的圈并注入", seed1.get("seeded") is True and seed1.get("pen_id") == dpen, seed1)
    check("计划量口径来自引擎（plan_source 合法）", seed1.get("plan_source") in ("formula", "standard", "none"), seed1.get("plan_source"))
    ovw = dc.get(f"/feeding/overview?pen_id={dpen}", headers=dh)
    recs = dc.get(f"/feeding/record?pen_id={dpen}&page_size=30", headers=dh).json()["data"]["items"]
    check("注入 10 条饲喂登记", len([r for r in recs]) >= 10, len(recs))
    check("每条都带演示标注（数据库里可查，不只写在 UI 上）", all("演示数据" in (r.get("remark") or "") for r in recs) and bool(recs))
    check("planned_kg 与计划口径一致（非硬编码）", any(r.get("planned_kg") for r in recs), [r.get("planned_kg") for r in recs[:3]])
    fcr = dc.get(f"/pen/{dpen}/feed-conversion", headers=dh).json()["data"]
    check("未注入出栏记录 → 料肉比如实拒算", fcr.get("fcr") is None and fcr.get("reason") == "批次未出栏", fcr)
    gains = dc.get(f"/pen/{dpen}/daily-gain", headers=dh).json()["data"]
    check("三次称重落库且日增重按规则算出", len(gains.get("points") or []) >= 2 or any(gains.get("points") or []), gains)
    seed2 = dc.post("/feeding/seed-demo", headers=dh).json()["data"]
    check("再次注入被幂等跳过（不覆盖用户数据）", seed2.get("seeded") is False, seed2)
    dc.close()

    print("\n== D1/D2：行情来源 + 最小成本 LP ==")
    alts = c.post("/feed/formula/alternatives", json={"pen_id": pen, "constraints": {}}, headers=H).json()["data"]["options"]
    lp = [o for o in alts if "最小成本" in o["label"]]
    check("alternatives 含 LP 最小成本方案", bool(lp), [o["label"] for o in alts])
    if lp:
        check("LP 方案营养全达标（不可行时它不该出现）", lp[0]["nutrition_pass"], lp[0].get("miss"))
        passing = [o["cost_per_head_day"] for o in alts if o["nutrition_pass"]]
        check("LP 是达标方案里最省的（可行域最优）", abs(lp[0]["cost_per_head_day"] - min(passing)) < 1e-6,
              f"LP={lp[0]['cost_per_head_day']} passing={passing}")
        check("LP 的 basis 写清求解口径", "最小成本 LP" in lp[0]["formula"]["basis"], lp[0]["formula"]["basis"][:80])
    price_items = c.get("/feed/price", headers=H).json()["data"]["items"]
    check("价格列表带来源三字段", all({"price_source", "source_site", "confidence"} <= set(p) for p in price_items))
    check("sync 拒绝无口径原料", c.post("/feed/price/sync", json={"ingredient_names": ["石灰石"]}, headers=H).json()["code"] != 0)

    # 行情校验器是纯函数，不发请求也要验——这四道过滤是"不把模型记忆当行情"的关键
    from app.services import price_sync as ps

    corpus = (
        "玉米 | 2390元/吨 | 全国 | 2026-10-08 | 你的知识库\n"
        "玉米 | 2172.86元/吨 | 全国 | 2026-10-15 | 生意社饲料频道\n"
        "豆粕 | 3370元/吨 | 天津 | 2026-10-14 | 生意社饲料频道\n"
        "麸皮 | 1440元/吨 | 全国 | 2026-10-10 | 中华粮网\n"
        "菜籽粕 | 2450元/吨 | 南通 | 2026-10-15 | 未提供"
    )
    kept = ps.verify(ps.parse_lines(corpus), [corpus])
    check("来源=「你的知识库」被丢弃（模型记忆不算行情）", kept.get("玉米", {}).get("site") == "生意社饲料频道", kept.get("玉米"))
    check("站点名缺失/未提供 → 丢弃", "菜籽粕" not in kept)
    check("元/吨→元/公斤 由本地换算", abs(kept["玉米"]["unit_price"] - 2.17) < 0.01, kept["玉米"])
    fabricated = ps.verify([{"name": "豆粕", "raw_quote": "999元/吨", "quote_date": "2026-10-14", "site": "生意社"}], [corpus])
    check("摘录回查：原文里不存在的报价被丢弃", fabricated == {}, fabricated)

    print(f"\n{'=' * 56}")
    for x in FAILED:
        print("  × " + x)
    print(f"PASS {PASS} / FAIL {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
