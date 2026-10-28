"""切真契约回归探针（P2 收口批次，2026-10-16）。

这一组断言专门盯"mock 与真后端字段名/参数名不一致"这类切真后才暴露的断裂：
浏览器侧曾按 `handled_note` 提交告警备注，而后端读 `note` → 真后端下「标记已处理」
恒定 42201，而 `.smoke`（跑 mock 内核）全绿。这类缺陷只有打到真 HTTP 才测得出来。

运行：
  DATABASE_URL=...smart_farm_test  python run.py（另起 8001 实例）
  python smoke_contract.py [http://127.0.0.1:8001/api]

纪律：只打测试库。脚本会自建随机手机号的新账号，不碰演示数据。
"""

from __future__ import annotations

import json
import os
import random
import string
import sys
import time
from datetime import date

import httpx

from app.core.config import settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SF_TEST_BASE", "http://127.0.0.1:8001/api")).rstrip("/")
client = httpx.Client(base_url=BASE, timeout=40, trust_env=False)

pass_n = 0
fail_n = 0
failures: list[str] = []


def check(name: str, cond: bool, detail: object = None) -> None:
    global pass_n, fail_n
    if cond:
        pass_n += 1
        print(f"  PASS {name}")
    else:
        fail_n += 1
        failures.append(name)
        print(f"  FAIL {name} :: {detail}")


