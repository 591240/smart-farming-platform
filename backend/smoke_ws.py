"""告警 WebSocket 冒烟（打 8001 测试实例）。

运行：
    AI_PORT=8001 DATABASE_URL=...smart_farm_test python run.py     # 另开窗口
    DATABASE_URL=...smart_farm_test python smoke_ws.py

覆盖：短令牌握手、子协议回显（浏览器强制要求）、心跳、真实告警触发后的下行帧、
业务长期 token 不能直连 WS（C-03 的封堵点）、无凭据握手失败、推送按归属人分片。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys

import httpx
import websockets

from app.core.config import settings

# Windows 控制台默认 GBK：断言名里的「−」等非 GBK 字符会在 print 时抛
# UnicodeEncodeError 直接中断整组测试（与业务无关的假失败）。统一按 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001/api").rstrip("/")
WS = BASE.replace("http://", "ws://").replace("https://", "wss://") + "/ws/alerts"
PASS = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} | {detail}")
        FAILED.append(f"{name} | {detail}")


def rnd() -> str:
    return "138" + "".join(random.choices("0123456789", k=8))


def setup() -> tuple[httpx.Client, dict[str, str], int]:
    c = httpx.Client(base_url=BASE, timeout=30, trust_env=False)
    phone = rnd()
    c.post("/auth/register", json={"phone": phone, "password": "Farm123456"})
    tk = c.post("/auth/login", json={"phone": phone, "password": "Farm123456"}).json()["data"]["token"]
    H = {"Authorization": f"Bearer {tk}"}
    c.put("/user/main-species", json={"main_species_code": "pig"}, headers=H)
    farm = c.post("/farm", json={
        "farm_name": "WS冒烟场", "province": "湖南省", "city": "长沙市", "county": "长沙县",
        "adcode": "430121", "address": "WS路 1 号", "contact_person": "WS", "phone": phone,
    }, headers=H).json()["data"]["farm_id"]
    pen = c.post("/pen", json={
        "farm_id": farm, "pen_name": "WS圈", "species_code": "pig", "variety_code": "pig_fattening",
        "male_count": 8, "female_count": 2, "batch_stage": "fattening",
    }, headers=H).json()["data"]["pen_id"]
    return c, H, pen


async def ws_token(c: httpx.Client, H: dict[str, str]) -> str:
    return c.post("/auth/ws-token", headers=H).json()["data"]["ws_token"]


async def main() -> None:
    if "smart_farm_test" not in settings.database_url:
        raise SystemExit(f"拒绝运行：DATABASE_URL 指向 {settings.database_url.split('@')[-1]}，非测试库")

    c, H, pen = setup()

    print("\n== 握手与令牌边界 ==")
    body = c.post("/auth/ws-token", headers=H).json()["data"]
    check("ws-token 返回短令牌与过期时间戳", bool(body.get("ws_token")) and body.get("expires_in", 0) > 0, list(body))
    ttl_min = (body["expires_in"] / 1000 - __import__("time").time()) / 60
    check("有效期 ≤10 分钟（短令牌而非业务 token）", 0 < ttl_min <= 10, round(ttl_min, 2))

    token = body["ws_token"]
    async with websockets.connect(WS, subprotocols=[token], open_timeout=10) as ws:
        connected = json.loads(await asyncio.wait_for(ws.recv(), 5))
        check("connected 帧带 user_id 与角色", connected.get("event") == "connected" and connected["data"].get("user_id"), connected)
        check("服务端回显子协议（浏览器强制要求）", ws.subprotocol == token, ws.subprotocol)

        await ws.send("ping")
        pong = json.loads(await asyncio.wait_for(ws.recv(), 5))
        check("心跳 ping → pong", pong.get("event") == "pong", pong)

        print("\n== 告警下行 ==")
        device_id = c.post("/device", json={
            "device_name": "WS摄像头", "device_type": "camera",
            "device_sn": "CAM" + "".join(random.choices("0123456789", k=6)),
            "pen_id": pen, "rtsp_url": "rtsp://127.0.0.1:554/ws",
        }, headers=H).json()["data"]["device_id"]
        sim = c.post("/vision/simulate", json={"device_id": device_id, "event_type": "death", "confidence": 0.8}, headers=H).json()["data"]
        alert_id = (sim.get("result") or {}).get("alert_id")
        check("注入产生告警（前置条件）", bool(alert_id), sim)
        frame = json.loads(await asyncio.wait_for(ws.recv(), 8))
        check("告警实时推达且 alert_id 与响应一致", frame.get("event") == "alert" and frame["data"]["alert_id"] == alert_id, frame)
        d = frame.get("data") or {}
        check("下行字段齐全（与 mock AlertEvent 同形状）",
              {"alert_id", "alert_type", "risk_level", "alert_content", "pen_name", "status", "create_time"} <= set(d),
              sorted(d))
        check("high 级 death 事件风险等级正确", d.get("risk_level") == "high" and d.get("alert_type") == "vision", d)

    print("\n== 越权与无效凭据 ==")
    biz_phone = rnd()
    c.post("/auth/register", json={"phone": biz_phone, "password": "Farm123456"})
    biz_token = c.post("/auth/login", json={"phone": biz_phone, "password": "Farm123456"}).json()["data"]["token"]
    try:
        async with websockets.connect(WS, subprotocols=[biz_token], open_timeout=8):
            check("业务长期 token 不能直连 WS", False, "竟然连上了")
    except Exception as e:
        check("业务长期 token 被拒（不是 scope=ws）", True, type(e).__name__)

    other, OH, _p2 = setup()
    other_token = await ws_token(other, OH)
    async with websockets.connect(WS, subprotocols=[other_token], open_timeout=10) as ws2:
        await asyncio.wait_for(ws2.recv(), 5)  # connected
        sn = "CAM" + "".join(random.choices("0123456789", k=6))
        dev2 = other.post("/device", json={
            "device_name": "另一摄像头", "device_type": "camera", "device_sn": sn,
            "pen_id": _p2, "rtsp_url": "rtsp://127.0.0.1:554/x",
        }, headers=OH).json()["data"]["device_id"]
        r = other.post("/vision/simulate", json={"device_id": dev2, "event_type": "water", "confidence": 0.95}, headers=OH).json()["data"]
        check("另一账号自身注入产生告警", bool((r.get("result") or {}).get("alert_id")), r)
        mine = json.loads(await asyncio.wait_for(ws2.recv(), 8))
        check("推送按归属人分片（他人收到的是自己的告警）",
              mine["data"]["alert_id"] == r["result"]["alert_id"], (mine["data"], r["result"]))

    bad = httpx.Client(base_url=BASE, timeout=20, trust_env=False)
    try:
        async with websockets.connect(WS, open_timeout=6):
            check("无子协议凭据握手失败", False, "竟然连上了")
    except Exception as e:
        check("无凭据握手失败（不进半开状态）", True, type(e).__name__)
    check("未鉴权的 ws-token 请求 40101", bad.post("/auth/ws-token").json().get("code") == 40101)

    print(f"\n{'=' * 56}")
    for x in FAILED:
        print("  × " + x)
    print(f"PASS {PASS} / FAIL {len(FAILED)}")
    sys.exit(1 if FAILED else 0)


asyncio.run(main())
