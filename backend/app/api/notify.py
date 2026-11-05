"""告警通知状态接口 — GET /api/notify/wx-status。

给设置页「微信告警通知」卡用：前端据此展示订阅入口（enabled=true 时带订阅链接）或
未开启引导文案。不含敏感信息：token 永不出接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.ai import ok  # 统一响应封装（ok/fail 定义在 ai.py，scene.py 同款复用）
from app.core.security import get_current_user
from app.models import User
from app.services import notify

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])


@router.get("/notify/wx-status")
async def wx_status(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return ok(
        {
            "enabled": notify.wxpusher_enabled(),
            "subscribe_url": notify.subscribe_url(),
        }
    )
