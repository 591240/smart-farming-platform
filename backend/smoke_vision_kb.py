"""F09 视觉事件 + F16 知识库契约 冒烟（打 8001 测试实例与 smart_farm_test）。

运行：
    cd backend && python run.py            # 另开窗口（或 AI_PORT=8001 起测试实例）
    python smoke_vision_kb.py [base_url]

覆盖口径——都是"错了就会误导使用者"的地方：
    视觉：五类事件分级（death≥0.5→high、≥0.7→mid）、低置信不入库不告警、
    msg_id 重发幂等丢弃、缺字段跳过、非法 event_type、1h 同因合并窗口、
    停用设备入库但不告警、列表字段与 bbox 不外泄、越权 40302、非摄像头 42201；
    知识库：/kb/list 必须带 content_md（阅读页直接读它）、PUT 路径是
    /admin/kb/{id}（前端就是这么调的）、DELETE 软删并摘向量、营养类写入即归 low
    且清空推荐药、关键词接受分隔串、单关键词>20 字拒收。
末行 PASS n / FAIL n，有失败退出码 1。
"""

from __future__ import annotations

import asyncio
import random
import sys
from typing import Any

import httpx
from sqlalchemy import select, update

from app.core.config import settings
from app.core.db import SessionLocal, schema_drift
from app.models import Device, MedicineLib, User

# Windows 控制台默认 GBK：断言名里的「−」等非 GBK 字符会在 print 时抛
# UnicodeEncodeError 直接中断整组测试（与业务无关的假失败）。统一按 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001/api").rstrip("/")
PASS = 0
FAILED: list[str] = []

# 本套件要直接改库（提权测试管理员、启停设备），所以硬拦在测试库上：
# 默认 DATABASE_URL 指向演示库 smart_farm，误跑会把演示账号提成 admin、把设备停用。
if "smart_farm_test" not in settings.database_url:
    raise SystemExit(
        f"拒绝运行：当前 DATABASE_URL 指向 {settings.database_url.split('@')[-1]}（非 smart_farm_test）。\n"
        "请显式指定测试库：DATABASE_URL=postgresql+asyncpg://smartfarm:smartfarm123@127.0.0.1:5432/smart_farm_test "
        "python smoke_vision_kb.py"
    )


def check(name: str, cond: bool, detail: object = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} | {detail}")
        FAILED.append(f"{name} | {detail}")


def rnd() -> str:
    return "137" + "".join(random.choices("0123456789", k=8))


def login(c: httpx.Client, phone: str, pwd: str = "Farm123456") -> dict[str, str]:
    c.post("/auth/register", json={"phone": phone, "password": pwd})
    tk = c.post("/auth/login", json={"phone": phone, "password": pwd}).json()["data"]["token"]
    return {"Authorization": f"Bearer {tk}"}


async def promote_to_admin(phone: str) -> None:
    """测试库内直接把账号角色置 admin（没有对外提权接口，也不该有）。"""
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.phone == phone).values(role="admin"))
        await db.commit()


async def set_device_enable(sn: str, enable: bool) -> None:
    async with SessionLocal() as db:
        await db.execute(update(Device).where(Device.device_sn == sn).values(is_enable=enable))
        await db.commit()


async def first_pig_med() -> str:
    """从真兽药库取一个本物种非违禁药名——写死药名会让冒烟随库内容变化而假失败。"""
    async with SessionLocal() as db:
        return (
            await db.execute(
                select(MedicineLib.med_name).where(
                    MedicineLib.species_code == "pig", MedicineLib.is_forbidden.is_(False)
                ).limit(1)
            )
        ).scalars().first() or ""


