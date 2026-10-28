"""MQTT 上行桥接 — EMQX → FastAPI 设备接入。

链路（07-硬件通信协议）：

    STM32/ESP32 ──publish──> EMQX ──subscribe──> 本模块 ──> ingest_payload()

为什么放在后端进程内而不是单独一个 consumer 容器：设备上行要落库、要触发防抖/分级/告警，
需要一个连着数据库的执行体。去重/防抖/合并窗口这些**状态**已经上移到 core/statestore
（接了 Redis 就跨进程共享），但把桥接拆成独立进程只会多出一套消费与提交路径，不换来任何语义。
进程内订阅 + 直调 `ingest_payload` 让"MQTT 进来的数据"和"HTTP 进来的数据"走**同一条**校验链，
不需要额外的 HTTP 回环，也不会出现两套去重表。

鉴权口径：MQTT 连接由 EMQX 的 username/password 认证，topic 由 EMQX 的 ACL 约束；
进入 `ingest_payload` 时传 `trusted=True` 跳过设备密钥校验，但 **topic 与设备归属的一致性
仍然逐条校验**（`farm/{fid}/pen/{pid}/{sn}/data` 必须与 devices 表里的记录吻合），
所以一条被伪造到别人 topic 的消息只会得到 topic-mismatch，不会写进别人圈舍。

未配置 MQTT_ENABLED=true 时整条桥接不启动，设备只能走 HTTP 直连（真实硬件带 X-Device-Key）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.config import settings
from app.core.db import SessionLocal

logger = logging.getLogger("app.mqtt")

_client: Any = None
_loop: asyncio.AbstractEventLoop | None = None
_started = False


def _parse(payload: bytes) -> dict[str, Any] | None:
    """07 协议 data 帧：{"msg_id","ts","metrics":{...}}；容忍 metrics 直接平铺在根上。"""
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    metrics = raw.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {k: v for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if not metrics:
        return None
    return {
        "msg_id": str(raw.get("msg_id") or ""),
        "ts": raw.get("ts"),
        "metrics": metrics,
    }


async def _handle(topic: str, payload: bytes) -> None:
    from app.api.device import IngestBody, ingest_payload  # 局部导入：避免启动期循环依赖

    data = _parse(payload)
    if data is None:
        logger.warning("MQTT 丢弃非法 payload topic=%s", topic)
        return
    # topic 的 sn 段即设备序列号；payload 里没写 device_id 时以 topic 为准
    parts = topic.split("/")
    sn = parts[4] if len(parts) == 6 else ""
    body = IngestBody(
        msg_id=data["msg_id"],
        device_id=str(data.get("device_id") or sn),
        topic=topic,
        ts=data["ts"],
        metrics=data["metrics"],
    )
    async with SessionLocal() as db:
        try:
            res = await ingest_payload(db, body, trusted=True)
        except Exception:  # noqa: BLE001 — 单条消息失败不得拖垮订阅线程
            logger.exception("MQTT 消息入库异常 topic=%s", topic)
            return
    if res.get("data", {}).get("ok") is not True:
        logger.info("MQTT 消息未入库 topic=%s reason=%s", topic, res.get("data", {}).get("reason"))


def _on_message(_client: Any, _userdata: Any, msg: Any) -> None:
    if _loop is None or _loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(_handle(msg.topic, msg.payload), _loop)


def _on_connect(client: Any, _userdata: Any, _flags: Any, rc: int) -> None:
    if rc != 0:
        logger.error("MQTT 连接失败 rc=%s（3=服务不可用 4=账密错误 5=未授权）", rc)
        return
    client.subscribe(settings.mqtt_topic, qos=1)
    logger.info("MQTT 已订阅 %s @ %s:%s", settings.mqtt_topic, settings.mqtt_host, settings.mqtt_port)


def start(loop: asyncio.AbstractEventLoop) -> None:
    """应用启动时调用；未开启或未装 paho 时安静跳过（不阻塞业务接口）。"""
    global _client, _loop, _started
    if _started or not settings.mqtt_enabled:
        return
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        logger.error("MQTT_ENABLED=true 但未安装 paho-mqtt（pip install paho-mqtt），桥接未启动")
        return
    _loop = loop
    client = mqtt.Client(client_id="sf-backend-bridge", clean_session=True)
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password or None)
    client.on_connect = _on_connect
    client.on_message = _on_message
    try:
        client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
    except OSError as e:
        logger.error("MQTT 连接 %s:%s 失败：%s（桥接未启动）", settings.mqtt_host, settings.mqtt_port, e)
        return
    client.loop_start()
    _client = client
    _started = True


def stop() -> None:
    global _client, _started
    if _client is not None:
        try:
            _client.loop_stop()
            _client.disconnect()
        except Exception:  # noqa: BLE001 — 退出路径不抛
            pass
    _client = None
    _started = False
