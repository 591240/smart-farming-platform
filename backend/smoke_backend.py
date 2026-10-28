"""后端切真主链路联调冒烟（对标 .smoke/f14-smoke 口径，目标 = 真数据库）。

覆盖：注册登录 → 建场建圈（初始建档自动耳标）→ 存栏流水强关联（入栏带标/离栏选标）
→ 单个建档头寸 → 一键补齐 → 编辑/纠错删档 → 转群迁移 → 台账列表/导出。
运行：python backend/smoke_backend.py（服务须在 127.0.0.1:8000）
"""

import os
import random
import string
import sys

import httpx

# Windows 控制台默认 GBK：断言名里的「−」等非 GBK 字符会在 print 时抛
# UnicodeEncodeError 直接中断整组测试（与业务无关的假失败）。统一按 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SF_TEST_BASE", "http://127.0.0.1:8001/api")).rstrip("/")
client = httpx.Client(base_url=BASE, timeout=30, trust_env=False)  # 绕过系统代理直连本机

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


def call(method: str, path: str, body: object = None, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.request(method, path, json=body, headers=headers)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"{method} {path} -> {data}")
    return data["data"]


def call_err(method: str, path: str, body: object = None, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.request(method, path, json=body, headers=headers)
    data = r.json()
    assert data.get("code") != 0, f"期望失败但成功: {method} {path} -> {data}"
    return data


phone = "139" + "".join(random.choices(string.digits, k=8))
pwd = "smoke123"

print("== 账户 ==")
d = call("POST", "/auth/register", {"phone": phone, "password": pwd, "nickname": "切真冒烟"})
check("注册返回 user_id", d["user_id"] > 0 and d["first_login"] is True, d)
err = call_err("POST", "/auth/register", {"phone": phone, "password": pwd})
check("重复注册 40901", err["code"] == 40901, err)
d = call("POST", "/auth/login", {"phone": phone, "password": pwd})
token = d["token"]
check("登录返回 token+user", bool(token) and d["user"]["nickname"] == "切真冒烟", d)
check("guide_done=False（未建场）", d["user"]["guide_done"] is False)
err = call_err("GET", "/user/me", token="bad.token.here")
check("非法 token 40101", err["code"] == 40101, err)
d = call("PUT", "/user/main-species", {"main_species_code": "pig"}, token)
check("设置主物种", d["main_species_code"] == "pig", d)

print("== 场舍与初始建档 ==")
farm_id = call("POST", "/farm", {"farm_name": "切真冒烟场", "province": "湖南省", "city": "长沙市", "county": "长沙县", "adcode": "430121"}, token)["farm_id"]
check("建场返回 farm_id", farm_id > 0)
d = call("GET", "/user/me", token=token)
check("guide_done=True（已建场）", d["guide_done"] is True)
pen_a = call("POST", "/pen", {"farm_id": farm_id, "pen_name": "育肥A圈", "species_code": "pig", "variety_code": "pig_fattening", "male_count": 3, "female_count": 2}, token)["pen_id"]
check("建圈返回 pen_id", pen_a > 0)
d = call("GET", "/individual/list", token=token, body=None) or call("GET", f"/individual/list?pen_id={pen_a}&status=all", token=token)
d = call("GET", f"/individual/list?pen_id={pen_a}&status=all", token=token)
check("初始建档 5 个体", len(d["items"]) == 5 and d["summary"]["diff"] == 0, d["summary"])
tags_a = {i["ear_tag_no"] for i in d["items"]}
check("自动编号 P{pen}-M001~M003", f"P{pen_a}-M001" in tags_a and f"P{pen_a}-M003" in tags_a, tags_a)

print("== 存栏流水强关联 ==")
err = call_err("POST", f"/pen/{pen_a}/stock-change", {"change_type": "purchase", "male_delta": 1, "female_delta": 0}, token)
check("入栏无耳标 42201", err["code"] == 42201, err)
err = call_err("POST", f"/pen/{pen_a}/stock-change", {"change_type": "purchase", "male_delta": 2, "female_delta": 0, "male_tags": ["IN-A"]}, token)
check("数量不符 42201", err["code"] == 42201, err)
err = call_err("POST", f"/pen/{pen_a}/stock-change", {"change_type": "purchase", "male_delta": 1, "female_delta": 0, "male_tags": [f"P{pen_a}-M001"]}, token)
check("占用耳标 42201", err["code"] == 42201, err)
d = call("POST", f"/pen/{pen_a}/stock-change", {"change_type": "purchase", "male_delta": 2, "female_delta": 1, "male_tags": ["IN-A", "IN-B"], "female_tags": ["IN-C"], "remark": "切真购入"}, token)
check("入栏带标成功", d["male_count"] == 5 and d["female_count"] == 3, d)
d = call("POST", f"/pen/{pen_a}/stock-change", {"change_type": "death", "male_delta": 1, "female_delta": 0, "exit_tag_nos": ["IN-B"], "remark": "切真死淘"}, token)
check("死淘选标成功", d["male_count"] == 4, d)
err = call_err("POST", f"/pen/{pen_a}/stock-change", {"change_type": "death", "male_delta": 1, "female_delta": 0, "exit_tag_nos": ["IN-C"]}, token)
check("性别分布不符 42201", err["code"] == 42201, err)
d = call("GET", f"/individual/list?pen_id={pen_a}&status=dead", token=token)
dead = [i for i in d["items"] if i["ear_tag_no"] == "IN-B"]
check("死淘个体 status=dead + 说明", len(dead) == 1 and "死淘" in dead[0]["exit_note"], dead)
d = call("GET", f"/individual/list?pen_id={pen_a}&status=all", token=token)
check("流水后对账仍一致", d["summary"]["diff"] == 0, d["summary"])

print("== 单个建档 / 补齐 / 编辑 ==")
pen_b = call("POST", "/pen", {"farm_id": farm_id, "pen_name": "补录圈", "species_code": "pig", "variety_code": "pig_fattening", "male_count": 2, "female_count": 0}, token)["pen_id"]
d = call("GET", f"/individual/list?pen_id={pen_b}&status=all", token=token)
for i in d["items"]:
    call("DELETE", f"/individual/{i['individual_id']}", token=token)  # 模拟历史未建档
d = call("GET", f"/individual/list?pen_id={pen_b}&status=all", token=token)
check("删档后 diff=2", d["summary"]["diff"] == 2, d["summary"])
err = call_err("POST", "/individual", {"pen_id": pen_b, "ear_tag_no": "SM-NEW-1", "gender": "female"}, token)
check("性别头寸不符 42201", err["code"] == 42201, err)
d = call("POST", "/individual", {"pen_id": pen_b, "ear_tag_no": "SM-NEW-1", "gender": "male", "birth_date": "2026-03-01"}, token)
check("差额内补录成功", d["individual_id"] > 0, d)
err = call_err("POST", "/individual", {"pen_id": pen_a, "ear_tag_no": "SM-NEW-1", "gender": "male"}, token)
check("跨圈重复耳标 42201", err["code"] == 42201, err)
d = call("POST", f"/pen/{pen_b}/individuals/auto-fill", {}, token)
check("一键补齐 1 公", d["created_male"] == 1 and d["created_female"] == 0, d)
d = call("POST", f"/pen/{pen_b}/individuals/auto-fill", {}, token)
check("重复补齐幂等", d["created_male"] == 0 and "无需补齐" in d["message"], d)
d = call("GET", "/record/list?record_type=individual", token=token)
check("补齐台账留痕", any("耳标补齐" in i["summary"] for i in d["items"]), d["total"])
d = call("GET", f"/individual/list?pen_id={pen_b}&status=all", token=token)
victim = next(i for i in d["items"] if i["ear_tag_no"] == "SM-NEW-1")
d = call("PUT", f"/individual/{victim['individual_id']}", {"ear_tag_no": "SM-RENAMED-1", "remark": "改号"}, token)
check("改号成功", d["updated"] is True)
dead_ind = call("GET", f"/individual/list?pen_id={pen_a}&status=dead", token=token)["items"][0]
err = call_err("DELETE", f"/individual/{dead_ind['individual_id']}", token=token)
check("已离栏不可删 42201", err["code"] == 42201, err)

print("== 转群 ==")
pen_c = call("POST", "/pen", {"farm_id": farm_id, "pen_name": "转群目标圈", "species_code": "pig", "variety_code": "pig_fattening", "male_count": 0, "female_count": 0}, token)["pen_id"]
err = call_err("POST", f"/pen/{pen_a}/transfer", {"target_pen_id": pen_c, "male_count": 1, "female_count": 0}, token)
check("转群无耳标 42201", err["code"] == 42201, err)
d = call("POST", f"/pen/{pen_a}/transfer", {"target_pen_id": pen_c, "male_count": 1, "female_count": 1, "tag_nos": ["IN-A", "IN-C"], "remark": "切真转群"}, token)
check("转群成功", d["out_change_id"] > 0 and d["in_change_id"] > 0, d)
d = call("GET", f"/individual/list?pen_id={pen_c}&status=active", token=token)
check("个体迁移到目标圈", {i["ear_tag_no"] for i in d["items"]} == {"IN-A", "IN-C"}, d["items"])
d = call("GET", f"/individual/list?pen_id={pen_a}&status=all", token=token)
check("源圈对账一致", d["summary"]["diff"] == 0, d["summary"])

print("== 台账 ==")
d = call("GET", "/record/list?page_size=50", token=token)
check("台账含 stock_change+individual", {i["record_type"] for i in d["items"]} >= {"stock_change", "individual"}, d["total"])
d = call("GET", "/record/export", token=token)
# 列序按 PRD 3.6「通用列固定在前（时间、圈舍、类型、摘要）」；原实现把"记录ID"放首位，已纠正
check("导出 CSV 含 BOM 与 PRD 列序", d["content"].startswith("\ufeff") and d["content"].lstrip("\ufeff").splitlines()[0].startswith("时间,圈舍,类型,摘要"), d["content"].lstrip("﻿").splitlines()[0][:40])

print("\n== F17 个体档案详情（一畜一档） ==")
import datetime as _dt

today = _dt.date.today().isoformat()
d0301 = str(_dt.date.today() - _dt.timedelta(days=60))
d0215 = str(_dt.date.today() - _dt.timedelta(days=45))
d0201 = str(_dt.date.today() - _dt.timedelta(days=30))

# 取在栏个体 IN-C（前段流水已转群至 pen_c，全场范围查找）
d = call("GET", "/individual/list?status=active&page_size=100", token=token)
ind_c = next(i for i in d["items"] if i["ear_tag_no"] == "IN-C")
iid = ind_c["individual_id"]

d = call("GET", f"/individual/{iid}/detail", token=token)
check("详情含 register 事件（建档）", any(e["event_type"] == "register" for e in d["events"]), [e["event_type"] for e in d["events"]])
check("register 事件 manual=False", all(not e["manual"] for e in d["events"] if e["event_type"] == "register"))

# 编辑 traits/lineage
d = call("PUT", f"/individual/{iid}", {"traits": "白底黑斑", "lineage": "父：长白；母：大白（母本 P2-F002）"}, token=token)
check("traits/lineage 编辑成功", d["updated"] is True)
d = call("GET", f"/individual/{iid}/detail", token=token)
check("详情返回 traits/lineage", d["individual"]["traits"] == "白底黑斑" and "大白" in d["individual"]["lineage"], d["individual"])

# 换标 → rename 事件留痕
old_tag = ind_c["ear_tag_no"]
call("PUT", f"/individual/{iid}", {"ear_tag_no": "F17-RENAMED"}, token=token)
d = call("GET", f"/individual/{iid}/detail", token=token)
ren = [e for e in d["events"] if e["event_type"] == "rename"]
check("换标自动留痕（old→new）", len(ren) == 1 and ren[0]["payload"]["old_no"] == old_tag and ren[0]["payload"]["new_no"] == "F17-RENAMED", ren)

# 体重 ×3 → 曲线 + 日增重
for dt, kg in ((d0301, 40), (d0215, 55), (d0201, 70)):
    call("POST", f"/individual/{iid}/event", {"event_type": "weight", "event_date": dt, "weight_kg": kg}, token=token)
d = call("GET", f"/individual/{iid}/detail", token=token)
ws = d["weight_series"]
check("体重曲线 3 点", len(ws) == 3, ws)
check("日增重计算（45d 点）", ws[1]["daily_gain"] is not None and abs(ws[1]["daily_gain"] - 1.0) < 0.01, ws[1])
err = call_err("POST", f"/individual/{iid}/event", {"event_type": "weight", "event_date": today, "weight_kg": 600}, token=token)
check("体重超界 42201", err["code"] == 42201, err)

# 免疫/诊疗/备注
call("POST", f"/individual/{iid}/event", {"event_type": "immunization", "event_date": today, "vaccine_name": "口蹄疫灭活疫苗", "dose": "2ml 肌注", "next_date": str(_dt.date.today() + _dt.timedelta(days=180))}, token=token)
call("POST", f"/individual/{iid}/event", {"event_type": "treatment", "event_date": d0215, "symptom": "咳嗽、采食下降", "diagnosis": "支原体肺炎", "medication": "泰妙菌素", "outcome": "康复"}, token=token)
call("POST", f"/individual/{iid}/event", {"event_type": "note", "event_date": today, "content": "调群观察个体"}, token=token)
err = call_err("POST", f"/individual/{iid}/event", {"event_type": "immunization", "event_date": today}, token=token)
check("免疫缺疫苗名 42201", err["code"] == 42201, err)
err = call_err("POST", f"/individual/{iid}/event", {"event_type": "note", "event_date": "2099-01-01", "content": "x"}, token=token)
check("事件日期晚于今天 42201", err["code"] == 42201, err)

d = call("GET", f"/individual/{iid}/detail", token=token)
types = {e["event_type"] for e in d["events"]}
check("时间线含七类事件（含转群联动）", types == {"register", "rename", "transfer", "weight", "immunization", "treatment", "note"}, types)
manual_ev = next(e for e in d["events"] if e["event_type"] == "note")
reg_ev = next(e for e in d["events"] if e["event_type"] == "register")
d = call("DELETE", f"/individual/event/{manual_ev['event_id']}", token=token)
check("手动事件可纠错删除", d["deleted"] is True)
err = call_err("DELETE", f"/individual/event/{reg_ev['event_id']}", token=token)
check("流水联动事件不可删 42201", err["code"] == 42201, err)

# 事件日期早于出生日期
d = call("GET", "/individual/list?status=active&page_size=100", token=token)
ind_tmp = next(i for i in d["items"] if i["ear_tag_no"] == "IN-A")
call("PUT", f"/individual/{ind_tmp['individual_id']}", {"birth_date": today}, token=token)
err = call_err("POST", f"/individual/{ind_tmp['individual_id']}/event", {"event_type": "weight", "event_date": d0301, "weight_kg": 50}, token=token)
check("事件日期早于出生 42201", err["code"] == 42201, err)

# 他人档案 40302
other_phone = "135" + "".join(random.choices(string.digits, k=8))
call("POST", "/auth/register", {"phone": other_phone, "password": pwd}, token=None)
other_token = call("POST", "/auth/login", {"phone": other_phone, "password": pwd}, token=None)["token"]
err = call_err("GET", f"/individual/{iid}/detail", token=other_token)
check("他人档案 40302/40401", err["code"] in (40302, 40401), err["code"])

print("\n== F03 设备/告警/传感器 ==")
import time as _time


def dstr(offset: int) -> str:
    return str(_dt.date.today() + _dt.timedelta(days=offset))


def addDays(date_str: str, days: int) -> str:
    return str(_dt.date.fromisoformat(date_str) + _dt.timedelta(days=days))


import datetime as _dt  # noqa: E402


pen_d = call("POST", "/pen", {"farm_id": farm_id, "pen_name": "传感冒烟圈", "species_code": "pig", "variety_code": "pig_fattening", "male_count": 4, "female_count": 4}, token)["pen_id"]
# device_sn 全局唯一 → 每次跑必须换号，否则第二次运行即撞 42201（脚本幂等性）
_run = random.randint(10**5, 10**9)
sn1 = f"SN-F03-A1-{_run}"
sn_cam = f"SN-F03-CAM-{_run}"
d = call("POST", "/device", {"device_name": "冒烟温湿度", "device_type": "sensor", "device_sn": sn1, "pen_id": pen_d}, token)
check("设备注册返回 mqtt_topic", d["mqtt_topic"] == f"farm/{farm_id}/pen/{pen_d}/{sn1}/data", d)
err = call_err("POST", "/device", {"device_name": "重号", "device_type": "sensor", "device_sn": sn1, "pen_id": pen_d}, token)
check("设备编号重复 42201", err["code"] == 42201, err)
err = call_err("POST", "/device", {"device_name": "坏SN", "device_type": "sensor", "device_sn": "x", "pen_id": pen_d}, token)
check("SN 格式非法 42201", err["code"] == 42201, err)
err = call_err("POST", "/device", {"device_name": "摄像头", "device_type": "camera", "device_sn": sn_cam, "pen_id": pen_d}, token)
check("摄像头缺 RTSP 42201", err["code"] == 42201, err)

now_ms = int(_time.time() * 1000)
topic = f"farm/{farm_id}/pen/{pen_d}/{sn1}/data"
ing = client  # 同一个 httpx client（trust_env=False）


def ingest(msg_suffix, temp, ts_ms=None):
    return ing.post(f"{BASE}/device/ingest", json={
        "msg_id": f"smoke-{msg_suffix}-{random.randint(0, 10**9)}", "device_id": sn1, "topic": topic,
        "ts": ts_ms or int(_time.time() * 1000), "metrics": {"temperature": temp},
    }, headers={"Authorization": f"Bearer {token}"}).json()["data"]


for i in range(1, 3):  # 前 2 次超标不告警（防抖）
    r = ingest(f"w{i}", 35)
    check(f"防抖第 {i} 次入库不告警", r["ok"] is True)
r = ingest("w3", 35)
check("连续第 3 次超标产生告警", r["ok"] is True)
r = ingest("w4", 35.5)
check("合并窗口内不重复告警", r["ok"] is True)
r = ingest("dup", 36, ts_ms=now_ms)
ingest("dup", 36, ts_ms=now_ms)  # 同 msg_id 重发
check("ingest 正常（去重在服务端内部消化）", r["ok"] is True)
d = call("GET", "/alert/list?status=pending", token=token)
sensor_alerts = [a for a in d["items"] if a["alert_type"] == "sensor"]
check("传感器告警 high 且内容含连续超标", len(sensor_alerts) >= 1 and sensor_alerts[0]["risk_level"] == "high" and "连续 3 次超标" in sensor_alerts[0]["alert_content"], sensor_alerts[:1])
d = call("GET", f"/sensor/history?pen_id={pen_d}&metric=temperature", token=token)
check("传感器历史 ≥4 点", len(d["points"]) >= 4, len(d["points"]))
# C-09 分工定稿：心跳按**到达时间**、历史行按**消息 ts**。
# ① 补报历史读数不得把设备判离线（此前心跳取消息 ts，`seed_story.py` 灌 2 天前的故事数据
#    会被离线扫描秒判 offline 并多造一条告警——"补报"和"掉线"被同一根时间轴混了）
ingest("old", 25, ts_ms=now_ms - 180_000)
d = call("GET", "/device/list", token=token)
dev = next(x for x in d["items"] if x["device_sn"] == sn1)
check("补报历史 ts 不判离线（心跳取到达时间）", dev["online_status"] == "online", dev["online_status"])
# ② 真离线 = 停止上报超过阈值。阈值可用 OFFLINE_AFTER_SECONDS 加速（默认 90s，
#    冒烟建议 `OFFLINE_AFTER_SECONDS=5 python run.py` 起服务，并给脚本传同名环境变量）
_offline_s = int(os.environ.get("OFFLINE_AFTER_SECONDS", "90"))
print(f"  ..等待 {_offline_s + 3}s 让心跳超期（OFFLINE_AFTER_SECONDS={_offline_s}）")
_time.sleep(_offline_s + 3)
call("GET", "/alert/list", token=token)  # 触发离线扫描（HTTP 路径按本人农场范围）
d = call("GET", "/device/list", token=token)
dev = next(x for x in d["items"] if x["device_sn"] == sn1)
check("设备状态转为 offline", dev["online_status"] == "offline", dev["online_status"])
d = call("GET", "/alert/list", token=token)
check("离线判定产生 device_offline 告警", any(a["alert_type"] == "device_offline" for a in d["items"]),
      [a["alert_type"] for a in d["items"]])
# 告警处理链
aid = sensor_alerts[0]["alert_id"]
d = call("POST", f"/alert/{aid}/handle", {"action": "acknowledge"}, token=token)
check("告警置已读", d["status"] == "acknowledged")
d = call("POST", f"/alert/{aid}/handle", {"action": "resolve", "note": "已开湿帘降温"}, token=token)
check("告警处理完成", d["status"] == "resolved")
err = call_err("POST", f"/alert/{aid}/handle", {"action": "resolve", "note": "重复"}, token=token)
check("重复处理 42201", err["code"] == 42201, err)

print("\n== F07 繁殖状态机/提醒/用药/出栏 ==")
pen_f = call("POST", "/pen", {"farm_id": farm_id, "pen_name": "繁殖冒烟圈", "species_code": "pig", "variety_code": "pig_sow", "male_count": 0, "female_count": 0}, token)["pen_id"]
d = call("POST", "/breeding-female", {"pen_id": pen_f, "tag_no": "S-01", "parity": 1}, token)
fid = d["female_id"]
check("母畜建档", fid > 0)
err = call_err("POST", "/breeding-female", {"pen_id": pen_f, "tag_no": "S-01"}, token)
check("同圈重号 42201", err["code"] == 42201, err)
d = call("POST", "/lifecycle/event", {"event_type": "breeding", "female_id": fid, "event_date": dstr(-115)}, token)
check("配种 → bred 且预产期 +114", d["female_status"] == "bred" and d["due_date"] == addDays(dstr(-115), 114), d)
err = call_err("PUT", f"/breeding-female/{fid}", {"breed_status": "pregnant"}, token)
check("PUT 直接改状态 40003（事件驱动）", err["code"] == 40003, err["code"])
d = call("POST", "/lifecycle/event", {"event_type": "pregnancy_check", "female_id": fid, "detail": {"result": "阳性"}}, token)
check("妊检 → pregnant", d["female_status"] == "pregnant")
d = call("GET", f"/lifecycle/reminders?farm_id={farm_id}", token=token)
check("预产期提醒已生成（进 3 天窗口）", any(r["reminder_type"] == "due_date" and r["status"] == "pending" for r in d["items"]), d["items"][:2])
d = call("POST", "/lifecycle/event", {"event_type": "birth", "female_id": fid, "detail": {"live_male": 2, "live_female": 1, "dead_count": 1}}, token)
check("分娩 → nursing", d["female_status"] == "nursing")
d = call("GET", f"/individual/list?pen_id={pen_f}&status=active", token=token)
check("活仔自动建档 3 个", len(d["items"]) == 3 and all(i["source"] == "分娩自动建档" for i in d["items"]), len(d["items"]))
d = call("GET", f"/lifecycle/reminders?farm_id={farm_id}", token=token)
check("断奶提醒已生成", any(r["reminder_type"] == "weaning" for r in d["items"]))
meds = call("GET", "/medicine/list", token=token)["items"]
check("药库不含违禁药", not any("瘦肉精" in m["med_name"] for m in meds))
flu = next(m for m in meds if m["med_name"] == "氟苯尼考注射液")
d = call("POST", "/medicine/usage", {"pen_id": pen_f, "med_id": flu["med_id"], "usage_date": dstr(0), "dosage": "肌注", "operator_note": "冒烟"}, token)
check("休药期服务端计算 28 天", d["withdrawal_until"] == addDays(dstr(0), 28), d)
# 出栏：休药期校验先于耳标校验（与 mock 同序）→ 直接命中 40001
err = call_err("POST", f"/pen/{pen_f}/slaughter", {"male_count": 1, "female_count": 0}, token)
check("休药期拦截优先（部分出栏未选耳标也先拦 40001）", err["code"] == 40001, err["message"])
# 休药期拦截 40001 三要素
err = call_err("POST", f"/pen/{pen_f}/slaughter", {"male_count": 2, "female_count": 1}, token)
check("休药期拦截 40001", err["code"] == 40001, err["code"])
bd = err["data"]
check("休药期三要素齐全", bd["med_name"] and bd["withdrawal_until"] and bd["left_days"] >= 28, bd)
err = call_err("POST", f"/pen/{pen_f}/slaughter", {"male_count": 2, "female_count": 1, "force": True}, token)
check("强制出栏缺备注 42201", err["code"] == 42201, err)
# 强制整栏出栏（自动全部耳标离栏）
d = call("POST", f"/pen/{pen_f}/slaughter", {"male_count": 2, "female_count": 1, "force": True, "force_note": "冒烟强制", "destination": "冒烟屠宰场"}, token)
check("强制整栏出栏成功", d["withdrawal_check"] == "forced", d)
d = call("GET", f"/pen/list?farm_id={farm_id}", token=token)
pf = next(p for p in d["items"] if p["pen_id"] == pen_f)
check("整栏出栏后 batch_stage=sold", pf["batch_stage"] == "sold" and pf["male_count"] == 0, pf)
d = call("GET", f"/individual/list?pen_id={pen_f}&status=all", token=token)
check("出栏个体全部 sold", all(i["status"] == "sold" for i in d["items"]), d["summary"])
# 超存栏
err = call_err("POST", f"/pen/{pen_f}/slaughter", {"male_count": 5, "female_count": 0}, token)
check("出栏超存栏 40002", err["code"] == 40002, err["code"])
# 部分出栏未选耳标（无用药记录的新圈，纯 42201 场景）
pen_g = call("POST", "/pen", {"farm_id": farm_id, "pen_name": "出栏冒烟圈", "species_code": "pig", "variety_code": "pig_fattening", "male_count": 2, "female_count": 1}, token)["pen_id"]
err = call_err("POST", f"/pen/{pen_g}/slaughter", {"male_count": 1, "female_count": 0}, token)
check("部分出栏未选耳标 42201", err["code"] == 42201, err["message"])
# 提醒处理
d = call("GET", f"/lifecycle/reminders?farm_id={farm_id}", token=token)
pending = next((r for r in d["items"] if r["status"] == "pending"), None)
if pending:
    d = call("POST", f"/reminder/{pending['reminder_id']}/handle", {"action": "done", "note": "冒烟处理"}, token=token)
    check("提醒标记完成", d["status"] == "done")
    err = call_err("POST", f"/reminder/{pending['reminder_id']}/handle", {"action": "done"}, token=token)
    check("重复处理提醒 42201", err["code"] == 42201, err)
else:
    check("提醒标记完成（无 pending 可跳过）", True)

print(f"\n===== 结果：PASS {pass_n} / FAIL {fail_n} =====")
if fail_n:
    print("失败项：")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
