"""Agent 状态定义 — 主图公共字段 + 问诊子图私有状态（01-架构 9.2）。

设计取舍（与目标架构的一处偏离，已记录）：
- **thread_id 按"轮"而非按"会话"**：spike 实测同一 thread 的 run 结束后再次 invoke 会从 START
  重跑；若第二轮问题落在一个尚未被兽医复核恢复的 thread 上，会撞坏挂起的 checkpoint。
  因此每轮一个新 thread_id，多轮上下文由 API 层从 agent_messages 显式装配后放进 state，
  不依赖 checkpoint 累积消息（checkpoint 只承担"可恢复"职责，不承担"记忆"职责）。
- **不写库进 state**：所有 DB 访问在节点内用 SessionLocal 自持会话，state 只放可序列化数据
  （checkpoint 要 JSON 序列化落 PostgreSQL，塞 ORM 对象会炸）。
"""

from __future__ import annotations

from typing import Any, TypedDict


class ConsultState(TypedDict, total=False):
    """问诊链路状态（主图公共字段 + 子图私有字段合一，V1.0 只有问诊子图实装）。"""

    # ----- 输入（API 层装配）-----
    user_id: int
    session_id: int
    thread_id: str
    message: str              # 用户原话（落库/展示/审计）
    pen_id: int | None        # 请求显式指定的圈舍（已过归属校验）
    individual_id: int | None  # F14 绑定的个体（已过归属校验；绑定时以该个体所在圈定位）
    image: str | None         # FR-11 图像问诊 data URL
    main_species_code: str    # 账号主物种（兜底话术动态化）
    history: list[dict[str, str]]  # 多轮上下文 [{role, content}]

    # ----- intent_recognize -----
    eff_message: str          # 合并澄清槽位后的实际处理文本
    intent: str               # consult/feed/data_query/weather/unknown
    intent_source: str        # rule/llm（成本可观测：rule 命中即省一次 LLM 调用）
    write_rejected: bool      # F11 写指令兜底拒绝
    guard_text: str           # 违禁药诱导拦截话术（非空即终止链路）

    # ----- vision_describe（FR-11 图像问诊）-----
    vision_signs: list[str]   # 视觉模型给出的可见征象词，并入检索文本用

    # ----- load_context_retrieve -----
    pen: dict[str, Any] | None      # 定位到的圈舍快照（pen_id/pen_name/farm_id/species_code/公母/阶段）
    individual: dict[str, Any] | None  # 绑定个体快照（individual_id/ear_tag_no/gender/birth_date/status）
    pen_context_text: str
    pen_unresolved: bool            # 有场有圈但没定位到 → 先按物种通用口径作答，末尾追问圈舍
    clarify: bool                   # 无场无圈等必须前置澄清 → 反问（不再用于"没定位到圈舍"）
    clarify_text: str               # 追问文案（clarify 轮直接输出；未定位轮由 record 作末节追加）
    rag_docs: list[dict[str, Any]]  # 命中条目（含 source）；diagnose 后按 LLM 主疑似重排
    kb_excerpts: str                # 命中条目的原文节选（≤3000 字，检索节点写、诊断节点读）
    hit_keywords: list[str]
    not_applicable: list[dict[str, Any]]  # 母畜专属病但本圈无母畜（EX-C4）
    not_applicable_text: str        # EX-C4 提示文案
    kb_empty_text: str              # EX-04 检索无结果的拒答文案（非空即终止链路）

    # ----- diagnose -----
    risk_level: str                 # low/mid/high（由知识库条目确定性推导，模型不得改判）
    diagnosis_text: str             # 诊断正文（LLM 主导生成；LLM 失败时为规则正文）
    diagnosis_mode: str             # llm/rule（可观测：本轮诊断由 LLM 主导还是规则兜底）
    ai_analysis_text: str | None    # 历史字段（旧"规则正文 + AI 补充段"两段式的第二段），LLM 主导后恒空、已无消费方，仅为旧 checkpoint 反序列化保留
    diagnosis_source: str
    llm_model: str | None           # LLM 主导成功时记录模型名（可观测）

    # ----- medication_check -----
    medication_text: str
    nutrition_text: str
    med_names: list[str]
    max_withdrawal: int

    # ----- record / hitl -----
    assistant_message_id: int
    ledger_record_id: int
    review_id: int | None           # 高危病例的复核单
    review_result: dict[str, Any] | None  # interrupt 恢复后由 Command(resume=...) 注入
    review_ledger_id: int | None    # 复核留痕台账

    # ----- 出口 -----
    done: dict[str, Any]            # done 帧 data
    error: dict[str, Any] | None    # error 帧 data（code/message）
