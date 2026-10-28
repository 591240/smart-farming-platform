"""LangGraph 节点实现 — F05 问诊链路 + F10 HITL 门（01-架构 9.1/9.3/9.4）。

节点与目标架构的对应关系：
| 本文件节点 | 架构文档 | 说明 |
|-----------|----------|------|
| intent_recognize | 主图 intent_recognize | **规则预判命中即返回，不调 LLM**（F05 决策 2，省成本） |
| load_context_retrieve | 问诊子图同名节点 | 品种路由+圈舍上下文+RAG 合并一步（PRD V1.5） |
| diagnose | disease_agent | **LLM 主导诊断**：知识库候选白名单内判定主疑似病（primary），节点重排 rag_docs 让用药/台账/HITL 随主疑似走；风险分级取**主疑似自身的** `risk_level`（确定性推导，模型不得改判），失败静默回退规则正文 |
| medication_check | medication_agent | 兽药库校验 + 违禁拦截 + 休药期强制输出（确定性，不过模型） |
| record | record | 会话消息 + disease/medicine 台账 + 高危建复核单 |
| hitl_gate | hitl_gate(interrupt) | 全系统唯一中断点，**节点内无任何副作用** |
| record_review | —（新增） | resume 后落 vet_review 留痕台账 |
| fallback_reply / clarify_reply / kb_reply | fallback | 兜底/澄清/EX-04 拒答的出口节点 |

两条实测得出的硬纪律（spike 验证，见 tmp-spike-langgraph.py 结论）：
1. **resume 时只有含 interrupt() 的节点从头重跑**，已完成节点不重跑 → 建复核单必须放在
   hitl_gate 之前的 record 节点，否则每次复核都会插一条重复 vet_review；
2. hitl_gate 内禁止写库/发消息，只做 interrupt() 与状态回填。
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from sqlalchemy import select

from app.agents.kb import (
    CONSULT_EXAMPLE,
    FORBIDDEN_MENTIONS,
    FORBIDDEN_REPLY,
    STAGE_LABEL,
    doc_to_kb_entry,
    hit_keywords_of,
    max_risk,
    search_diseases_smart,
    species_name,
)
from app.agents.prompts import classify_intent, describe_vision, llm_diagnose
from app.agents.state import ConsultState
from app.core.db import SessionLocal
from app.models import (
    AgentMessage,
    AgentSession,
    AnimalIndividual,
    Farm,
    KbDocument,
    LedgerRecord,
    MedicineLib,
    Pen,
    User,
    VetReview,
)
from app.services.llm import LLMError

# ===== 口径常量（与前端 mock-backend.ts 逐值对齐，避免切真后行为漂移）=====
MESSAGE_MAX_CHARS = 500        # EX-D11
TOKEN_CHUNK_CHARS = 14         # SSE token 帧切片宽度
CLARIFY_TTL_MS = 10 * 60_000   # 澄清槽位记忆有效期
CLARIFY_MERGE_MAX_LEN = 30     # 短补位判定长度
# 纯问候/寒暄（EX-D14）：不消费澄清槽位、不进检索诊断，走友好回复
_GREETING_RE = re.compile(
    r"^(?:你好|您好|hello|hi|嗨|哈喽|在吗|在么|在不在|谢谢|辛苦了|麻烦了|早上好|下午好|晚上好|你好呀)[吧呢嘛呀啊哈啦！!？?。.、~\s]*$",
    re.I,
)
_GREETING_REPLY = (
    "您好！我是云上牧场 AI 助手，可以帮您分析畜群症状、查数据、算饲料。"
    "直接描述症状（如咳嗽、不吃食、拉稀），或回复圈舍名，我马上开始。"
)
REVIEW_WARN_HOURS = 24         # EX-05 置顶标红
REVIEW_TIMEOUT_HOURS = 48      # EX-06 惰性降级（不 resume）

RULE_DIAGNOSIS_DISCLAIMER = "以上为知识库匹配的疑似判断，仅作辅助参考，不能替代执业兽医现场诊断。"
LLM_DIAGNOSIS_DISCLAIMER = "以上判断由 AI 模型结合知识库生成，仅作辅助参考，不能替代执业兽医现场诊断。"
MED_SOURCE = "本地兽药库（medicine_lib）校验通过"

INTENT_CONSULT_KEYWORDS = [
    "咳嗽", "喘", "气喘", "呼吸", "腹泻", "拉稀", "拉肚子", "下痢", "稀粪", "水样", "血便", "便血", "黑便", "便秘",
    "不吃食", "不吃料", "采食下降", "吃料", "减料", "呕吐", "发烧", "发热", "高热", "体温",
    "皮肤", "发紫", "发绀", "红疹", "疹块", "出血", "结痂", "脱毛", "蹭痒", "螨",
    "流产", "死胎", "早产", "木乃伊", "返情", "乳房", "产后", "瘫", "站不起来",
    "转圈", "抽搐", "划水", "水肿", "眼睑", "共济", "跛行", "瘸", "关节",
    "消瘦", "贫血", "苍白", "流泪", "鼻涕", "感冒", "精神", "萎靡", "扎堆", "磨牙", "尖叫",
    "病", "死了", "死亡", "猝死", "症状", "流涎", "无乳",
]
INTENT_FEED_KEYWORDS = ["配方", "饲料", "喂多少", "饲喂", "料量", "营养", "玉米", "豆粕", "麸皮", "预混料", "换料", "料肉比"]
INTENT_DATA_KEYWORDS = [
    "多少头", "几头", "存栏", "数量", "温度", "湿度", "氨气", "曲线", "告警", "报警",
    "查一下", "看看数据", "统计", "复核", "病例",
]
INTENT_WEATHER_KEYWORDS = ["天气", "下雨", "气温", "寒潮", "台风", "降温", "下雪", "预报"]

GUIDANCE_TEXT = {
    "feed": "配方与饲喂功能即将开放：喂食中心正在建设中（日粮配方、替代方案、饲喂日历、采食曲线），上线后可直接在此生成可执行配方。当前可在台账档案查看饲喂相关记录。",
    "data_query": "数据查询功能即将开放：届时您可以用自然语言直接询问存栏、圈舍温度曲线与告警情况。当前可前往总览看板与台账档案页查看场内数据。",
    "weather": "气象服务即将开放：上线后将提供养殖专属气象风险提醒（高温、寒潮、强降水等）与联动饲喂调整建议。",
}

# F11 FR-01：写指令祈使模式（unknown/data_query + 祈使 → 拒绝路径，安全侧倾斜）
WRITE_INSTRUCTION_RE = re.compile(
    r"(帮我|给我|替我|麻烦)[^。？！?！]{0,16}(加|减|调|调整|登记|录入|修改|删除|处理)"
    r"|(登记|录入|修改|删除)[^。？！?！]{0,10}(存栏|饲喂|用药|设备|告警)"
    r"|^(加|减)\s*\d+\s*(头|只)"
)


# ===== SSE 帧发射（01-架构 十一节帧契约；stream_mode="custom" 经 astream 转发）=====

def emit(event: str, data: dict[str, Any]) -> None:
    """向 SSE 流推一帧。非流式调用（如 /vet/review 的 resume）时静默丢弃。"""
    try:
        get_stream_writer()({"event": event, "data": data})
    except Exception:  # noqa: BLE001 - 无 writer 上下文（ainvoke 路径）不算错误
        pass


def stream_text(text: str) -> None:
    """按 14 字切 token 帧（与 mock 同宽，前端逐帧拼接的视觉节奏一致）。"""
    for i in range(0, len(text), TOKEN_CHUNK_CHARS):
        emit("token", {"text": text[i : i + TOKEN_CHUNK_CHARS]})


def stream_section(key: str, title: str, text: str, source: str | None = None) -> None:
    emit("section", {"key": key, "title": title, "source": source})
    stream_text(text)


def looks_like_write_instruction(message: str) -> bool:
    return bool(WRITE_INSTRUCTION_RE.search(message))


def write_reject_guidance(message: str) -> str:
    if re.search(r"存栏|头|只|猪|牛|羊", message):
        return "调整存栏请到【场舍管理 → 调整存栏】操作"
    if re.search(r"饲喂|饲料|配方|喂", message):
        return "饲喂与配方请到【喂食中心】操作"
    if re.search(r"设备|摄像头|传感器", message):
        return "设备请到【设备管理】添加或调整"
    if re.search(r"告警|报警", message):
        return "告警处理请到【告警中心】操作"
    return "请到对应功能页面操作"


def classify_intent_by_rule(message: str) -> str:
    """规则意图识别：命中即返回，**不产生 LLM 调用**（F05 决策 2 的成本口径）。"""
    # 「复核」优先判定：问诊词表含单字「病」，会误吞「病例复核了吗」类查询
    if "复核" in message:
        return "data_query"
    if any(k in message for k in INTENT_CONSULT_KEYWORDS):
        return "consult"
    if any(k in message for k in INTENT_FEED_KEYWORDS):
        return "feed"
    if any(k in message for k in INTENT_DATA_KEYWORDS):
        return "data_query"
    if any(k in message for k in INTENT_WEATHER_KEYWORDS):
        return "weather"
    return "unknown"


def unknown_guidance(main_species_code: str | None) -> str:
    code = main_species_code if main_species_code in ("pig", "sheep", "cattle") else "pig"
    return (
        f"我没太理解您的意思。您可以描述{species_name(code)}的症状"
        f"（如“{CONSULT_EXAMPLE[code]}”）进行问诊；数据查询、配方饲喂与天气服务将陆续开放。"
    )


async def _list_my_pens(db, user_id: int) -> list[Pen]:
    farm_ids = (
        await db.execute(select(Farm.farm_id).where(Farm.user_id == user_id, Farm.is_deleted.is_(False)))
    ).scalars().all()
    if not farm_ids:
        return []
    return list(
        (await db.execute(select(Pen).where(Pen.farm_id.in_(farm_ids), Pen.is_deleted.is_(False)))).scalars().all()
    )


async def _load_individual(db, individual_id: int | None, user_id: int) -> dict[str, Any] | None:
    """取绑定个体快照（只认本人名下的）。F14 个体绑定问诊/告警的读侧入口。

    越权与不存在一律当"没绑定"处理而不是报错：问诊链路一旦开流就无法再返回 JSON 错误码，
    而 API 层（agent.py）已做过一次归属校验，这里是节点内的二次确认。
    """
    if not individual_id:
        return None
    row = await db.get(AnimalIndividual, individual_id)
    if row is None or row.is_deleted:
        return None
    farm = await db.get(Farm, row.farm_id)
    if farm is None or farm.user_id != user_id:
        return None
    age_days: int | None = None
    if row.birth_date:
        age_days = max(0, (date.today() - row.birth_date).days)
    return {
        "individual_id": row.individual_id,
        "ear_tag_no": row.ear_tag_no,
        "gender": row.gender,
        "pen_id": row.pen_id,
        "status": row.status,
        "birth_date": row.birth_date.isoformat() if row.birth_date else None,
        "age_days": age_days,
    }


_ANIMAL_CHARS = set("羊猪牛马驴骡犊崽禽鸡鸭鹅兔犬头只匹口公母")


def _resolve_pen(pens: list[Pen], pen_id: int | None, message: str) -> Pen | None:
    """圈舍定位四分支（03-API 9.1）：显式 pen_id → 单圈自动 → 槽位抽取 → 定位失败。"""
    if pen_id:
        return next((p for p in pens if p.pen_id == pen_id), None)
    if len(pens) == 1:
        return pens[0]
    # 逐个试所有"N号"，不能只取首个：澄清合并轮把上一轮原话拼在前面，首个数字往往是
    # 用户上次答错的那个（"3号圈…"），会永久遮住本轮新答的"1号圈"→ 死循环再问一遍。
    # 但"N号"后接动物/量词时说的是第 N 只畜（"3号圈的2号羊"），不能当圈舍号——定位到
    # 错圈比再问一次更糟，这类命中一律跳过（真叫「2号母猪圈」的仍由末行整名匹配兜住）。
    for m in re.finditer(r"(\d{1,3})\s*号", message):
        if message[m.end():m.end() + 1] in _ANIMAL_CHARS:
            continue
        hit = next((p for p in pens if f"{m.group(1)}号" in p.pen_name), None)
        if hit:
            return hit
    return next((p for p in pens if len(p.pen_name) >= 2 and p.pen_name in message), None)


async def _persist_round(
    db,
    state: ConsultState,
    *,
    content: str,
    meta: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
    intent: str = "consult",
) -> int:
    """一轮问答落库：user + assistant 消息 + 会话 updateTime（同事务）。返回 assistant message_id。"""
    user_meta: dict[str, Any] | None = {"image": state["image"]} if state.get("image") else None
    db.add(AgentMessage(session_id=state["session_id"], role="user", content=state["message"], meta=user_meta))
    assistant = AgentMessage(
        session_id=state["session_id"],
        role="assistant",
        content=content,
        meta={**meta, **({"sections": sections} if sections else {})},
    )
    db.add(assistant)
    session = await db.get(AgentSession, state["session_id"])
    if session is not None:
        session.intent = intent
        # update_time 必须显式写：onupdate=func.now() 只在真的发出 UPDATE 时触发，
        # 而 intent 从 "consult" 赋成 "consult" 不算变更 → 会话列表按 update_time 排序时
        # 刚聊过的会话浮不到顶部。
        session.update_time = datetime.now(timezone.utc)
    await db.flush()
    return assistant.message_id


# =====================================================================
# 节点 1：意图识别（规则预判 → 未命中且非写指令才调 LLM）
# =====================================================================

async def intent_recognize(state: ConsultState) -> dict[str, Any]:
    message = (state.get("message") or "").strip()[:MESSAGE_MAX_CHARS]
    greeting_only = bool(_GREETING_RE.match(message))

    # 澄清槽位记忆（F05 FR-03）：上一轮问了"哪个圈"，本轮短补位与原始描述合并重诊；
    # 落库与展示仍记用户原话（审计真实），合并文本只用于意图/定位/检索。
    # 纯问候不消费槽位（EX-D14）：保留 pending，下一句真正的补位仍可合并。
    eff_message = message
    async with SessionLocal() as db:
        session = await db.get(AgentSession, state["session_id"])
        pending = (session.pending_clarify if session else None) or {}
        if (
            not greeting_only
            and pending.get("msg")
            and int(time.time() * 1000) - int(pending.get("at") or 0) < CLARIFY_TTL_MS
            and len(message) <= CLARIFY_MERGE_MAX_LEN
        ):
            eff_message = f"{pending['msg']} {message}"
            session.pending_clarify = None  # 本轮消费（无论后续定位成功或再澄清）
            await db.commit()

    # 纯问候 → 友好回复，不进检索诊断；若正等着补圈舍，顺带提醒一句
    if greeting_only:
        hint = "另外，上一条还在等您补充圈舍信息，直接回复圈舍名即可。" if pending.get("msg") else ""
        emit("intent", {"intent": "consult"})
        return {
            "eff_message": message,
            "intent": "consult",
            "intent_source": "rule",
            "guard_text": f"{_GREETING_REPLY}{hint}",
        }

    # 违禁药诱导拦截先于意图与诊断（TC-G07：任何形态提及一律不提供使用信息）
    forbidden = next((w for w in FORBIDDEN_MENTIONS if w in message), None)
    if forbidden:
        emit("intent", {"intent": "consult"})
        return {
            "eff_message": eff_message,
            "intent": "consult",
            "intent_source": "rule",
            "guard_text": FORBIDDEN_REPLY.format(word=forbidden),
        }

    writeish = looks_like_write_instruction(eff_message)
    intent = classify_intent_by_rule(eff_message)
    source = "rule"

    # 规则未命中才调 LLM 兜底；写指令疑似不送模型（安全侧倾斜）
    if intent == "unknown" and not writeish:
        try:
            llm_intent = await classify_intent(eff_message)
            if llm_intent in ("consult", "feed", "data_query", "weather"):
                intent, source = llm_intent, "llm"
        except LLMError:
            pass  # EX-03：保持 unknown 走兜底话术，不阻塞链路

    if state.get("image") and intent != "consult":
        intent = "consult"  # FR-11：上传图片即问诊信号

    write_rejected = intent in ("data_query", "unknown") and writeish
    eff_intent = "data_query" if write_rejected else intent
    emit("intent", {"intent": eff_intent})
    return {
        "eff_message": eff_message,
        "intent": eff_intent,
        "intent_source": source,
        "write_rejected": write_rejected,
        "guard_text": "",
    }


# =====================================================================
# 节点 1.5：vision_describe — FR-11 图像问诊（VLM 只做客观征象描述器）
# =====================================================================

VISION_NOTE = "图片分析不能替代执业兽医现场诊断。"


async def vision_describe(state: ConsultState) -> dict[str, Any]:
    """把图片里的可见征象转成症状词并入检索文本。

    纪律（PRD 3.5.9 / 架构 4.6）：疾病判定、风险分级、用药仍由知识库+兽药库管道产出，
    视觉模型只当"征象描述器"；模型输出无法识别或调用失败时如实说明，不硬猜、不阻断问诊。
    """
    image = state.get("image")
    if not image:
        return {}

    try:
        res = await describe_vision(
            image=image, species_code=state.get("main_species_code") or "pig", message=state["message"]
        )
    except LLMError:
        stream_section("vision", "图片识别", f"图片未能识别出明确体征，请以文字描述症状。\n{VISION_NOTE}", "多模态视觉模型")
        return {}

    signs = [s for s in (res.get("signs") or []) if s]
    observation = str(res.get("observation") or "").strip()
    if not signs and not observation:
        stream_section("vision", "图片识别", f"图片中未观察到明确异常体征，请补充文字描述。\n{VISION_NOTE}", str(res.get("model") or ""))
        return {}

    signs_text = "、".join(signs) if signs else observation
    text = f"从图片识别到以下可见征象：{signs_text}。\n"
    if signs and observation:
        text += f"{observation}\n"
    stream_section("vision", "图片识别", f"{text}{VISION_NOTE}", str(res.get("model") or "多模态视觉模型"))

    # 征象词并入检索文本（下一节点按合并文本做关键词匹配），展示与落库仍记用户原话
    return {
        "eff_message": f"{state['eff_message']} {' '.join(signs)}".strip(),
        "vision_signs": signs,
    }


# =====================================================================
# 出口节点 A：违禁拦截 / 非问诊意图引导 / unknown 兜底（EX-09）
# =====================================================================

async def fallback_reply(state: ConsultState) -> dict[str, Any]:
    guard = state.get("guard_text") or ""
    if guard:
        text, meta = guard, {"intent": "consult"}
    elif state.get("write_rejected"):
        text = f"数据查询助手只能查询，不能修改数据。{write_reject_guidance(state['message'])}"
        meta = {"intent": "data_query", "write_rejected": True}
    elif state["intent"] in GUIDANCE_TEXT:
        text, meta = GUIDANCE_TEXT[state["intent"]], {"intent": state["intent"]}
    else:
        # 补位轮只圈定了圈舍、无症状描述 → 针对性追问而非干巴巴 unknown
        async with SessionLocal() as db:
            pens = await _list_my_pens(db, state["user_id"])
        named = next((p for p in pens if len(p.pen_name) >= 2 and p.pen_name in state["eff_message"]), None)
        text = (
            f"已定位到「{named.pen_name}」。请描述{species_name(state.get('main_species_code'))}的症状"
            f"（如咳嗽、不吃食、拉稀），我马上为您分析。"
            if named
            else unknown_guidance(state.get("main_species_code"))
        )
        meta = {"intent": state["intent"]}

    stream_text(text)
    async with SessionLocal() as db:
        await _persist_round(db, state, content=text, meta=meta, intent=state["intent"])
        await db.commit()
    done = {"session_id": state["session_id"]}
    if state.get("write_rejected"):
        done["write_rejected"] = True
    emit("done", done)
    return {"done": done}


# =====================================================================
# 节点 2：加载上下文与知识检索（品种路由 + 圈舍上下文 + RAG 合并一步）
# =====================================================================

async def load_context_retrieve(state: ConsultState) -> dict[str, Any]:
    eff_message = state["eff_message"]
    # 定位优先用本轮原话：合并串里上一轮的旧圈舍号排在前面，答对的新圈舍会被它遮住
    raw_message = (state.get("message") or "").strip()
    async with SessionLocal() as db:
        user = await db.get(User, state["user_id"])
        pens = await _list_my_pens(db, state["user_id"])
        pen = _resolve_pen(pens, state.get("pen_id"), raw_message) or _resolve_pen(
            pens, state.get("pen_id"), eff_message
        )
        individual = await _load_individual(db, state.get("individual_id"), state["user_id"])
        # 绑了个体就以该个体所在圈定位：话里另提一个圈时，档案不能挂在错圈上；
        # 没提圈也不再追问"哪个圈"——问的就是那头。
        if individual is not None:
            pen = next((p for p in pens if p.pen_id == individual["pen_id"]), pen)

        if pen is None and not pens:
            return {
                "clarify": True,
                "clarify_text": "您还没有创建养殖场，请先到【场舍管理】完成建场建圈后再来问诊。",
            }

        # 物种动态口径：口语点名词 > 账号主物种 > 猪兜底（不写死"猪"，羊场用户不被问"哪个圈舍的猪"）
        mentioned = next(
            (c for c, w in (("pig", "猪"), ("sheep", "羊"), ("cattle", "牛")) if w in eff_message), None
        )

        if pen is None:
            # 先答后问（2026-10-15 用户定的口径）：圈舍没定位到不再挡住诊断。按物种通用口径
            # 先出结论，record 末尾追问是哪个圈；下一轮补位后带存栏/批次上下文重诊。
            # 旧行为是直接 return 澄清，配上"如 3号圈"这种账号里根本不存在的假例子，
            # 用户被反复问同一个问题，看起来就像"大模型不回复"（13 圈同名"1号育肥圈"实测死循环）。
            code = mentioned or (user.main_species_code if user else None) or "pig"
            farm_names = {
                f.farm_id: f.farm_name
                for f in (
                    await db.execute(select(Farm).where(Farm.farm_id.in_({p.farm_id for p in pens})))
                ).scalars()
            }
            labels = list(dict.fromkeys(f"{farm_names.get(p.farm_id, '')}{p.pen_name}" for p in pens))
            shown = "、".join(labels[:5]) + ("等" if len(labels) > 5 else "")
            followup = (
                f"以上按{species_name(code)}通用口径给出，还没有绑定到具体圈舍。"
                f"请告诉我是哪个圈舍：{shown}。直接回复圈舍名称即可，"
                f"症状不用重打，我会带上该圈存栏与批次信息重新分析。"
            )
            pen_snapshot = {
                "pen_id": 0,
                "farm_id": 0,
                "pen_name": "未定位圈舍",
                "species_code": code,
                "variety_code": None,
                "male_count": 0,
                "female_count": 0,
                "batch_stage": None,
            }
            pen_unresolved = True
        else:
            followup = ""
            pen_snapshot = {
                "pen_id": pen.pen_id,
                "farm_id": pen.farm_id,
                "pen_name": pen.pen_name,
                "species_code": pen.species_code or "pig",
                "variety_code": pen.variety_code,
                "male_count": pen.male_count,
                "female_count": pen.female_count,
                "batch_stage": pen.batch_stage,
            }
            pen_unresolved = False

        # F16：管理员上传且启用的知识库文档入检索池（与内置种子同池计分，B5 Milvus 前形态）
        doc_rows = (
            await db.execute(
                select(KbDocument).where(KbDocument.is_enable.is_(True), KbDocument.is_deleted.is_(False))
            )
        ).scalars().all()

    # 未定位圈舍时性别构成未知 → 按含母畜的宽松口径检索，免得把母畜专属病误判成"不适用"
    has_female = True if pen_unresolved else pen_snapshot["female_count"] > 0
    species_code = pen_snapshot["species_code"]
    extra_entries = [doc_to_kb_entry(d) for d in doc_rows]
    # B5：向量语义检索优先（Milvus），失败/无命中回退关键词计分（extra_entries 仅关键词路径生效）
    matched, not_applicable = await search_diseases_smart(
        eff_message, species_code, has_female, top_k=5, extra_entries=extra_entries
    )

    # 2026-10-15 知识回灌第二步：命中的知识库文档正文节选进问诊上下文——
    # 全文化书册（数千字）不可能整本塞 prompt，按检索关键词定位取 1100 字窗口节选，
    # top 2 篇封顶 3000 字；LLM 据此引用书内细节（具体操作数字/管理要点）。
    kb_excerpts = ""
    kb_doc_ids = [m.get("doc_id") for m in matched[:2] if m.get("from_kb_doc") and m.get("doc_id")]
    if kb_doc_ids:
        try:
            async with SessionLocal() as db:
                docs = (
                    await db.execute(select(KbDocument).where(KbDocument.doc_id.in_(kb_doc_ids)))
                ).scalars().all()
            by_id = {d.doc_id: d for d in docs}
            parts: list[str] = []
            for m in matched[:2]:
                d = by_id.get(m.get("doc_id"))
                if d is None:
                    continue
                content = (d.content_md or "").strip()
                if not content:
                    continue
                positions = (p for p in (content.find(k) for k in (m.get("symptom_keywords") or [])) if p >= 0)
                pos = min(positions, default=-1)
                excerpt = content[max(0, pos - 300): pos + 800] if pos >= 0 else content[:1100]
                parts.append(f"《{d.doc_name}》（来源：{d.source}）节选：\n{excerpt}……")
            kb_excerpts = "\n\n".join(parts)[:3000]
        except Exception:  # noqa: BLE001 — 节选失败不阻塞问诊（条目结构化字段仍可用）
            kb_excerpts = ""

    stage_label = STAGE_LABEL.get(pen_snapshot["batch_stage"] or "", pen_snapshot["batch_stage"] or "未设置")
    ctx_text = (
        f"未定位圈舍 · {species_name(species_code)}群（按物种通用口径，未接入本圈存栏/批次）"
        if pen_unresolved
        else f"{pen_snapshot['pen_name']} · {stage_label} · "
        f"公{pen_snapshot['male_count']}/母{pen_snapshot['female_count']}"
        f"{'（含母畜）' if has_female else ''} · 体重：未称重"
    )
    if individual:
        # 让模型与台账都知道"这次问的是哪一头"：性别/日龄会改变鉴别范围（阉割、配种、哺乳）
        bits = [f"{'公' if individual['gender'] == 'male' else '母'}"]
        if individual["age_days"] is not None:
            bits.append(f"{individual['age_days']} 日龄")
        if individual["status"] != "active":
            bits.append(f"状态 {individual['status']}")
        ctx_text += f" · 个体 {individual['ear_tag_no']}（{' · '.join(bits)}）"

    out: dict[str, Any] = {
        "pen": pen_snapshot,
        "individual": individual,
        "pen_context_text": ctx_text,
        "clarify": False,
        "pen_unresolved": pen_unresolved,
        "clarify_text": followup,
        "not_applicable": not_applicable,
        "rag_docs": matched,
        "kb_excerpts": kb_excerpts,
        "kb_empty_text": "",
    }

    if not matched:
        # EX-C4：命中母畜专属病但本圈不含母畜 → 明确说明不适用，不硬套
        if not_applicable:
            names = "、".join(e["disease_name"] for e in not_applicable)
            out["not_applicable_text"] = (
                f"该症状对应母畜专属疾病（{names}），而 {pen_snapshot['pen_name']} 的性别构成不含母畜，"
                f"不适用于本圈排查。如症状确实存在，请补充描述或联系执业兽医现场查看。"
            )
        else:
            # EX-04：检索无结果明确拒答（50002），禁止编造
            mentioned = next(
                ((c, w) for c, w in (("pig", "猪"), ("sheep", "羊"), ("cattle", "牛")) if w in eff_message), None
            )
            label = species_name(species_code)
            if mentioned and mentioned[0] != species_code:
                out["kb_empty_text"] = (
                    f"您描述的是{species_name(mentioned[0])}的症状，但当前问诊圈舍「{pen_snapshot['pen_name']}」"
                    f"是{label}群，知识库按物种隔离检索、不跨物种匹配。请切换到{species_name(mentioned[0])}圈舍后"
                    f"重新问诊，或直接描述{label}群的症状。"
                )
            else:
                out["kb_empty_text"] = (
                    f"知识库暂无该病症资料（当前圈舍：{pen_snapshot['pen_name']} · {label}），"
                    f"建议联系执业兽医现场诊断。"
                )
        out["hit_keywords"] = []
    else:
        out["hit_keywords"] = hit_keywords_of(matched[0], eff_message)
        # 徽章与答案同源（见 diagnose 里的 C-22 说明）；diagnose 重排后会再算一次
        out["risk_level"] = max_risk(matched[:2])
    return out


# =====================================================================
# 出口节点 B：圈舍澄清（EX-C1，写澄清槽位供下一轮合并）
# =====================================================================

async def clarify_reply(state: ConsultState) -> dict[str, Any]:
    text = state["clarify_text"]
    stream_text(text)
    async with SessionLocal() as db:
        session = await db.get(AgentSession, state["session_id"])
        if session is not None:
            session.pending_clarify = {
                "msg": state["eff_message"],
                "at": int(time.time() * 1000),
                "orig_msg": state["message"],
            }
        await _persist_round(db, state, content=text, meta={"clarify": True, "intent": "consult"})
        await db.commit()
    done = {"session_id": state["session_id"], "clarify": True}
    emit("done", done)
    return {"done": done}


# =====================================================================
# 出口节点 C：EX-04 检索无结果拒答 / EX-C4 母畜专属不适用
# =====================================================================

async def kb_reply(state: ConsultState) -> dict[str, Any]:
    if state.get("kb_empty_text"):
        text = state["kb_empty_text"]
        async with SessionLocal() as db:
            await _persist_round(db, state, content=text, meta={"code": 50002, "intent": "consult"})
            await db.commit()
        emit("error", {"code": 50002, "message": text})
        done = {"session_id": state["session_id"]}
        emit("done", done)
        return {"done": done, "error": {"code": 50002, "message": text}}

    text = state.get("not_applicable_text") or "知识库暂无该病症资料，建议联系执业兽医现场诊断。"
    stream_section("diagnosis", "诊断", text)
    async with SessionLocal() as db:
        await _persist_round(
            db, state, content=text, meta={"intent": "consult"}, sections=[{"title": "诊断", "text": text}]
        )
        await db.commit()
    done = {"session_id": state["session_id"]}
    emit("done", done)
    return {"done": done}


# =====================================================================
# 节点 3：诊断（LLM 主导 — 知识库候选白名单内判定主疑似病；失败回退规则正文）
# =====================================================================

async def diagnose(state: ConsultState) -> dict[str, Any]:
    """升级口径（V0.4）：LLM 在检索候选内完成症状理解 + 主疑似判定 + 鉴别 + 饲养建议，
    节点据 primary 重排 rag_docs——用药校验/台账/复核单/HITL 均随主疑似走，下游零改动。

    确定性护栏不变（附录 A 工程保证）：
    - 候选白名单：疾病名只能来自 rag_docs（prompts 层二次过滤，不依赖模型自觉）；
    - 风险分级：取主疑似自身的 `risk_level`（确定性推导，模型不得改判；见下方 C-22 说明）；
    - 用药：medication_check 查兽药库生成，模型输出不含药名；
    - EX-03：LLM 失败静默回退规则正文（原口径），链路不中断。
    """
    matched = state["rag_docs"]
    top = matched[0]
    others = matched[1:2]
    hit_words = state.get("hit_keywords") or []
    model: str | None = None
    primary: str | None = None
    llm_body: str | None = None
    try:
        res = await llm_diagnose(
            message=state["eff_message"],
            species_code=state["pen"]["species_code"],
            ctx_text=state["pen_context_text"],
            hit_keywords=hit_words,
            rag_docs=matched,
            kb_excerpts=state.get("kb_excerpts") or "",
            history=state.get("history"),
        )
        model = res["model"]
        primary = res["primary"]
        llm_body = res["text"]
    except LLMError:
        llm_body = None  # EX-03 静默回退：规则正文（原口径）

    # 主疑似重排：LLM 选定者提到首位——medication_check/record/hitl_gate 读 rag_docs[0]，
    # 重排后用药、台账、复核单自动跟随主疑似，下游节点零改动。
    # 白名单二次校验（节点层兜底，不信任 prompts 层已过滤的假设——附录 A 防幻觉双保险）：
    # primary 不在候选内则保持检索原序，走规则口径。
    if (
        primary
        and primary != top["disease_name"]
        and any(e["disease_name"] == primary for e in matched)
    ):
        matched = [next(e for e in matched if e["disease_name"] == primary)] + [
            e for e in matched if e["disease_name"] != primary
        ]
        top = matched[0]
        others = [e for e in matched if e["disease_name"] != primary][:1]

    # C-22 收口（2026-10-16）：风险等级取「答案里真正呈现给用户的疾病集合」的最高级 =
    # 主疑似 + 展示鉴别（另需排查那一条），既不按 top5 乱抬、也不会漏抬已展示的更危重鉴别。
    # 理由是内部一致性：用药校验、台账、HITL 复核单全部按 rag_docs[0] 走，唯独徽章按
    # top5 的 max 算，于是会出现「正文说疑似 A（mid）、徽章写 high 并因此开一张兽医复核单」；
    # 而且旧实现的 max 是在主疑似重排**之前**算的，重排换了主疑似、徽章从不跟着改。
    # 黄金集三口径实测（94 例，同一批 embedding）：
    #   set(top5 max)=0.8413（7 例偏差全是 mid→high，凭空增兽医工作量）
    #   top1(主疑似)  =0.9683 / shown(主疑似+展示鉴别)=0.9683 —— 数值相同，
    #   但 shown 只可能往上抬、不可能比 top1 更低，故取 shown。
    # 复算命令：python scripts/eval_consult.py --mode vector --risk-from shown
    risk = max_risk([top, *others])
    risk_text = {"low": "low（低风险）", "mid": "mid（中度风险）", "high": "high（高风险）"}[risk]
    high_note = "\n本病例风险等级为高，已转交兽医复核，复核前请勿自行用药。" if risk == "high" else ""
    diagnosis_source = top["source"]

    if llm_body is None:
        mode = "rule"
        diagnosis_text = (
            f"疑似疾病：{top['disease_name']}"
            f"{'；另需排查：' + '、'.join(e['disease_name'] for e in others) if others else ''}\n"
            f"匹配症状：{'、'.join(hit_words) if hit_words else '描述要点'}\n"
            f"风险等级：{risk_text}{high_note}\n"
            f"圈舍上下文：{state['pen_context_text']}\n"
            f"{RULE_DIAGNOSIS_DISCLAIMER}"
        )
    else:
        mode = "llm"
        diagnosis_text = (
            f"{llm_body}\n"
            f"风险等级：{risk_text}{high_note}\n"
            f"圈舍上下文：{state['pen_context_text']}\n"
            f"{LLM_DIAGNOSIS_DISCLAIMER}"
        )

    stream_section("diagnosis", "诊断", diagnosis_text, diagnosis_source)

    return {
        "rag_docs": matched,
        "risk_level": risk,
        "diagnosis_text": diagnosis_text,
        "ai_analysis_text": None,
        "diagnosis_source": diagnosis_source,
        "diagnosis_mode": mode,
        "llm_model": model,
    }


# =====================================================================
# 节点 4：用药校验（兽药库 + 违禁拦截 + 休药期强制输出）
# =====================================================================

async def medication_check(state: ConsultState) -> dict[str, Any]:
    matched = state["rag_docs"]
    species_code = state["pen"]["species_code"]
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(MedicineLib).where(
                    MedicineLib.species_code == species_code, MedicineLib.is_forbidden.is_(False)
                )
            )
        ).scalars().all()
    lib = {m.med_name: m for m in rows}

    lines: list[str] = []
    med_names: list[str] = []
    max_withdrawal = 0
    for entry in matched[:2]:
        for name in entry["med_names"]:
            med = lib.get(name)
            if med is None:
                continue  # 库外/违禁药一律不输出（TC-G07 底线）
            lines.append(
                f"{med.med_name}（对应 {entry['disease_name']}）\n"
                f"用法用量：{med.usage}\n"
                f"休药期：{med.withdrawal_period} 天（自停药日起算，未满休药期不得出栏屠宰）\n"
                f"用药禁忌：{med.contraindication or '按标签说明'}"
            )
            med_names.append(med.med_name)
            max_withdrawal = max(max_withdrawal, med.withdrawal_period)

    if lines:
        med_text = (
            "\n\n".join(lines)
            + f"\n休药期提示：本场建议最短休药期 {max_withdrawal} 天，期间{species_name(species_code)}不得出栏。"
            + "\n系统建议不可替代执业兽医处方。"
        )
    else:
        med_text = (
            "该病以隔离、支持治疗与兽医现场处置为主，兽药库内无适用治疗药物，请勿自行用药。"
            "\n系统建议不可替代执业兽医处方。"
        )
    stream_section("medication", "用药建议", med_text, MED_SOURCE)

    top = matched[0]
    nutrition_text = (
        f"饲喂管理建议：\n- {top['feed_advice']}\n"
        f"- 详细日粮配方（原料比例/成本核算/替代方案）功能即将上线。\n依据来源：{top['source']}"
    )
    stream_section("nutrition", "饲料建议", nutrition_text, top["source"])
    return {
        "medication_text": med_text,
        "nutrition_text": nutrition_text,
        "med_names": med_names,
        "max_withdrawal": max_withdrawal,
    }


# =====================================================================
# 节点 5：record — 落会话消息 + 台账 + 高危建复核单（必须在 interrupt 之前完成）
# =====================================================================

async def record(state: ConsultState) -> dict[str, Any]:
    risk = state.get("risk_level") or "low"
    top = state["rag_docs"][0]
    pen = state["pen"]
    individual = state.get("individual")
    risk_label = {"low": "低", "mid": "中", "high": "高"}[risk]
    # 段落顺序与流式帧严格一致（前端历史回看 = 直播所见）
    sections: list[dict[str, Any]] = [
        {"title": "诊断", "text": state["diagnosis_text"], "source": state["diagnosis_source"]},
        {"title": "用药建议", "text": state["medication_text"], "source": MED_SOURCE},
        {"title": "饲料建议", "text": state["nutrition_text"], "source": state["diagnosis_source"]},
    ]
    # 先答后问：结论已给完，末节才追问圈舍。高危要补一句——复核单需要真 pen_id，
    # 本轮建不了（farm_id/pen_id 是 NOT NULL 外键），等补位那一轮重诊时再建
    unresolved = bool(state.get("pen_unresolved"))
    followup = state.get("clarify_text") or ""
    if unresolved and followup:
        if risk == "high":
            followup += "\n本病例风险等级为高：定位到圈舍后系统才会转交执业兽医复核，请尽快补充。"
        stream_section("followup", "补充圈舍", followup)
        sections.append({"title": "补充圈舍", "text": followup, "source": "系统提示"})
    content = "\n\n".join(s["text"] for s in sections)
    # 个体引用同时进消息 meta 与 done 帧：历史回看要能看出这轮问的是那头，
    # 前端"记入个体档案"按钮也靠它预选（缺了就得重新搜耳标）
    ind_ref = (
        {"individual_id": individual["individual_id"], "ear_tag_no": individual["ear_tag_no"], "pen_name": pen["pen_name"]}
        if individual and not unresolved
        else None
    )

    async with SessionLocal() as db:
        message_id = await _persist_round(
            db, state, content=content,
            meta={
                "risk_level": risk,
                "intent": "consult",
                **({"pen_unresolved": True} if unresolved else {}),
                **({"individual": ind_ref} if ind_ref else {}),
            },
            sections=sections,
        )

        # 复核单先建：disease 台账要用真列 vet_review_id 指过去（02-数据库 3.6 溯源口径）
        review_id: int | None = None
        disease_record: LedgerRecord | None = None
        if unresolved:
            # vet_reviews / ledger_records 的 farm_id+pen_id 都是 NOT NULL 外键，
            # 未定位圈舍时没有可挂的主体：只落会话消息 + 写澄清槽位，
            # 复核单与台账留到用户补位那一轮重诊时按真圈舍建（不造 pen_id=0 的幽灵行）
            session = await db.get(AgentSession, state["session_id"])
            if session is not None:
                session.pending_clarify = {
                    "msg": state["eff_message"],
                    "at": int(time.time() * 1000),
                    "orig_msg": state["message"],
                }
        else:
            tag = f" {individual['ear_tag_no']}" if individual else ""
            if risk == "high":
                review = VetReview(
                    alert_id=0,  # 问诊高危病例无 alert_log 可挂（F05 FR-09 放宽口径）
                    session_id=state["session_id"],
                    message_id=message_id,
                    farm_id=pen["farm_id"],
                    pen_id=pen["pen_id"],
                    thread_id=state["thread_id"],  # Command(resume) 的定位键
                    # 耳标写进主诉：复核单没有 individual_id 列，兽医排队时得知道是那头
                    original_diagnosis=f"{top['disease_name']}{tag}（{state['eff_message']}）",
                    risk_level=risk,
                    review_status="pending",
                )
                db.add(review)
                await db.flush()
                review_id = review.review_id

            disease_ext: dict[str, Any] = {
                "disease_name": top["disease_name"],
                "risk_level": risk,
                "source": state["diagnosis_source"],
                "intent": "consult",
                "session_id": state["session_id"],
                "thread_id": state["thread_id"],
                "diagnosis_mode": state.get("diagnosis_mode") or "rule",
                **({"llm_model": state["llm_model"]} if state.get("llm_model") else {}),
                **({"vision_signs": state["vision_signs"]} if state.get("vision_signs") else {}),
                # F14：绑定个体时台账要能追溯到那头（record_ext 的 tag_no 也是台账动态列的既有键）
                **({"tag_no": individual["ear_tag_no"], "individual_id": individual["individual_id"]} if individual else {}),
            }
            disease_record = LedgerRecord(
                farm_id=pen["farm_id"],
                pen_id=pen["pen_id"],
                record_type="disease",
                summary=(
                    f"{pen['pen_name']}{tag} AI 问诊疑似 {top['disease_name']}（风险{risk_label}）"
                    f"· 会话#{state['session_id']}"
                ),
                vet_review_id=review_id or 0,
                record_ext=disease_ext,
            )
            db.add(disease_record)
            if state.get("med_names"):
                db.add(
                    LedgerRecord(
                        farm_id=pen["farm_id"],
                        pen_id=pen["pen_id"],
                        record_type="medicine",
                        summary=f"{pen['pen_name']} AI 用药建议：{'、'.join(state['med_names'])}",
                        record_ext={
                            "med_suggest": "、".join(state["med_names"]),
                            "max_withdrawal_days": state.get("max_withdrawal", 0),
                            "session_id": state["session_id"],
                        },
                    )
                )
        await db.commit()

    done = {
        "session_id": state["session_id"],
        "risk_level": risk,
        "vet_review_created": review_id is not None,
    }
    if unresolved:
        done["pen_unresolved"] = True
    if ind_ref:
        done["individual"] = ind_ref
    emit("done", done)
    return {
        "assistant_message_id": message_id,
        "ledger_record_id": disease_record.record_id if disease_record else 0,
        "review_id": review_id,
        "done": done,
    }


# =====================================================================
# 节点 6：hitl_gate — 全系统唯一中断点（节点内零副作用）
# =====================================================================

def hitl_gate(state: ConsultState) -> dict[str, Any]:
    """高危病例挂起等兽医复核（PRD 3.5.8 / 01-架构 9.4）。

    低中风险直接穿过。interrupt() 之后由 POST /vet/review 用 Command(resume=复核结果) 唤醒。
    **本节点不得写库**：resume 时节点从头重跑，副作用会执行两次（spike 实测）。
    """
    if state.get("risk_level") != "high":
        return {}
    result = interrupt(
        {
            "reason": "high_risk_consult",
            "review_id": state.get("review_id"),
            "session_id": state.get("session_id"),
            "risk_level": "high",
            "original_diagnosis": state["rag_docs"][0]["disease_name"] if state.get("rag_docs") else "",
        }
    )
    return {"review_result": result}


# =====================================================================
# 节点 7：record_review — resume 后终态化 + 落 vet_review 留痕台账
# =====================================================================

async def record_review(state: ConsultState) -> dict[str, Any]:
    result = state.get("review_result") or {}
    action = result.get("action")
    review_id = state.get("review_id")
    if not review_id or action not in ("approve", "reject"):
        return {}

    async with SessionLocal() as db:
        review = await db.get(VetReview, review_id)
        if review is None or review.review_status != "pending":
            return {}
        pen = await db.get(Pen, review.pen_id)
        pen_name = pen.pen_name if pen else ""
        cost_hours = round((time.time() - review.create_time.timestamp()) / 3600, 1) if review.create_time else 0.0

        review.review_status = "approved" if action == "approve" else "rejected"
        review.vet_user_id = int(result.get("vet_user_id") or 0) or None
        review.review_opinion = str(result.get("review_opinion") or "")
        review.revised_diagnosis = str(result.get("revised_diagnosis") or "")
        review.revised_med = str(result.get("revised_med") or "")
        review.review_cost_hours = cost_hours
        review.review_time = datetime.now(timezone.utc)

        opinion = review.review_opinion
        original = review.original_diagnosis
        record_ext = {
            "review_id": review_id,
            "review_action": action,
            "review_opinion": opinion,
            "review_cost_hours": cost_hours,
            "vet_user_id": review.vet_user_id,
            "original_diagnosis": original,
            "session_id": state["session_id"],
            "thread_id": state["thread_id"],
        }
        if action == "reject":
            record_ext["revised_diagnosis"] = review.revised_diagnosis
            record_ext["revised_med"] = review.revised_med
            summary = (
                f"{pen_name} 兽医复核修正：{original[:40]} → {review.revised_diagnosis[:40]}"
                f"（用药：{review.revised_med[:60]}）"
            )
        else:
            summary = f"{pen_name} 兽医复核确认：{original[:40]}" + (f" · 意见：{opinion[:60]}" if opinion else "")

        ledger = LedgerRecord(
            farm_id=review.farm_id,
            pen_id=review.pen_id,
            record_type="vet_review",
            summary=summary,
            alert_id=review.alert_id,
            vet_review_id=review_id,
            record_ext=record_ext,
        )
        db.add(ledger)
        await db.commit()
        ledger_id = ledger.record_id

    return {"review_ledger_id": ledger_id}


# =====================================================================
# 路由函数
# =====================================================================

# =====================================================================
# 出口节点 D：data_query 子图（四把只读工具白名单，数值 100% 来自查询结果）
# =====================================================================

async def data_query_dispatch(state: ConsultState) -> dict[str, Any]:
    from app.agents import tools as dq

    async with SessionLocal() as db:
        user = await db.get(User, state["user_id"])
        slots = await dq.extract_slots(db, user, state["eff_message"])
        if slots["denied"]:
            text = "该圈舍不属于您的账号，您只能查询自己养殖场的数据。"
            stream_text(text)
            await _persist_round(db, state, content=text, meta={"intent": "data_query", "denied": True}, intent="data_query")
            await db.commit()
            done = {"session_id": state["session_id"], "denied": True}
            emit("done", done)
            return {"done": done}
        if slots["pen_not_found"]:
            pens = await _list_my_pens(db, state["user_id"])
            names = "、".join(p.pen_name for p in pens[:5])
            text = f"没有找到您提到的圈舍。您当前的圈舍：{names or '（暂无，请先到【场舍管理】建圈）'}。"
            stream_text(text)
            await _persist_round(db, state, content=text, meta={"intent": "data_query"}, intent="data_query")
            await db.commit()
            done = {"session_id": state["session_id"]}
            emit("done", done)
            return {"done": done}
        # 传感器工具必须有圈舍：单圈自动用，多圈则澄清（与问诊同一圈舍定位纪律）
        if slots["tool"] == "sensor_digest" and not slots["pen_id"]:
            pens = await _list_my_pens(db, state["user_id"])
            if len(pens) > 1:
                text = "您有多个圈舍，请问要查哪个的传感器数据？可以说圈舍名称或编号（如“3号圈”）。"
                stream_text(text)
                await _persist_round(db, state, content=text, meta={"clarify": True, "intent": "data_query"}, intent="data_query")
                await db.commit()
                done = {"session_id": state["session_id"], "clarify": True}
                emit("done", done)
                return {"done": done}
            if len(pens) == 1:
                slots["pen_id"] = pens[0].pen_id
        res = await dq.run_tool(db, user, slots)
        text, title, source = dq.build_answer(slots["tool"], res, slots)

    stream_section("data_query", title, text, source)
    async with SessionLocal() as db:
        await _persist_round(
            db, state, content=text, meta={"intent": "data_query"},
            sections=[{"title": title, "text": text, "source": source, "tool": slots["tool"]}], intent="data_query",
        )
        await db.commit()
    done = {"session_id": state["session_id"]}
    emit("done", done)
    return {"done": done}


# =====================================================================
# 出口节点 E：weather 子图（MCP 优先 → 降级缓存/直调取数 → 风险规则 → 建议；不编造天气）
# =====================================================================

async def weather_answer(state: ConsultState) -> dict[str, Any]:
    from app.core.config import settings
    from app.services import weather as wx

    async with SessionLocal() as db:
        farm = await wx.primary_farm(db, state["user_id"])
        if farm is None:
            text = "还没有养殖场信息：请先在「场舍管理」创建养殖场，气象服务将按场所在地区提供养殖专属风险提醒。"
        elif not (farm.adcode or "").strip():
            text = "请先完善养殖场所在地区（场舍管理 → 编辑养殖场，选择省市区），完善后即可查看本地气象与养殖风险提醒。"
        else:
            # MCP 优先：weather-mcp 服务已配置时经 MCP 动态调用天气工具（client.py 失败返回 None）
            result = None
            if settings.weather_mcp_url:
                from app.mcp_weather.client import fetch_weather_via_mcp

                payload = await fetch_weather_via_mcp(farm.adcode, settings.weather_mcp_url)
                if payload is not None:
                    await wx.save_cache(db, farm.adcode, payload)  # 缓存口径与直调链路共用
                    result = {"ok": True, "source": "mcp", "degraded": False, "payload": payload}
            if result is None:  # 未配置 MCP / MCP 不可用 → 原直调链路（缓存 6h / 降级 / 50003）
                result = await wx.resolve_weather_for_farm(db, farm)
            if not result["ok"]:
                text = "气象服务暂不可用，请稍后重试。"  # EX-02 无缓存分支
            else:
                payload = result["payload"]
                days = "；".join(
                    f"{d['date'][5:].replace('-', '/')} {d['text_day']} {d['temp_min']:g}~{d['temp_max']:g}℃"
                    for d in payload.get("forecast") or []
                )
                items = await wx.generate_climate_alerts(db, farm, payload)
                text = f"{farm.farm_name} 未来 3 天：{days}。"
                if items:
                    top = max(items, key=lambda x: {"low": 0, "mid": 1, "high": 2}[x["level"]])
                    text += (
                        f"当前风险：{top.get('warning_title') or top['title']}——{top['impact']}"
                        f"（{top['data_hint']}）。建议：{'；'.join(top['advice'][:2])}。"
                    )
                else:
                    text += "未来 3 天无养殖气候风险，正常安排饲喂与巡查即可。"
                if result["degraded"]:
                    text += f"（数据更新于 {wx.cache_age_hours(payload)} 小时前，供参考）"

    stream_text(text)
    async with SessionLocal() as db:
        await _persist_round(db, state, content=text, meta={"intent": "weather"}, intent="weather")
        await db.commit()
    done = {"session_id": state["session_id"]}
    emit("done", done)
    return {"done": done}


def route_after_intent(state: ConsultState) -> str:
    if state.get("guard_text") or state.get("write_rejected") or state["intent"] != "consult":
        if state["intent"] == "data_query" and not state.get("guard_text"):
            return "data_query_dispatch"
        if state["intent"] == "weather" and not state.get("guard_text"):
            return "weather_answer"
        return "fallback_reply"
    # 带图轮先过视觉征象描述，再把征象词交给检索（架构 4.6：vision_describe 在子图入口）
    return "vision_describe" if state.get("image") else "load_context_retrieve"


def route_after_context(state: ConsultState) -> str:
    if state.get("clarify"):
        return "clarify_reply"
    if state.get("kb_empty_text") or state.get("not_applicable_text"):
        return "kb_reply"
    return "diagnose"


def route_after_record(state: ConsultState) -> str:
    # 未定位圈舍不挂起：本轮没建复核单（pen_id 是 NOT NULL 外键），interrupt 会把
    # thread 永久挂在无人可恢复的状态上，且 prune_checkpoints 只清"非 pending 复核"的 checkpoint
    if state.get("risk_level") == "high" and not state.get("pen_unresolved"):
        return "hitl_gate"
    return "__end__"
