"""告警微信推送（WxPusher）— 通知出口之一。

设计口径：
- **尽力而为**：推送失败只记日志，绝不影响告警落库/实时 WS 主链路（通知是增益，不是依赖）；
- **主题订阅制**：养殖户在 WxPusher 公众号里扫码加入主题即订阅，平台不存任何用户设备标识，
  免去 UID 绑定/存储/解绑一整套表；农户换手机重新扫码即可；
- **风险分级路由**：mid → 全部告警主题；high → 全部告警主题 +（可选）高危专属主题各推一份；
- **token 未配置 = 通道整体静默关闭**，业务代码无感知。

接入步骤（管理员一次性操作，详见 WxPusher 官方文档 https://wxpusher.zjiecode.com/docs）：
  1. 微信扫码登录 wxpusher.zjiecode.com → 创建应用 → 复制 APP_TOKEN；
  2. 创建两个主题：「全部告警」「仅高危」，记下主题 ID；
  3. 服务端 .env 配 WX_PUSHER_TOKEN / WX_PUSHER_TOPIC_ALL / WX_PUSHER_TOPIC_HIGH /
     WX_PUSHER_SUBSCRIBE_URL（控制台生成的订阅二维码页链接，设置页会展示给农户）；
  4. 重启 API。之后中危及以上告警自动推微信。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger("app.notify")

_WXPUSHER_API = "https://wxpusher.zjiecode.com/api/send/message"
_TIMEOUT = 10.0

RISK_LABEL = {"low": "提示", "mid": "中危", "high": "高危"}


def wxpusher_enabled() -> bool:
    return bool(settings.wxpusher_token and settings.wxpusher_topic_all)


def subscribe_url() -> str:
    return settings.wxpusher_subscribe_url or ""


_bg_tasks: set[asyncio.Task[None]] = set()


def schedule_alert_push(pen_name: str, alert_type: str, risk_level: str, content: str) -> None:
    """在告警产生处调用：fire-and-forget，失败只记日志（通知绝不拖垮告警主链路）。

    create_task 的引用必须保活（裸 create_task 可能被 GC），set+done_callback 是官方推荐姿势。
    """
    if not wxpusher_enabled() or risk_level not in ("mid", "high"):
        return
    task = asyncio.create_task(push_alert(pen_name, alert_type, risk_level, content))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def push_alert(pen_name: str, alert_type: str, risk_level: str, content: str) -> None:
    """中危及以上告警推微信（fire-and-forget，调用方用 asyncio.create_task 挂起）。"""
    if not wxpusher_enabled():
        return
    topics: list[int] = []
    if settings.wxpusher_topic_all:
        topics.append(settings.wxpusher_topic_all)
    if risk_level == "high" and settings.wxpusher_topic_high and settings.wxpusher_topic_high != settings.wxpusher_topic_all:
        topics.append(settings.wxpusher_topic_high)
    if not topics:
        return

    label = RISK_LABEL.get(risk_level, risk_level)
    html = (
        f"<h3 style='margin:0 0 8px'>[{label}] {pen_name} · {alert_type}</h3>"
        f"<p style='margin:0;color:#333'>{content}</p>"
        f"<p style='margin:8px 0 0;color:#999;font-size:12px'>—— 云上牧场 · 请尽快登录平台处理</p>"
    )
    body: dict[str, Any] = {
        "appToken": settings.wxpusher_token,
        "content": html,
        "summary": f"[{label}] {pen_name}：{content[:40]}",
        "contentType": 3,
        "topicIds": topics,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(_WXPUSHER_API, json=body)
            resp = r.json()
        if not resp.get("success"):
            logger.warning("WxPusher 推送被拒 alert_type=%s resp=%s", alert_type, resp)
        else:
            logger.info("WxPusher 推送成功 alert_type=%s risk=%s topics=%s", alert_type, risk_level, topics)
    except Exception:  # noqa: BLE001 — 通知失败绝不上抛：告警主链路不能被外部服务拖垮
        logger.exception("WxPusher 推送异常 alert_type=%s", alert_type)
