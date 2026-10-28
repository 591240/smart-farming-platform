"""F15 拍照建 3D 场景 —— POST /api/scene/analyze-photo + GET /api/scene/latest。

这条路径此前不在 `VITE_REAL_API_PREFIXES` 里，由 `client.ts` 特例转发到浏览器自配的
`sf_llm_base`：既没有任何 UI 能写这个键（真账号下点拍照识别必然失败），也绕过了统一的
Bearer 鉴权。现收敛为普通业务路径——前端走 apiFetch（自动带 token），模型调用与
可见征象约束全部复用 `agents/prompts.analyze_scene`。

识别结果落 `scene_analyses`（只存派生配置，不存照片）：此前结果只活在组件的 useState 里，
刷新即消失，而页面话术写着"已按照片生成『××』场景"——刷新后那句就成了假话。

`/api/ai/scene` 是同一次模型调用的网关入口（供自检与脚本直调，不落库）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import prompts
from app.api.ai import fail, ok
from app.core.db import get_db
from app.core.ratelimit import llm_rate_limit
from app.core.security import get_current_user
from app.models import SceneAnalysis, User
from app.services.llm import LLMError

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])

MAX_IMAGES = 6
DATA_URL_MAX_CHARS = prompts.VISION_DATA_URL_MAX_CHARS  # 单张 ≈3MB base64，前端压缩后远低于此


class SceneBody(BaseModel):
    images: list[str] = Field(min_length=1, max_length=MAX_IMAGES)


@router.post("/scene/analyze-photo")
async def analyze_photo(
    body: SceneBody, user: User = Depends(llm_rate_limit), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    imgs = [i for i in body.images if isinstance(i, str) and i.startswith("data:image/")]
    if not imgs:
        return fail("请至少上传一张场舍照片")
    if any(len(i) > DATA_URL_MAX_CHARS for i in imgs):
        return fail("单张图片过大，请压缩后重试")
    try:
        result = await prompts.analyze_scene(images=imgs)
    except LLMError as e:
        return fail(str(e))
    config = result.get("config") or {}
    if not config:  # 模型返回结构不合规时不落半成品
        return fail("模型未返回可用的场景配置，请重试或换一张更清晰的照片")
    row = SceneAnalysis(
        user_id=user.user_id,
        config=config,
        summary=str(result.get("summary") or ""),
        confidence=float(result.get("confidence") or 0),
        image_count=len(imgs),
        model=str(result.get("model") or ""),
    )
    db.add(row)
    await db.commit()
    return ok({**result, "analysis_id": row.analysis_id})


@router.get("/scene/latest")
async def latest_scene(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """最近一次识别结果（进入 3D 场景页时先恢复它，刷新不再是空场景）。"""
    row = (
        await db.execute(
            select(SceneAnalysis).where(SceneAnalysis.user_id == user.user_id).order_by(SceneAnalysis.analysis_id.desc()).limit(1)
        )
    ).scalars().first()
    if row is None:
        return ok({"has_scene": False})
    return ok({
        "has_scene": True,
        "analysis_id": row.analysis_id,
        "config": row.config or {},
        "summary": row.summary,
        "confidence": float(row.confidence or 0),
        "image_count": row.image_count,
        "model": row.model,
        "create_time": row.create_time.isoformat() if row.create_time else "",
    })
