"""告警实时通道 `/ws/alerts`（F03 FR-05 + 03-API 附录 WS 约定）。

凭据走 **Sec-WebSocket-Protocol 子协议头**，不拼 query string —— 握手 URL 会原样进
反代与 uvicorn 访问日志，把 24h 业务 token 写进去等于长期凭据外泄（docs/08 C-03）。
因此这里只认 `/auth/ws-token` 签发的 5 分钟 `scope=ws` 短令牌。

浏览器侧无法给 WebSocket 请求加自定义头，但**可以**指定子协议
（`new WebSocket(url, [token])`），服务端必须把它回显在 accept 响应里，否则 Chrome
判定握手失败——所以下面 accept 时显式带 `subprotocol=`。

消息约定（与 mock 的 AlertEvent 同形状，前端只需换传输层）：
    下行 {"event":"connected"|"alert"|"pong", "data":{...}}
    上行 纯文本 "ping" → 回 {"event":"pong"}（30s 心跳；空闲时靠它探活并穿越代理超时）
前端收到 alert 只做查询缓存失效重取（不把帧内容当权威），与 mock 时代行为一致。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.core.db import SessionLocal
from app.core.security import decode_ws_token
from app.models import User
from app.services import ws_hub

logger = logging.getLogger("app.ws")
router = APIRouter(prefix="/api")

CLOSE_UNAUTHORIZED = 4401
CLOSE_GONE = 4403


def _offered_protocol(ws: WebSocket) -> str:
    """取客户端第一个候选子协议（我们只把令牌放在这一个位置上）。"""
    return (ws.headers.get("sec-websocket-protocol") or "").split(",")[0].strip()


@router.websocket("/ws/alerts")
async def alerts_socket(ws: WebSocket) -> None:
    token = _offered_protocol(ws)
    if not token:
        # 未 accept 直接 close：Starlette 回 HTTP 403，握手失败，不会留下半开连接
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return
    try:
        payload = decode_ws_token(token)
    except Exception:  # noqa: BLE001 — 令牌无效/过期都按未授权处理，不给探测者区分线索
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return

    user_id = int(payload.get("user_id") or 0)
    async with SessionLocal() as db:
        row = (
            await db.execute(select(User.user_id, User.is_enable, User.role).where(User.user_id == user_id))
        ).first()
    if row is None or row.is_enable is False:
        await ws.close(code=CLOSE_GONE)
        return

    await ws.accept(subprotocol=token)
    ws_hub.register(user_id, ws)
    logger.info("告警通道已连接 user=%s role=%s 在线用户=%s", user_id, row.role, ws_hub.online_users())
    try:
        await ws.send_json({"event": "connected", "data": {"user_id": user_id, "role": row.role}})
        while True:
            text = await ws.receive_text()
            if text.strip().lower() == "ping":
                await ws.send_json({"event": "pong", "data": {}})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — 半开/协议错误：静默摘除，不影响其他连接
        logger.info("告警通道异常断开 user=%s", user_id)
    finally:
        ws_hub.unregister(user_id, ws)
