"""Prompt 模板与 LLM 任务函数 — PRD 附录 A 约束规范的落地。

单一实现、两处消费：
- `app/api/ai.py`：HTTP 端点（前端 mock 编排层调用），失败返回 code=50003 由前端静默降级；
- `app/agents/nodes.py`：LangGraph 节点内直接调用，失败静默回退规则文案（EX-03）。

硬约束（不依赖模型自觉，代码层兜底）：
1. 疑似疾病只能来自知识库候选，候选外疾病名一律丢弃（白名单过滤）；
2. 风险分级取主疑似条目自己的 `risk_level`，由节点确定性推导，模型不得改判；
3. 模型不得输出药品名称与用法用量——用药方案由 medication_check 节点查兽药库生成；
4. 存栏/体重等数字只可原样引用圈舍上下文，不得编造。

两个诊断任务：
- `enhance_diagnosis`：表述增强（/api/ai/diagnose 端点 + 旧口径），疾病名不出候选；
- `llm_diagnose`：LLM 主导诊断（LangGraph diagnose 节点），额外产出 primary_disease
  供节点重排 rag_docs，让主疑似驱动用药/台账/HITL；白名单与上述约束同款生效。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.llm import LLMError, chat, extract_json

INTENTS = {"consult", "feed", "data_query", "weather", "unknown"}

INTENT_SYSTEM = (
    "你是养殖场助手的意图分类器。把用户消息分为以下意图之一：\n"
    "consult=描述畜禽症状/生病/伤亡等健康问题；"
    "feed=问饲料配方/喂量/营养/料价/料肉比；"
    "data_query=查询场内数据（存栏数量/温湿度曲线/告警记录/病例复核状态）；"
    "weather=问天气/降温/下雨/寒潮/台风等气象；"
    "unknown=以上都不是（闲聊/问候/与养殖无关）。\n"
    '只输出 JSON：{"intent": "..."}，不要输出任何其他内容。'
)

DIAGNOSE_SYSTEM = (
    "你是畜牧兽医 AI 助手，基于给定知识库候选做症状理解与鉴别分析。\n"
    "硬约束：\n"
    "1. 疑似疾病只能从知识库候选的 disease_name 中选择，禁止引入候选之外的疾病名；\n"
    "2. 风险等级以系统给定为准，你不得改判；\n"
    "3. 禁止输出任何具体药品名称与用法用量（用药方案由系统兽药库另行生成）；\n"
    "4. 不得编造数字：存栏/体重等仅可原样引用圈舍上下文；\n"
    '5. 只输出 JSON：{"symptom_summary": "症状理解（1-2句）", '
    '"differentials": [{"name": "候选内疾病名", "reason": "鉴别理由"}], '
    '"care_advice": ["饲养管理建议1", "建议2"]}。中文输出。'
)

DIAGNOSE_PRIMARY_SYSTEM = (
    "你是畜牧兽医 AI 助手，负责在给定知识库候选内完成症状理解与主疑似病判定。\n"
    "硬约束：\n"
    "1. primary_disease 与 differentials.name 只能从知识库候选的 disease_name 中选择，"
    "禁止引入候选之外的疾病名；\n"
    "2. 你只判定主疑似与鉴别理由；风险等级与用药方案由系统另行生成，"
    "禁止在输出中出现任何药品名称、用法用量或风险等级；\n"
    "3. 不得编造数字：存栏/体重等仅可原样引用圈舍上下文；\n"
    '4. 只输出 JSON：{"symptom_summary": "症状理解（1-2句）", '
    '"primary_disease": "主疑似（候选内疾病名）", '
    '"differentials": [{"name": "候选内疾病名", "reason": "鉴别理由"}], '
    '"care_advice": ["饲养管理建议1", "建议2"]}。中文输出。'
)

VISION_SYSTEM = (
    "你是畜牧兽医图像观察助手。用户上传了畜禽照片，你的任务是【只描述图片中客观可见的异常体征】，"
    "为后续知识库检索提供症状关键词。\n"
    "硬约束：\n"
    "1. 只描述肉眼可见征象（皮肤颜色/疱疹/溃疡、眼鼻分泌物、被毛状态、体型消瘦、姿势异常、"
    "排泄物沾染、呼吸腹式起伏等），不下疾病结论、不给用药建议；\n"
    "2. 禁止编造：图片中看不清或不存在的征象不得输出；图片与畜禽无关时如实说明；\n"
    "3. signs 只能填简短症状词（≤10 字/条，最多 6 条），不得填疾病名或药品名；\n"
    '4. 只输出 JSON：{"observation": "整体观察（1-2 句）", "signs": ["症状词1", "症状词2"]}。'
    "中文输出。"
)

VISION_DATA_URL_MAX_CHARS = 4_000_000  # ≈3MB base64，前端压缩后远低于此


async def classify_intent(message: str) -> str:
    """意图分类兜底（F05 PRD 决策 2：规则未命中才调用）。失败抛 LLMError 由调用方决定降级。"""
    text = await chat(
        [
            {"role": "system", "content": INTENT_SYSTEM},
            {"role": "user", "content": message[:500]},  # EX-D11 同款 500 字截断
        ],
        temperature=0.0,
        max_tokens=64,
        timeout=20.0,
    )
    obj = extract_json(text)
    intent = str(obj.get("intent", "unknown")).strip()
    return intent if intent in INTENTS else "unknown"


def _rag_docs_block(rag_docs: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- {d['disease_name']}（系统风险分级 {d['risk_level']}）"
        f"症状要点：{'、'.join(d['symptom_keywords'][:6]) or '见用户描述'}；来源：{d['source']}"
        for d in rag_docs[:5]
    ) or "（知识库无候选）"


async def enhance_diagnosis(
    *,
    message: str,
    species_code: str,
    ctx_text: str,
    hit_keywords: list[str],
    rag_docs: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    """诊断表述增强：返回 (diagnosis_text, model)。

    只做"症状理解/鉴别/饲养建议"的语言增强；疾病名过白名单，输出为空视为失败。
    history 为最近若干轮会话（多轮追问时给模型上下文，如"还是不吃食"这类省略句）。
    """
    prior = "\n".join(
        f"{'养殖户' if h.get('role') == 'user' else '助手'}：{str(h.get('content', ''))[:200]}"
        for h in (history or [])[-4:]
    )
    user = (
        f"物种：{species_code}\n"
        f"圈舍上下文：{ctx_text}\n"
        f"命中症状关键词：{'、'.join(hit_keywords) or '无'}\n"
        f"知识库候选：\n{_rag_docs_block(rag_docs)}\n"
        + (f"本轮之前的对话：\n{prior}\n" if prior else "")
        + f"养殖户描述：{message}"
    )
    text = await chat(
        [
            {"role": "system", "content": DIAGNOSE_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=900,
        timeout=40.0,
    )
    obj = extract_json(text)
    summary = str(obj.get("symptom_summary", "")).strip()
    diffs = obj.get("differentials") if isinstance(obj.get("differentials"), list) else []
    advice = obj.get("care_advice") if isinstance(obj.get("care_advice"), list) else []

    allowed = {d["disease_name"] for d in rag_docs}
    lines: list[str] = []
    if summary:
        lines.append(f"症状理解：{summary}")
    for d in diffs:
        # 白名单二次兜底：候选外疾病名一律丢弃（附录 A 防幻觉，不依赖模型自觉）
        if isinstance(d, dict) and str(d.get("name", "")).strip() in allowed:
            reason = str(d.get("reason", "")).strip()[:120]
            lines.append(f"鉴别：{d.get('name')}——{reason}" if reason else f"鉴别：{d.get('name')}")
    for a in advice:
        if isinstance(a, str) and a.strip():
            lines.append(f"饲养建议：{a.strip()[:120]}")
    if not lines:
        raise LLMError("模型输出为空")
    return "\n".join(lines[:10]), settings.llm_model


async def llm_diagnose(
    *,
    message: str,
    species_code: str,
    ctx_text: str,
    hit_keywords: list[str],
    rag_docs: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
    kb_excerpts: str = "",
) -> dict[str, Any]:
    """LLM 主导诊断（diagnose 节点升级口径）：在知识库候选白名单内判定主疑似病。

    返回 {"text": 诊断正文, "model": 模型名, "primary": 主疑似病名}。
    与 enhance_diagnosis 的区别：模型给出 primary_disease，节点据此重排 rag_docs，
    让主疑似驱动用药校验/台账/HITL；白名单过滤仍在本函数（候选外一律回退检索首位，
    附录 A 约束 1，不依赖模型自觉）。正文无任何可用内容时抛 LLMError，
    由节点静默回退规则正文（EX-03）。
    """
    prior = "\n".join(
        f"{'养殖户' if h.get('role') == 'user' else '助手'}：{str(h.get('content', ''))[:200]}"
        for h in (history or [])[-4:]
    )
    user = (
        f"物种：{species_code}\n"
        f"圈舍上下文：{ctx_text}\n"
        f"命中症状关键词：{'、'.join(hit_keywords) or '无'}\n"
        f"知识库候选：\n{_rag_docs_block(rag_docs)}\n"
        + (
            f"知识库文档正文节选（本场知识库书册内容，可引用其中细节与具体管理数字，出处随节选标注）：\n{kb_excerpts}\n"
            if kb_excerpts
            else ""
        )
        + (f"本轮之前的对话：\n{prior}\n" if prior else "")
        + f"养殖户描述：{message}"
    )
    text = await chat(
        [
            {"role": "system", "content": DIAGNOSE_PRIMARY_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=900,
        timeout=40.0,
    )
    obj = extract_json(text)
    summary = str(obj.get("symptom_summary", "")).strip()
    raw_primary = str(obj.get("primary_disease", "")).strip()
    diffs = obj.get("differentials") if isinstance(obj.get("differentials"), list) else []
    advice = obj.get("care_advice") if isinstance(obj.get("care_advice"), list) else []

    allowed = {d["disease_name"] for d in rag_docs}
    # 白名单过滤：主疑似必须是候选内疾病名，否则回退检索首位（附录 A 约束 1）
    primary = raw_primary if raw_primary in allowed else rag_docs[0]["disease_name"]

    lines: list[str] = []
    if summary:
        lines.append(f"症状理解：{summary}")
    reason_by_name = {
        str(d.get("name", "")).strip(): str(d.get("reason", "")).strip()[:120]
        for d in diffs
        if isinstance(d, dict)
    }
    # 主疑似放鉴别首位，其余候选内鉴别项跟随（候选外 reason 一律丢弃）
    ordered = [primary] + [n for n in reason_by_name if n in allowed and n != primary]
    for name in ordered[:3]:
        reason = reason_by_name.get(name, "")
        lines.append(f"鉴别：{name}——{reason}" if reason else f"鉴别：{name}")
    for a in advice:
        if isinstance(a, str) and a.strip():
            lines.append(f"饲养建议：{a.strip()[:120]}")
    if not lines:
        raise LLMError("模型输出为空")
    return {"text": "\n".join(lines[:10]), "model": settings.llm_model, "primary": primary}


async def describe_vision(*, image: str, species_code: str, message: str) -> dict[str, Any]:
    """图像问诊：视觉模型只做客观征象描述器，疾病判定仍由知识库管道完成（FR-11）。"""
    if not image.startswith("data:image/"):
        raise LLMError("图片格式不支持（仅接受图片 data URL）")
    from app.agents.kb import species_name  # 局部导入避免循环依赖

    user_text = (
        f"物种：{species_name(species_code)}\n"
        f"养殖户补充描述：{message.strip() or '（无，请以图片为准）'}\n"
        "请观察图片中的可见异常体征。"
    )
    text = await chat(
        [
            {"role": "system", "content": VISION_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image}},
                ],
            },
        ],
        temperature=0.2,
        max_tokens=400,
        timeout=45.0,
        model=settings.llm_vision_model,
    )
    obj = extract_json(text)
    observation = str(obj.get("observation", "")).strip()[:200]
    raw_signs = obj.get("signs") if isinstance(obj.get("signs"), list) else []
    signs = [s.strip()[:10] for s in raw_signs if isinstance(s, str) and s.strip()][:6]
    if not observation and not signs:
        raise LLMError("视觉模型输出为空")
    return {"observation": observation, "signs": signs, "model": settings.llm_vision_model}


# ----- F15 场景照片识别（拍照建 3D 场景）：多图 → 参数化场景配置 -----

SCENE_SYSTEM = (
    "你是养殖场设施识别专家。用户上传了自家场舍的照片，"
    "请识别动物种类与圈舍形态，输出严格 JSON（不要 markdown 代码块、不要多余解释），字段：\n"
    '{"species": "pig|cattle|sheep", "pen_count": 2到6的整数, '
    '"wall_color": "#rrggbb", "roof_color": "#rrggbb", '
    '"has_silo": true或false, "has_fans": true或false, "has_cool_pad": true或false, "has_yard": true或false, '
    '"barn_name": "不超过10字的场舍名", "summary": "不超过80字的识别说明", '
    '"confidence": 0到1的小数}\n'
    "判定口径：猪舍多为封闭双坡+室外料塔+负压风机+湿帘；牛棚多为敞开式+饲槽颈枷+运动场；"
    "羊舍常见楼式高床漏缝地板+草架+运动场。照片中看不到的设施，按该物种常见配置给合理默认值；"
    "wall_color/roof_color 取照片中墙体与屋顶的主色调。"
)


async def analyze_scene(*, images: list[str]) -> dict[str, Any]:
    """多张场舍照片 → 参数化 3D 场景配置（严格 JSON，字段齐全后由端点侧兜底校验）。"""
    if not images:
        raise LLMError("请至少上传一张场舍照片")
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"共上传 {len(images)} 张照片，请综合识别场舍结构与养殖动物。"}
    ]
    for img in images:
        if not img.startswith("data:image/"):
            raise LLMError("图片格式不支持（仅接受图片 data URL）")
        content.append({"type": "image_url", "image_url": {"url": img}})
    text = await chat(
        [
            {"role": "system", "content": SCENE_SYSTEM},
            {"role": "user", "content": content},
        ],
        temperature=0.2,
        max_tokens=500,
        timeout=60.0,
        model=settings.llm_vision_model,
    )
    obj = extract_json(text)

    def _hex(v: Any, fallback: str) -> str:
        s = str(v).strip()
        if len(s) == 7 and s[0] == "#":
            try:
                int(s[1:], 16)
                return s.lower()
            except ValueError:
                pass
        return fallback

    def _bool(v: Any) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "yes", "有", "是")

    species = str(obj.get("species", "pig")).strip()
    if species not in ("pig", "cattle", "sheep"):
        species = "pig"
    try:
        pen_count = int(obj.get("pen_count", 4))
    except (TypeError, ValueError):
        pen_count = 4
    pen_count = max(2, min(6, pen_count))
    try:
        confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.8))))
    except (TypeError, ValueError):
        confidence = 0.8

    return {
        "config": {
            "species": species,
            "pen_count": pen_count,
            "wall_color": _hex(obj.get("wall_color"), "#f4f1e8"),
            "roof_color": _hex(obj.get("roof_color"), "#8f9aa3"),
            "has_silo": _bool(obj.get("has_silo")),
            "has_fans": _bool(obj.get("has_fans")),
            "has_cool_pad": _bool(obj.get("has_cool_pad")),
            "has_yard": _bool(obj.get("has_yard")),
            "barn_name": str(obj.get("barn_name", "")).strip()[:12] or "我的场舍",
        },
        "summary": str(obj.get("summary", "")).strip()[:120] or "已按照片识别生成场景配置",
        "confidence": round(confidence, 2),
        "model": settings.llm_vision_model,
    }
