"""告警落库与合并窗口 — 告警出口唯一（01-架构 核心原则 3）。

环境告警、设备离线、气象告警都从这里出，保证：
- 告警 + 台账一次写入（record_type=告警类型，带 alert_id 溯源）；
- 同类合并窗口口径统一（默认 1h），气象用 `climate:{pen}:{type}:{level}` 让 mid→high 升级
  能重新出一条（key 带 level 正是为此）。

合并窗口状态走 `core/statestore`：配了 Redis 就是全进程共享的一份，没配则退回进程内存
（单 worker 演示可用，多 worker 会各算各的窗口）。`in_merge_window` 是唯一的窗口入口，
调用方不感知底下接的是哪种。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rules import MERGE_WINDOW_MS
from app.core import statestore
from app.models import Alert, LedgerRecord, Pen
from app.services import ws_hub


def now_ms() -> int:
    return int(time.time() * 1000)


async def in_merge_window(key: str, window_ms: int = MERGE_WINDOW_MS) -> bool:
    """命中窗口返回 True（并跳过）；未命中则占位，本次允许产生告警。

    占位与判重是同一个原子动作（Redis `SET NX PX`），所以两条并发告警只会有一条放行
    ——进程内 dict 时代是先 get 再 set，并发下有窗口被双双放过的竞态。
    """
    return not await statestore.claim(f"merge:{key}", window_ms)


async def create_alert(
    db: AsyncSession, pen: Pen, alert_type: str, risk_level: str, content: str, ext: dict[str, Any] | None = None
) -> Alert:
    """告警落库 + 台账 + 登记实时推送（02-数据库口径：告警产生类台账 record_type=告警类型，带 alert_id 溯源）。"""
    alert = Alert(
        farm_id=pen.farm_id, pen_id=pen.pen_id, alert_type=alert_type, risk_level=risk_level, alert_content=content
    )
    db.add(alert)
    await db.flush()
    db.add(
        LedgerRecord(
            farm_id=pen.farm_id,
            pen_id=pen.pen_id,
            record_type=alert_type,
            summary=content,
            stock_change_id=0,
            alert_id=alert.alert_id,
            record_ext=ext or {},
        )
    )
    # 广播不是告警的一部分：只登记，真正发送等 after_commit（回滚就不推，见 ws_hub 纪律 1）
    await db.refresh(alert)
    ws_hub.attach(db, pen.farm_id, {
        "event": "alert",
        "data": {
            "alert_id": alert.alert_id,
            "alert_type": alert.alert_type,
            "risk_level": alert.risk_level,
            "alert_content": alert.alert_content,
            "pen_name": pen.pen_name,
            "status": alert.status,
            "create_time": alert.create_time.isoformat() if alert.create_time else None,
        },
    })
    return alert
