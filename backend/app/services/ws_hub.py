"""告警 WebSocket 广播中枢（F03 实时提醒）。

三条纪律：

1. **提交后才推**：`create_alert` 只把待发消息挂在 `session.info` 上，真正的发送挂在
   SQLAlchemy 的 `after_commit` 事件里。若在 flush 后立即推，事务回滚时客户端会收到
   一条库里根本不存在的告警（前端角标 +1 却查不到，比不推更糟）。
2. **推送不影响业务**：任何连接异常都吞掉并摘除该连接，告警落库永不因广播失败而回滚。
3. **按归属人分片**：只推给该场 `farm.user_id` 对应的连接，别人连上也看不到
   （与业务侧租户过滤同口径）。

跨进程：消息经 statestore 广播，每个 worker 都订阅同一 channel，只有握着该用户 socket 的
进程真正发出去。socket 对象本身出不了进程，所以 `_connections` 保持进程本地，
`online_users()` 统计的是**本进程**在线数（不是全局）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import event, select

from app.core import statestore
from app.core.db import SessionLocal
from app.models import Farm

logger = logging.getLogger("app.ws")

_CHANNEL = "ws"
_connections: dict[int, set[Any]] = {}  # user_id -> 活跃 socket（同一用户多端同时在线）


def attach(db: Any, farm_id: int, payload: dict[str, Any]) -> None:
    """登记"本事务提交后向该场归属人推这条消息"（db 为请求内 AsyncSession）。"""
    db.info.setdefault("ws_pending", []).append((farm_id, payload))


def register(user_id: int, ws: Any) -> None:
    _connections.setdefault(user_id, set()).add(ws)


def unregister(user_id: int, ws: Any) -> None:
    socks = _connections.get(user_id)
    if not socks:
        return
    socks.discard(ws)
    if not socks:
        _connections.pop(user_id, None)


def online_users() -> int:
    return len(_connections)


async def _deliver_local(message: dict[str, Any]) -> None:
    """订阅回调：握着这个用户的连接才发，否则静默略过（连接在别的进程上）。"""
    user_id = int(message.get("user_id") or 0)
    for ws in list(_connections.get(user_id, ())):
        try:
            await ws.send_json(message["payload"])
        except Exception:  # noqa: BLE001 — 半开连接：摘除，绝不让异常冒回业务事务
            unregister(user_id, ws)


def _drain(session: Any) -> None:
    """after_commit 回调：把本事务登记的告警投给事件循环（不在这里 await，避免阻塞提交）。"""
    pending = session.info.pop("ws_pending", None)
    if not pending:
        return

    async def run() -> None:
        farm_ids = {fid for fid, _payload in pending}
        async with SessionLocal() as db:
            rows = (await db.execute(select(Farm.farm_id, Farm.user_id).where(Farm.farm_id.in_(farm_ids)))).all()
        owners = dict(rows)
        for farm_id, payload in pending:
            user_id = owners.get(farm_id)
            if user_id:
                await statestore.publish(_CHANNEL, {"user_id": int(user_id), "payload": payload})

    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:  # 无运行中的循环（如脚本内同步提交）——丢弃推送，不影响已落库的告警
        logger.info("告警广播跳过：当前无事件循环（%s 条）", len(pending))


def install() -> None:
    """挂到同步 Session 类上：AsyncSession 的 after_commit 实际由内层同步 session 触发。"""
    # 回调注册在状态层实例上，Redis 探活接管时会被带走，所以这里先注册也有效
    statestore.subscribe(_CHANNEL, _deliver_local)
    event.listen(SessionLocal.class_.sync_session_class, "after_commit", _drain)


install()
