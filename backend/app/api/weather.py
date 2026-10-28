"""F08 气象端点 — GET /weather（03-API 十五节 / F08 PRD FR-01~08）。

契约要点：`degraded` 键在不降级时**整个省略**（不是 false），`reason`/`adcode` 等
也只在各自状态下出现——前端按"缺省即否"处理，多给一个 false 会让徽标语义变味。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.response import E_FORBIDDEN, E_NOT_FOUND, E_VALIDATION, E_WEATHER_UNAVAILABLE, ApiError, ok
from app.core.security import get_current_user, require_farmer
from app.models import Farm, User
from app.services import weather as wx

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])


@router.get("/weather")
async def get_weather(
    farm_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """气象 + 养殖专属风险。farm_id 缺省取主养殖场（场级信息，无需圈舍定位）。"""
    farm: Farm | None = None
    if farm_id is not None:
        farm = await db.get(Farm, farm_id)
        if farm is None or farm.is_deleted:
            raise ApiError(E_NOT_FOUND, "该养殖场不存在或已删除")
        if farm.user_id != user.user_id:
            raise ApiError(E_FORBIDDEN, "无权限访问该养殖场")
    else:
        farm = await wx.primary_farm(db, user.user_id)

    if farm is None:
        return ok({"available": False, "reason": "no_farm"})
    result = await wx.resolve_weather_for_farm(db, farm)
    if not result["ok"]:
        if result["reason"] == "no_adcode":
            return ok({"available": False, "reason": "no_adcode", "farm_id": farm.farm_id})
        raise ApiError(E_WEATHER_UNAVAILABLE, "气象服务暂不可用，请稍后重试")  # EX-02 无缓存分支

    payload = result["payload"]
    items = await wx.generate_climate_alerts(db, farm, payload)
    data: dict[str, Any] = {
        "available": True,
        "farm_id": farm.farm_id,
        "adcode": farm.adcode,
        "data_time": payload.get("fetched_at"),  # 降级时即旧缓存时间
        "source": result["source"],
        "forecast": payload.get("forecast") or [],
        "warnings": payload.get("warnings") or [],
        "risk": wx.aggregate_risk(items, payload),
    }
    if result["degraded"]:
        data["degraded"] = True  # 只在降级时出现
    return ok(data)