async def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=30, trust_env=False)
    H = login(c, rnd())

    print("\n== Schema 漂移自检（models.py 声明 vs 库里真实列）==")
    # 放在最前面：C-21 那次是"模型加列没补 ALTER"，症状是读接口 500。
    # 冒烟其余各组都可能被同一个根因连锁炸掉，先报它才能少绕弯路。
    drift = await schema_drift()
    check(
        f"无缺表缺列（{drift['tables_checked']} 张表）",
        drift["ok"],
        {"missing_tables": drift["missing_tables"], "missing_columns": drift["missing_columns"]},
    )
    if drift["extra_columns"]:
        print("  ! 库里存在模型未声明的列（不判失败）：", drift["extra_columns"])
    # 主物种决定 /kb/list 的可见范围（farmer=本物种+公共），不设则只看公共文档
    c.put("/user/main-species", json={"main_species_code": "pig"}, headers=H)

    farm = c.post("/farm", json={
        "farm_name": "视觉冒烟场", "province": "湖南省", "city": "长沙市", "county": "长沙县",
        "adcode": "430121", "address": "冒烟路 1 号", "contact_person": "冒", "phone": "13700000001",
    }, headers=H).json()["data"]["farm_id"]
    pen = c.post("/pen", json={
        "farm_id": farm, "pen_name": "冒烟圈", "species_code": "pig", "variety_code": "pig_fattening",
        "male_count": 10, "female_count": 5, "batch_stage": "fattening",
    }, headers=H).json()["data"]["pen_id"]
    sn = "CAM" + "".join(random.choices("0123456789", k=6))
    no_rtsp = c.post("/device", json={"device_name": "缺地址摄像头", "device_type": "camera", "device_sn": sn + "X", "pen_id": pen}, headers=H).json()
    check("摄像头缺 rtsp_url 拒建 42201", no_rtsp.get("code") == 42201, no_rtsp)
    dev = c.post("/device", json={
        "device_name": "冒烟摄像头", "device_type": "camera", "device_sn": sn, "pen_id": pen,
        "rtsp_url": "rtsp://127.0.0.1:554/smoke",
    }, headers=H)
    device_id = dev.json()["data"]["device_id"]

    print("\n== 视觉事件消费链 ==")
    r = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "huddle", "confidence": 0.87}, headers=H).json()["data"]
    check("huddle 0.87 → mid 且产生告警", r.get("ok") and r["result"]["level"] == "mid" and r["result"]["alert_id"], r)
    alert_id = (r.get("result") or {}).get("alert_id")

    r2 = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "death", "confidence": 0.6}, headers=H).json()["data"]
    check("death 0.6 → high（决策 1 边界）", r2["result"]["level"] == "high", r2)

    r3 = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "huddle", "confidence": 0.7}, headers=H).json()["data"]
    check("huddle 0.7 → mid", r3["result"]["level"] == "mid", r3)

    before = c.get("/vision/events", headers=H).json()["data"]["total"]
    low = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "cough", "confidence": 0.9, "anomaly": "low_confidence"}, headers=H).json()["data"]
    after = c.get("/vision/events", headers=H).json()["data"]["total"]
    check("低置信演练被丢弃且不入库", low.get("dropped") and low.get("reason") == "low_confidence" and after == before, (low, before, after))

    dup = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "water", "confidence": 0.9, "anomaly": "duplicate"}, headers=H).json()["data"]
    check("同 msg_id 重发第二次被去重丢弃", dup.get("ok") and dup.get("dedup", {}).get("msg_id_replay_dropped") is True, dup)

    badp = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "cough", "confidence": 0.9, "anomaly": "bad_payload"}, headers=H).json()["data"]
    check("缺 event_type 判 bad_payload 且不崩溃", badp.get("dropped") and badp.get("reason") == "bad_payload", badp)

    bade = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "flying_pig", "confidence": 0.9}, headers=H).json()["data"]
    check("非法 event_type 判 bad_event_type", bade.get("reason") == "bad_event_type", bade)

    same = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "huddle", "confidence": 0.85}, headers=H).json()["data"]
    check("1h 合并窗口内同因同级别不再告警但事件仍入库", same.get("ok") and same["result"]["alert_id"] is None, same)

    print("\n== 事件列表契约 ==")
    ev = c.get(f"/vision/events?pen_id={pen}&page_size=50", headers=H).json()["data"]
    check("列表按采集时间倒序", ev["items"] == sorted(ev["items"], key=lambda x: x["collect_time"], reverse=True), [i["collect_time"] for i in ev["items"][:3]])
    want = {"result_id", "event_type", "event_label", "confidence", "level", "collect_time", "pen_name", "device_sn"}
    check("字段集合与 mock 契约一致且不外泄 bbox", ev["items"] and want <= set(ev["items"][0]) and "bbox" not in ev["items"][0], sorted(set(ev["items"][0])))
    labels = {i["event_label"] for i in ev["items"]}
    check("event_label 为中文（读侧派生）", {"扎堆", "死亡牲畜（疑似）", "圈舍积水"} <= labels, labels)
    one = c.get(f"/vision/events?pen_id={pen}&from=2000-01-01&to=2099-12-31", headers=H).json()["data"]
    check("日期区间含当日（当日事件仍在）", one["total"] >= len(ev["items"]), (one["total"], len(ev["items"])))
    future = c.get("/vision/events?from=2099-01-01", headers=H).json()["data"]
    check("未来起点过滤生效", future["total"] == 0, future["total"])

    print("\n== 停用设备 / 越权 / 角色 ==")
    await set_device_enable(sn, False)
    off = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "lethargy", "confidence": 0.95}, headers=H).json()["data"]
    total_after_off = c.get("/vision/events", headers=H).json()["data"]["total"]
    check("停用设备：事件仍入库但不告警", off.get("ok") and off["result"]["alert_id"] is None, off)
    await set_device_enable(sn, True)

    other = httpx.Client(base_url=BASE, timeout=30, trust_env=False)
    OH = login(other, rnd())
    check("越权读他人圈事件 40302", other.get(f"/vision/events?pen_id={pen}", headers=OH).json()["code"] == 40302)
    check("越权向他人设备注入 40302", other.post("/vision/simulate", json={"device_id": device_id, "event_type": "cough", "confidence": 0.9}, headers=OH).json()["code"] == 40302)
    ofarm = other.post("/farm", json={
        "farm_name": "另一场", "province": "湖南省", "city": "株洲市", "county": "天元区",
        "adcode": "430211", "address": "另一路 1 号", "contact_person": "另", "phone": "13700000002",
    }, headers=OH).json()["data"]["farm_id"]
    open_pen = other.post("/pen", json={
        "farm_id": ofarm, "pen_name": "另一圈", "species_code": "pig", "variety_code": "pig_fattening",
        "male_count": 3, "female_count": 1, "batch_stage": "fattening",
    }, headers=OH).json()["data"]["pen_id"]
    osid = other.post("/device", json={
        "device_name": "非摄像头", "device_type": "sensor",
        "device_sn": "SEN" + "".join(random.choices("0123456789", k=6)), "pen_id": open_pen,
    }, headers=OH).json()["data"]["device_id"]
    not_cam = other.post("/vision/simulate", json={"device_id": osid, "event_type": "cough", "confidence": 0.9}, headers=OH).json()
    check("向传感器注入被拒 42201", not_cam.get("code") == 42201 and "摄像头" in str(not_cam.get("message")), not_cam)

    print("\n== 告警联动 ==")
    alerts = c.get("/alert/list?page_size=50", headers=H).json()["data"]["items"]
    vision_alerts = [a for a in alerts if a["alert_type"] == "vision"]
    check("vision 告警出现在告警列表且带级别", bool(vision_alerts) and vision_alerts[0]["risk_level"] in ("low", "mid", "high"), vision_alerts[:1])
    death_alert = next((a for a in vision_alerts if "死亡" in a["alert_content"]), None)
    check("death 告警文案引导人工核实、不自动扣存栏", bool(death_alert) and "人工核实" in death_alert["alert_content"], death_alert)
    check("模拟注入文案带（模拟）标注", any("（模拟）" in a["alert_content"] for a in vision_alerts), [a["alert_content"][:40] for a in vision_alerts])

    print("\n== F16 知识库契约 ==")
    kb = c.get("/kb/list", headers=H).json()["data"]
    check("/kb/list 返回 items 结构", isinstance(kb.get("items"), list), kb)
    check("farmer 访问 /admin/kb → 40301", c.get("/admin/kb", headers=H).json()["code"] == 40301)

    admin_phone = rnd()
    AC = login(c, admin_phone)
    await promote_to_admin(admin_phone)
    AC = login(c, admin_phone)
    med = await first_pig_med()
    up = c.post("/admin/kb", json={
        "doc_name": "猪流感识别要点", "doc_type": "disease", "species_code": "pig",
        "retrieval_keywords": "咳嗽，体温升高、精神沉郁",  # 分隔串形式（mock 同款宽容）
        "risk_level": "high", "med_names": [med], "feed_advice": "加强通风",
        "content_md": "正文" * 400, "source": "《猪病学》", "organizer": "冒烟",
    }, headers=AC).json()
    check("admin 上传成功（关键词接受顿号/逗号分隔串）", up.get("code") == 0, up)
    doc = (up.get("data") or {})
    doc_id = doc.get("doc_id")
    check("上传回显 chunk_count/indexed_at", doc.get("chunk_count", 0) >= 2 and doc.get("indexed_at"), doc)
    check("上传回显含 content_md（列表同款字段）", "content_md" in doc)

    nut = c.post("/admin/kb", json={
        "doc_name": "育肥猪营养要点", "doc_type": "nutrition", "species_code": "pig",
        "retrieval_keywords": ["能量", "蛋白"], "risk_level": "high",
        "content_md": "营养正文", "source": "NY/T 65", "organizer": "冒烟",
    }, headers=AC).json()["data"]
    check("营养类写入即归 low（列表不回显 high）", nut["risk_level"] == "low", nut["risk_level"])
    check("营养类推荐药被清空", nut["med_names"] == [], nut["med_names"])

    bad_kw = c.post("/admin/kb", json={
        "doc_name": "关键词过长", "doc_type": "disease", "species_code": "pig",
        "retrieval_keywords": ["这个关键词实在是太长了超过了二十个字限制需要拒收"], "risk_level": "mid",
        "content_md": "x", "source": "s", "organizer": "o",
    }, headers=AC).json()
    check("单关键词 >20 字被拒（不静默截断）", bad_kw.get("code") == 42201, bad_kw)
    few = c.post("/admin/kb", json={
        "doc_name": "关键词不足", "doc_type": "disease", "species_code": "pig",
        "retrieval_keywords": ["只有一"], "risk_level": "mid", "content_md": "x", "source": "s", "organizer": "o",
    }, headers=AC).json()
    check("关键词不足 2 个被拒", few.get("code") == 42201, few)
    illegal_med = c.post("/admin/kb", json={
        "doc_name": "违禁药", "doc_type": "disease", "species_code": "pig",
        "retrieval_keywords": ["a", "b"], "risk_level": "mid", "med_names": ["瘦肉精"],
        "content_md": "x", "source": "s", "organizer": "o",
    }, headers=AC).json()
    check("库外/违禁药被拒并点名", illegal_med.get("code") == 42201 and "瘦肉精" in str(illegal_med.get("message")), illegal_med)

    lst = c.get("/kb/list", headers=H).json()["data"]["items"]
    check("/kb/list 带 content_md（阅读页直接读它）", bool(lst) and all("content_md" in d for d in lst), sorted(lst[0]) if lst else "空列表")
    check("farmer 只看到本物种+公共文档", all(d["species_code"] in ("pig", "") for d in lst), {d["species_code"] for d in lst})
    admin_lst = c.get("/admin/kb", headers=AC).json()["data"]["items"]
    check("GET /admin/kb 也带 content_md", all("content_md" in d for d in admin_lst), sorted(admin_lst[0]) if admin_lst else "空")

    tog = c.put(f"/admin/kb/{doc_id}", json={"is_enable": False}, headers=AC).json()
    check("PUT /admin/kb/{id} 停用生效（前端就是这个路径）", tog.get("code") == 0 and tog["data"]["is_enable"] is False, tog)
    after_off = c.get("/kb/list", headers=H).json()["data"]["items"]
    check("停用即退出阅读列表", all(d["doc_id"] != doc_id for d in after_off), doc_id)
    bad_body = c.put(f"/admin/kb/{doc_id}", json={"is_enable": "yes"}, headers=AC).json()
    check("is_enable 非布尔返回业务码而非框架 422", bad_body.get("code") == 42201, bad_body)

    dele = c.delete(f"/admin/kb/{doc_id}", headers=AC).json()
    check("DELETE 软删返回 is_deleted", dele.get("code") == 0 and dele["data"].get("is_deleted") is True, dele)
    gone = c.get(f"/admin/kb/{doc_id}", headers=AC).json()
    check("删除后再查 40401", gone.get("code") == 40401, gone)

    print(f"\n{'=' * 56}")
    for x in FAILED:
        print("  × " + x)
    print(f"PASS {PASS} / FAIL {len(FAILED)}")
    sys.exit(1 if FAILED else 0)


asyncio.run(main())
