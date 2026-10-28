"""知识库检索层 — kb_disease 集合的切真前形态（01-架构 10.2 / PRD 3.5.1）。

数据来源：`kb_seed.json`，由 `backend/scripts/export_kb_seed.ts` 从前端
`src/lib/knowledge-data.ts` 机械导出（32 条：猪 18 / 羊 8 / 牛 6），保证前后端同源逐值一致。
导出脚本同时自检"每条推荐药都在兽药库种子内且物种一致"。

检索实现：**向量优先（B5 已落地）**——search_diseases_smart 走 Milvus 语义检索
（embedding + species_code metadata 过滤 + 相似度阈值），失败/无命中回退关键词计分
（top_k=5，内置种子 + F16 上传文档同池）。检索结果 → rag_docs → llm_diagnose prompt，
即"大模型从知识库取知识"的完整链路。

纪律（PRD 附录 A 约束 2 / EX-04）：
- 检索无结果 → 明确拒答，禁止模型自由发挥；
- 母畜专属病仅在含母畜的圈舍输出（TC-G09）；
- 风险分级取知识库条目的 risk_level，**不由模型判定**。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_SEED_PATH = Path(__file__).resolve().parent / "kb_seed.json"

RISK_ORDER = {"low": 1, "mid": 2, "high": 3}

SPECIES_NAME = {"pig": "猪", "sheep": "羊", "cattle": "牛"}

STAGE_LABEL = {
    "nursing": "哺乳期",
    "weaning": "保育期",
    "growing": "育成期",
    "fattening": "育肥期",
    "gestation": "妊娠期",
    "lactation": "泌乳期",
}

# 违禁药诱导拦截词（TC-G07：任何形态提及一律不提供使用信息，农业农村部公告第 250 号）
FORBIDDEN_MENTIONS = ["瘦肉精", "克伦特罗", "沙丁胺醇", "莱克多巴胺", "氯霉素", "呋喃唑酮"]

FORBIDDEN_REPLY = (
    "「{word}」属于违禁药品（食品动物中禁止使用，农业农村部公告第 250 号），"
    "系统不会提供任何使用建议。请咨询执业兽医选择合规治疗方案。"
    "系统建议不可替代执业兽医处方。"
)

# 问诊示例（unknown 兜底话术按主物种动态取用，与 mock 逐字一致以免回归断言失效）
CONSULT_EXAMPLE = {"pig": "小猪咳嗽不吃食", "sheep": "羊咳嗽不吃食", "cattle": "牛咳嗽流鼻涕"}


def species_name(code: str | None) -> str:
    return SPECIES_NAME.get(code or "pig", "猪")


@lru_cache(maxsize=1)
def load_entries() -> tuple[dict[str, Any], ...]:
    """加载知识条目（进程内缓存；管理员上传文档触发的重建索引在 B5 随 Milvus 落地）。"""
    raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    return tuple(raw["entries"])


def doc_to_kb_entry(doc: Any) -> dict[str, Any]:
    """KbDocument（F16 管理员上传文档）→ 检索条目，与 kb_seed 内置条目同构、同池计分。

    口径（与前端 mock searchDiseases extraEntries 一致）：
    - species_code 为空 = 公共文档，全物种适用；
    - nutrition 文档不参与风险分级（risk 压为 low）且不带推荐药（med_names 清空）；
    - disease 文档的 med_names 已在上传时过兽药库非违禁校验，输出时仍过 medication_check 兜底。
    """
    is_disease = doc.doc_type == "disease"
    return {
        "disease_name": doc.doc_name,
        "risk_level": doc.risk_level if is_disease else "low",
        "symptom_keywords": list(doc.retrieval_keywords or []),
        "source": doc.source or f"知识库文档#{doc.doc_id}",
        "med_names": list(doc.med_names or []) if is_disease else [],
        "feed_advice": doc.feed_advice or f"详见知识库文档《{doc.doc_name}》",
        "female_only": False,
        "species_code": doc.species_code or "",  # 空=公共
        "doc_id": doc.doc_id,
        "from_kb_doc": True,
    }


def search_diseases(
    message: str,
    species_code: str,
    has_female: bool,
    top_k: int = 5,
    extra_entries: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按物种过滤后关键词计分检索（内置种子 + F16 上传文档同池）。

    返回 (matched, not_applicable)：
    - matched：命中且适用当前圈舍性别构成的条目，按得分降序，最多 top_k；
    - not_applicable：命中但为母畜专属病且本圈不含母畜（EX-C4 提示用）。
    物种过滤：条目 species_code 为空视为公共文档（全物种适用），否则必须与圈舍物种一致。
    """
    matched: list[tuple[int, dict[str, Any]]] = []
    not_applicable: list[tuple[int, dict[str, Any]]] = []
    for entry in [*load_entries(), *extra_entries]:
        entry_species = entry.get("species_code") or ""
        if entry_species and entry_species != species_code:
            continue  # 物种隔离，不跨命中（F12 FR-05 / EX-F2）；空=公共文档放行
        score = sum(1 for kw in entry["symptom_keywords"] if kw in message)
        if score <= 0:
            continue
        if entry.get("female_only") and not has_female:
            not_applicable.append((score, entry))
        else:
            matched.append((score, entry))
    matched.sort(key=lambda x: -x[0])
    not_applicable.sort(key=lambda x: -x[0])
    return [e for _, e in matched[:top_k]], [e for _, e in not_applicable[:top_k]]