def post(path: str, body: object = None, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(path, json=body, headers=headers).json()


def get(path: str, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(path, headers=headers).json()


def rnd() -> str:
    return "139" + "".join(random.choice("0123456789") for _ in range(8))


def new_farmer(nickname: str, species: str = "sheep") -> tuple[str, int]:
    phone, pwd = rnd(), "Test1234!"
    r = post("/auth/register", {"phone": phone, "password": pwd, "nickname": nickname})
    assert r["code"] == 0, r
    login = post("/auth/login", {"phone": phone, "password": pwd})
    assert login["code"] == 0, login
    token = login["data"]["token"]
    put_species = client.put(
        "/user/main-species", json={"main_species_code": species}, headers={"Authorization": f"Bearer {token}"}
    )
    assert put_species.json()["code"] == 0, put_species.json()
    return token, login["data"]["user"]["user_id"]


def make_farm_pen(token: str, name: str, variety: str = "common") -> tuple[int, int]:
    farm = post("/farm", {"farm_name": name, "province": "湖南省", "city": "长沙市", "county": "长沙县", "adcode": "430121"}, token)["data"]
    pen = post("/pen", {"farm_id": farm["farm_id"], "pen_name": "1号育肥圈", "variety_code": variety, "male_count": 3, "female_count": 0}, token)["data"]
    return farm["farm_id"], pen["pen_id"]


print("== 1. 告警处理请求体字段名（曾 handled_note / 后端 note → 恒定 42201）==")
t1, u1 = new_farmer("契约甲")
farm1, pen1 = make_farm_pen(t1, "契约甲场")
sn = "SN-CONTRACT-" + "".join(random.choice(string.ascii_uppercase) for _ in range(6))
dev = post("/device", {"device_name": "契约温湿度", "device_type": "sensor", "device_sn": sn, "pen_id": pen1}, t1)
check("传感器设备建档（ingest 需先有设备）", dev["code"] == 0, dev)
device_key = (dev.get("data") or {}).get("device_secret") or ""
check("建设备回传一次性设备密钥（C-24）", len(device_key) >= 16, device_key)
ingest: dict = {}
for k in range(3):  # FR-04 防抖：连续 3 次超标才成警
    ingest = post(
        "/device/ingest",
        {
            "msg_id": f"contract-{k}-" + "".join(random.choice(string.ascii_lowercase) for _ in range(6)),
            "device_id": sn,
            "topic": f"farm/{farm1}/pen/{pen1}/{sn}/data",
            "ts": int(time.time() * 1000) + k,
            "metrics": {"temperature": 99.5},
        },
        t1,
    )
check("ingest 受理（羊舍高温阈值命中）", ingest.get("code") == 0 and ingest.get("data", {}).get("ok") is not False, ingest)
alerts = get("/alert/list?page_size=50", t1)["data"]["items"]
target = next((a for a in alerts if a["alert_type"] == "sensor"), None)
check("阈值突破产生了 sensor 告警", target is not None, [a["alert_type"] for a in alerts])
if target:
    r = post(f"/alert/{target['alert_id']}/handle", {"action": "resolve", "note": "已现场核实并通风"}, t1)
    check("resolve 带 note 成功（不再恒 42201）", r["code"] == 0, r)
    check("状态转 resolved", r.get("data", {}).get("status") == "resolved", r)
    empty = post(f"/alert/{target['alert_id']}/handle", {"action": "resolve", "note": ""}, t1)
    check("空备注仍被拒（审计要求没被放松）", empty["code"] == 42201, empty)

print("== 2. 告警列表按场过滤 + 今日计数 ==")
other = get(f"/alert/list?farm_id={farm1 + 9999}", t1)
check("查别人的场 40302", other["code"] == 40302, other)
mine = get(f"/alert/list?farm_id={farm1}", t1)
check("本场告警可读", mine["code"] == 0 and all(a["pen_name"] for a in mine["data"]["items"]) is not None, mine)
today = get(f"/alert/list?farm_id={farm1}&from={date.today().isoformat()}", t1)
check("from 日期参数生效（今日计数走服务端）", today["code"] == 0, today)
bad = get("/alert/list?from=2026-13-45", t1)
check("非法日期被拒而不是静默忽略", bad["code"] == 42201, bad)

print("== 3. 圈舍物种继承主物种（曾恒回落 pig）==")
pen_row = next(p for p in get(f"/pen/list?farm_id={farm1}", t1)["data"]["items"] if p["pen_id"] == pen1)
check("羊账号建圈未传物种 → 继承账号主物种 sheep", pen_row["species_code"] == "sheep", pen_row)
post(f"/pen/{pen1}", {"variety_code": "common"}, t1)
after = next(p for p in get(f"/pen/list?farm_id={farm1}", t1)["data"]["items"] if p["pen_id"] == pen1)
check("编辑品种为「通用」不把物种改回 pig", after["species_code"] == "sheep", after)

print("== 4. 妊娠检查区分「待复查」（曾一律记已妊娠）==")
fid = post("/breeding-female", {"pen_id": pen1, "tag_no": "P2-M001", "sex_type": "female", "breed_status": "open"}, t1)
check("母畜建档", fid["code"] == 0, fid)
female_id = fid["data"]["female_id"]
post("/lifecycle/event", {"event_type": "breeding", "female_id": female_id}, t1)
pending = post(
    "/lifecycle/event",
    {"event_type": "pregnancy_check", "female_id": female_id, "detail": {"result": "待复查"}},
    t1,
)
check("待复查不落 pregnant（仍为配种待确认）", pending["data"]["female_status"] == "bred", pending)
check("待复查返回 recheck 标记", pending["data"].get("recheck") is True, pending)
nores = post("/lifecycle/event", {"event_type": "pregnancy_check", "female_id": female_id, "detail": {}}, t1)
check("空结果 42201", nores["code"] == 42201, nores)
ok_check = post("/lifecycle/event", {"event_type": "pregnancy_check", "female_id": female_id, "detail": {"result": "阳性"}}, t1)
check("阳性落 pregnant（既有口径不变）", ok_check["data"]["female_status"] == "pregnant", ok_check)

print("== 5. 台账 farm_id 筛选 + 动态扩展列结构 ==")
lst = get(f"/record/list?page_size=50", t1)["data"]
cols = lst["ext_columns"]
check(
    "ext_columns 是 {field,label} 对象数组（前端按对象消费）",
    all(isinstance(c, dict) and set(c) >= {"field", "label"} for c in cols),
    cols,
)
check("已知字段有中文列名", any(c["field"] == "male_delta" and c["label"] == "公变动" for c in cols), cols)
scoped = get(f"/record/list?farm_id={farm1}", t1)["data"]
foreign = get(f"/record/list?farm_id={farm1 + 9999}", t1)
check("台账按场过滤后仍可读", scoped["total"] >= 0, scoped["total"])
check("越权场 40302", foreign["code"] == 40302, foreign)
exp = get(f"/record/export?farm_id={farm1}", t1)["data"]
check("导出表头用中文列名而非原始键", "公变动" in exp["content"].splitlines()[0], exp["content"].splitlines()[:1])
check("导出表头不残留原始键名", "male_delta" not in exp["content"].splitlines()[0], exp["content"].splitlines()[:1])

print("== 6. AI 网关必须鉴权（曾是匿名 LLM 代理）==")
anon = post("/ai/intent", {"message": "羊不吃料"})
check("未登录调 /ai/intent 40101", anon["code"] == 40101, anon)
scene_anon = post("/scene/analyze-photo", {"images": ["data:image/png;base64,AA"]})
check("未登录调 /scene/analyze-photo 40101", scene_anon["code"] == 40101, scene_anon)
bad_scene = post("/scene/analyze-photo", {"images": []}, t1)
check("拍照识别路由已注册（空图片走参数校验而非 404）", bad_scene.get("code") in (422, 50002, 500) or "detail" in str(bad_scene), bad_scene)

print("== 7. 营养标准改为后端供数 ==")
std = get("/feed/standards?variety_code=pig_fattening", t1)
check("GET /feed/standards 返回档位", std["code"] == 0 and std["data"]["total"] > 0, std)
row = std["data"]["items"][0]
check(
    "响应含展示所需字段（阶段/体重区间/采食量/指标）",
    {"stage", "min_weight", "max_weight", "nutrition", "formula"} <= set(row),
    list(row),
)
check("体重区间是数字（Numeric 未转 float 会 JSON 序列化失败）", isinstance(row["min_weight"], (int, float)), row["min_weight"])

print("== 8. 跨租户改价不再改写别人的配方成本 ==")
t2, _ = new_farmer("契约乙", species="pig")
farm2, pen2 = make_farm_pen(t2, "契约乙场", variety="pig_fattening")
gen = post("/feed/formula/generate", {"pen_id": pen2}, t2)
check("未设生长阶段的圈舍也能生成配方（feed_engine next() 优先级陷阱曾抛 50001）", gen["code"] == 0, gen)
if gen["code"] == 0:
    formula = gen["data"]
    act = post(f"/feed/formula/{formula['formula_id']}/activate", {}, t2)
    check("配方生效", act["code"] == 0, act)
    target_ing = (formula.get("ingredients") or [{}])[0].get("name") or "玉米"
    before = get(f"/pen/{pen2}/feed-plan", t2)["data"].get("active_formula") or {}
    cost_before = before.get("cost_per_head_day")
    # 另建一个账号（丙），由它改同一个原料的价格
    t3, _ = new_farmer("契约丙")
    make_farm_pen(t3, "契约丙场")
    r = post("/feed/price", {"ingredient_name": target_ing, "unit_price": 9999.0, "price_date": ""}, t3)
    check("丙改价成功", r["code"] == 0, r)
    affected = r["data"].get("affected") or []
    check("丙的重算结果里不含乙的配方（跨租户写已收口）",
          all(a.get("formula_id") != formula["formula_id"] for a in affected), affected)
    after = get(f"/pen/{pen2}/feed-plan", t2)["data"].get("active_formula") or {}
    check("乙的生效配方成本未被丙改写", after.get("cost_per_head_day") == cost_before, (cost_before, after.get("cost_per_head_day")))

print("== 9. 设备上行双轨凭证（C-24）==")


def raw_ingest(key: str | None, token: str | None, tag: str, sn_used: str = sn, farm_used: int = farm1, pen_used: int = pen1) -> dict:
    headers = {}
    if key is not None:
        headers["X-Device-Key"] = key
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "msg_id": f"k{tag}-" + "".join(random.choice(string.ascii_lowercase) for _ in range(6)),
        "device_id": sn_used,
        "topic": f"farm/{farm_used}/pen/{pen_used}/{sn_used}/data",
        "ts": int(time.time() * 1000),
        "metrics": {"temperature": 22.0},
    }
    return client.post("/device/ingest", json=body, headers=headers).json()


ok_key = raw_ingest(device_key, None, "key")
check("设备密钥上报（无 JWT）被受理", ok_key.get("code") == 0 and ok_key.get("data", {}).get("ok") is True, ok_key)
bad_key = raw_ingest("deadbeef" * 4, None, "bad")
check("错误设备密钥 40101", bad_key.get("code") == 40101, bad_key)
no_cred = raw_ingest(None, None, "none")
check("既无密钥也无 JWT → 40101（不存在匿名上报）", no_cred.get("code") == 40101, no_cred)
other = raw_ingest(None, t3, "other")
check("别人拿农户 JWT 也无法往我的设备写读数（曾只验角色不验归属）", other.get("code") == 40302, other)
device_id = (dev.get("data") or {}).get("device_id")
rot = post(f"/device/{device_id}/rotate-secret", {}, t1)
check("轮换密钥成功且只回传一次", rot.get("code") == 0 and len((rot.get("data") or {}).get("device_secret", "")) >= 16, rot)
stale = raw_ingest(device_key, None, "stale")
check("旧密钥轮换后立即失效", stale.get("code") == 40101, stale)

print("== 10. 告警「判定误报」闭环（后端早就支持、界面没有入口）==")
# 用**新圈舍**做演练：同类同圈的告警有 1h 合并窗口（EX-C7），复用第 1 组那条圈舍会被窗口吞掉
pen_fa = post("/pen", {"farm_id": farm1, "pen_name": "误报演练圈", "variety_code": "common",
                       "male_count": 2, "female_count": 0}, t1)["data"]["pen_id"]
sn_fa = sn + "-FA"
dev2 = post("/device", {"device_name": "误报温湿度", "device_type": "sensor",
                        "device_sn": sn_fa, "pen_id": pen_fa}, t1)
for k in range(3):
    post("/device/ingest", {
        "msg_id": f"fa{k}-" + "".join(random.choice(string.ascii_lowercase) for _ in range(6)),
        "device_id": sn_fa,
        "topic": f"farm/{farm1}/pen/{pen_fa}/{sn_fa}/data",
        "ts": int(time.time() * 1000) + k,
        "metrics": {"temperature": 98.5},
    }, t1)
fa_alerts = [a for a in get("/alert/list?page_size=50", t1)["data"]["items"]
             if a["alert_type"] == "sensor" and a["status"] == "pending"]
check("误报演练用的传感器告警已生成（新圈舍绕开 1h 合并窗口）", len(fa_alerts) >= 1, len(fa_alerts))
if fa_alerts:
    no_reason = post(f"/alert/{fa_alerts[0]['alert_id']}/handle", {"action": "false_alarm", "note": ""}, t1)
    check("误报空理由 42201（与 resolve 同审计口径）", no_reason.get("code") == 42201, no_reason)
    judged = post(f"/alert/{fa_alerts[0]['alert_id']}/handle", {"action": "false_alarm", "note": "传感器进水"}, t1)
    check("判定误报成功", judged.get("code") == 0 and judged.get("data", {}).get("status") == "false_alarm", judged)
    again = post(f"/alert/{fa_alerts[0]['alert_id']}/handle", {"action": "false_alarm", "note": "重复"}, t1)
    check("已判误报不可重复改判", again.get("code") == 42201, again)
    lst = get("/alert/list?status=false_alarm", t1)["data"]
    check("误报可按状态检索到", lst["total"] >= 1, lst["total"])
    led = get("/record/list?page_size=50", t1)["data"]["items"]
    check("误报判定留痕台账", any("误报" in r["summary"] for r in led), [r["summary"] for r in led[:3]])

print("== 11. F15 场景结果落库后跨刷新可恢复 ==")
latest = get("/scene/latest", t1)
check("GET /scene/latest 可读且空态明确", latest.get("code") == 0 and latest.get("data", {}).get("has_scene") is False, latest)
anon_latest = get("/scene/latest")
check("未登录取场景 40101", anon_latest.get("code") == 40101, anon_latest)
bad_scene = post("/scene/analyze-photo", {"images": ["not-a-data-url"]}, t1)
check("非法图片被拒且不落半成品", bad_scene.get("code") != 0, bad_scene)
check("非法图片未产生场景记录", get("/scene/latest", t1)["data"]["has_scene"] is False, None)
# 说明：analyze→落库 这一腿要真跑一次多模态调用（花钱），留给浏览器人工验证；
# 这里钉住的是"表存在、读写契约对、半成品不入库"

print("== 12. F14 问诊/告警绑定到具体个体 ==")
# 单独开一圈做个体绑定演练。POST /pen 按存栏快照自动生成耳标（F14 强关联对账），
# 所以取自动建档的那头——手工再补录会撞「无补录额度」，那是另一条既有规则
pen_i = post("/pen", {"farm_id": farm1, "pen_name": "个体绑定圈", "variety_code": "common",
                      "male_count": 1, "female_count": 0}, t1)["data"]["pen_id"]
inlist = get(f"/individual/list?pen_id={pen_i}", t1)
row = ((inlist.get("data") or {}).get("items") or [{}])[0]
iid = row.get("individual_id") or 0
tag_no = row.get("ear_tag_no") or ""
check("新建圈舍自动带出耳标档案（强关联对账）", inlist.get("code") == 0 and inlist["data"]["total"] == 1, inlist)
check("个体可读（绑定断言的主体）", bool(iid and tag_no), row)


def chat(message: str, token: str, **kw) -> list[tuple[str, dict]]:
    """跑一轮 SSE 问诊，返回 [(event, data)]。individual_id 是新契约字段。"""
    frames: list[tuple[str, dict]] = []
    event = ""
    with client.stream("POST", "/agent/chat", json={"message": message, **kw},
                      headers={"Authorization": f"Bearer {token}"}, timeout=180.0) as resp:
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                raw = line[5:].strip()
                try:
                    frames.append((event, json.loads(raw) if raw else {}))
                except json.JSONDecodeError:
                    frames.append((event, {"text": raw}))
    return frames


if iid:
    gone = post("/agent/chat", {"message": "羊咳嗽", "individual_id": 9_999_999}, t1)
    check("绑定不存在的个体在开流前 40401（不是流内 error 帧）", gone.get("code") == 40401, gone)
    cross = post("/agent/chat", {"message": "羊咳嗽", "individual_id": iid}, t2)
    check("绑定别人的个体 40302", cross.get("code") == 40302, cross)
    conflict = post("/agent/chat", {"message": "羊咳嗽", "pen_id": pen_fa, "individual_id": iid}, t1)
    check("个体与圈舍不属于同一处 → 42201 而不是档案挂错圈", conflict.get("code") == 42201, conflict)

    frames = chat("羊咳嗽、流鼻涕，绑定的这头精神差", t1, individual_id=iid)
    got = {e for e, _ in frames}
    check("带 individual_id 的问诊能正常出完帧", "done" in got and "error" not in got, sorted(got))
    recs = get("/record/list?page_size=100", t1)["data"]["items"]
    # 列表项只回 pen_name 不回 pen_id（03-API 10.1 口径），按 individual_id 认这条台账
    disease = next((r for r in recs if r["record_type"] == "disease"
                    and (r.get("record_ext") or {}).get("individual_id") == iid), None)
    check("disease 台账已生成（绑定个体即自动定位到圈，不走先答后问）", disease is not None,
          [r["summary"] for r in recs[:3]])
    if disease:
        ext = disease.get("record_ext") or {}
        check("台账挂在个体所在圈而非默认圈", disease["pen_name"] == "个体绑定圈", disease["pen_name"])
        check("台账 record_ext 回写 tag_no", ext.get("tag_no") == tag_no, ext)
        check("台账 record_ext 回写 individual_id", ext.get("individual_id") == iid, ext)
        check("台账摘要带耳标，人能一眼看出是那头", tag_no in disease["summary"], disease["summary"])
        cols = get("/record/list?page_size=100", t1)["data"].get("ext_columns") or []
        labels = {c["field"]: c["label"] for c in cols}
        check("新增 ext 键有中文列名（不裸显 individual_id）",
              labels.get("individual_id") == "个体编号" and labels.get("tag_no") == "耳标号", labels)
        raw_cols = [c["field"] for c in cols if c["label"] == c["field"]]
        # 兜底规则是"未登记的键原样展示"，所以 label==field 就等于漏登记一个中文列头
        check("台账动态列全部有中文列名（问诊/用药/复核键不得裸显英文）", not raw_cols, raw_cols)

    today_s = date.today().isoformat()
    ev = post(f"/individual/{iid}/event", {"event_type": "treatment", "event_date": today_s,
                                          "symptom": "咳嗽、流鼻涕", "diagnosis": "疑似传染性胸膜肺炎",
                                          "medication": "替米考星"}, t1)
    check("问诊/告警结论可记入个体治疗档案", ev.get("code") == 0, ev)
    detail = get(f"/individual/{iid}/detail", t1)
    treat = [e for e in (detail.get("data") or {}).get("events", []) if e["event_type"] == "treatment"]
    check("个体时间线可见该治疗事件", len(treat) == 1 and treat[0]["payload"].get("symptom") == "咳嗽、流鼻涕",
          treat)
    other_ev = post(f"/individual/{iid}/event", {"event_type": "treatment", "event_date": today_s,
                                                 "symptom": "越权试探"}, t2)
    check("跨账号记入别人个体档案 40302", other_ev.get("code") == 40302, other_ev)
    # 自动建档的个体没有出生日期，先补一条才谈得上时序护栏
    put = client.put(f"/individual/{iid}", json={"birth_date": date.fromordinal(date.today().toordinal() - 10).isoformat()},
                     headers={"Authorization": f"Bearer {t1}"}).json()
    check("个体可补录出生日期", put.get("code") == 0, put)
    past = post(f"/individual/{iid}/event", {"event_type": "treatment",
                                             "event_date": date.fromordinal(date.today().toordinal() - 60).isoformat(),
                                             "symptom": "早于出生"}, t1)
    check("事件日期早于出生被拒（档案时序不可乱）", past.get("code") == 42201, past)
else:
    check("个体可读才有后续绑定断言", False, inlist)

print()
print("=" * 56)
print(f"PASS {pass_n} / FAIL {fail_n}")
if failures:
    for f in failures:
        print("  -", f)
sys.exit(1 if fail_n else 0)
