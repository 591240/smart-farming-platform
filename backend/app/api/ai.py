"""AI 端点 — LLM 语言智能服务（F05 切真第一步 + F11 意图兜底 + FR-11 图像问诊）。

V0.3 重构：Prompt 与后处理（含防幻觉白名单）统一收敛到 `app/agents/prompts.py`，
本文件只做 HTTP 契约适配。同一份实现两处消费：
- 这里：给前端 mock 编排层调用，失败返回 code=50003 由前端静默降级；
- `app/agents/nodes.py`：LangGraph 节点内直接调用，失败静默回退规则文案（EX-03）。

架构口径：业务数据/检索/分级/兽药库校验保持确定性（附录 A 约束 5 工程保证），
本服务只做三类"语言智能"——意图分类兜底、诊断表述增强、图像可见征象描述。
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.agents import prompts
from app.core.config import settings
from app.core.ratelimit import llm_rate_limit
from app.core.security import get_current_user
from app.services.llm import LLMError

# 鉴权口径：本组端点是花真金白银 token 的 LLM 出口，必须登录态 + 单用户限流。
# 此前整组匿名可达——局域网内任何来源都能驱动 qwen 视觉/诊断调用（成本与滥用面无界），
# 且前端拍照识别走的也是它（现在改由 /scene 带 token 调用）。
router = APIRouter(prefix="/api/ai", dependencies=[Depends(get_current_user), Depends(llm_rate_limit)])


def ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


def fail(message: str) -> dict[str, Any]:
    # 50003 = 外部服务降级（03-API 1.3 错误码表口径），前端按降级链路处理
    return {"code": 50003, "message": f"AI 服务暂不可用：{message}", "data": None}


# ----- 意图分类兜底（F05 PRD 决策 2 / F11 FR-01 切真口径）-----

class IntentReq(BaseModel):
    message: str = Field(min_length=1, max_length=600)


@router.post("/intent")
async def classify_intent(req: IntentReq) -> dict[str, Any]:
    try:
        intent = await prompts.classify_intent(req.message)
        return ok({"intent": intent, "model": settings.llm_model})
    except LLMError as e:
        return fail(str(e))


# ----- 问诊诊断表述增强（F05 切真第一步）-----

class RagDoc(BaseModel):
    disease_name: str
    risk: str
    symptoms: list[str] = Field(default_factory=list)
    source: str = ""


class DiagnoseReq(BaseModel):
    message: str = Field(min_length=1, max_length=600)
    species_code: str = "pig"
    stage_label: str = ""
    ctx_text: str = ""
    hit_keywords: list[str] = Field(default_factory=list)
    rag_docs: list[RagDoc] = Field(default_factory=list)


@router.post("/diagnose")
async def diagnose(req: DiagnoseReq) -> dict[str, Any]:
    try:
        text, model = await prompts.enhance_diagnosis(
            message=req.message,
            species_code=req.species_code,
            ctx_text=req.ctx_text,
            hit_keywords=req.hit_keywords,
            # 前端 mock 传的是 RagDoc 形状，统一映射为 kb 条目键名
            rag_docs=[
                {
                    "disease_name": d.disease_name,
                    "risk_level": d.risk,
                    "symptom_keywords": d.symptoms,
                    "source": d.source,
                }
                for d in req.rag_docs
            ],
        )
        return ok({"diagnosis_text": text, "model": model})
    except LLMError as e:
        return fail(str(e))


# ----- 图像问诊：病症图片可见体征识别（F05 FR-11）-----

VISION_DATA_URL_MAX_CHARS = prompts.VISION_DATA_URL_MAX_CHARS


class VisionReq(BaseModel):
    image: str = Field(min_length=32, max_length=VISION_DATA_URL_MAX_CHARS)
    species_code: str = "pig"
    message: str = Field(default="", max_length=600)


@router.post("/vision")
async def recognize_vision(req: VisionReq) -> dict[str, Any]:
    try:
        result = await prompts.describe_vision(
            image=req.image, species_code=req.species_code, message=req.message
        )
        return ok(result)
    except LLMError as e:
        return fail(str(e))


# ----- F15 场景照片识别：多张场舍照片 → 参数化 3D 场景配置 -----

class SceneReq(BaseModel):
    images: list[str] = Field(min_length=1, max_length=6)


@router.post("/scene")
async def analyze_scene_endpoint(req: SceneReq) -> dict[str, Any]:
    imgs = [i for i in req.images if isinstance(i, str) and i.startswith("data:image/")]
    if not imgs:
        return fail("请至少上传一张场舍照片")
    if any(len(i) > VISION_DATA_URL_MAX_CHARS for i in imgs):
        return fail("单张图片过大，请压缩后重试")
    try:
        result = await prompts.analyze_scene(images=imgs)
        return ok(result)
    except LLMError as e:
        return fail(str(e))