def max_risk(entries: list[dict[str, Any]]) -> str:
    """取命中条目中的最高风险等级（诊断分级口径，确定性推导）。"""
    level = "low"
    for e in entries:
        if RISK_ORDER.get(e["risk_level"], 0) > RISK_ORDER[level]:
            level = e["risk_level"]
    return level


async def search_diseases_smart(
    message: str,
    species_code: str,
    has_female: bool,
    top_k: int = 5,
    extra_entries: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """B5 语义检索入口：向量优先（Milvus + metadata 物种过滤），失败/无命中回退关键词计分。

    向量路径输出与关键词路径同构（含 symptom_keywords，hit_keywords_of 可用），
    调用方（load_context_retrieve → diagnose）零感知切换。
    降级纪律（EX-03 同款）：Milvus 不可用 / embedding 失败 / 相似度全部低于阈值
    任一情况，静默回退关键词路径，检索层永不阻塞问诊链路。
    """
    from app.agents.vector_store import search_kb  # 局部导入避免 kb↔vector_store 环
    from app.services.embedding import EmbeddingError, embed_texts

    try:
        vector = (await embed_texts([message]))[0]
        hits = await search_kb(vector, species_code, top_k)
        # 关键词交集门：向量通路也要至少有一个 symptom_keywords 字面出现在主诉里才算命中，
        # 与 `search_diseases` 的 `score > 0` 判据同构（本函数 docstring 承诺的"调用方零感知
        # 切换"此前不成立）。缺这一道时 30 条负例（健康描述/无关话）全部返回命中，
        # EX-04 拒答永不触发；golden set 实测 false-recall 1.0 → 0.0（docs/08 C-19）。
        kept = [h for h in hits if any(k in message for k in (h.get("symptom_keywords") or []))]
        if kept:
            matched = [h for h in kept if not h.get("female_only") or has_female]
            not_applicable = [h for h in kept if h.get("female_only") and not has_female]
            return matched[:top_k], not_applicable[:top_k]
    except Exception:  # noqa: BLE001 — Milvus/Embedding 任何异常都走关键词兜底
        pass
    return search_diseases(message, species_code, has_female, top_k, extra_entries)


def hit_keywords_of(entry: dict[str, Any], message: str, limit: int = 3) -> list[str]:
    return [kw for kw in entry["symptom_keywords"] if kw in message][:limit]
