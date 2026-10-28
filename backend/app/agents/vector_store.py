"""Milvus 向量库 — B5 批次：kb 条目灌库 + 语义检索（01-架构 10.2 终态）。

集合 `kb_entries`：一条 = 一个知识条目（内置种子或 F16 上传文档），
文本 = 病名 + 症状关键词（embedding 输入），其余条目结构全部进 metadata 字段——
search 输出与 kb_seed 条目同构，diagnose 节点零改动。

纪律：
- 物种隔离用 metadata 过滤（species_code 空 = 公共文档全物种放行），不靠向量"自觉"；
- 相似度低于 settings.vector_min_score 视为不相关（防弱相关误召回）；
- Milvus/Embedding 任何一步失败由 kb.search_diseases_smart 静默回退关键词计分，
  检索层永不阻塞问诊链路（EX-03 同款降级纪律）；
- 全量重建 = drop + 重建（维度以当次 embedding 为准），幂等。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pymilvus import DataType, MilvusClient
from sqlalchemy import select

from app.agents.kb import doc_to_kb_entry, load_entries
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import KbDocument
from app.services.embedding import embed_texts

COLLECTION = "kb_entries"

_SYNC_CLIENT: MilvusClient | None = None


def _client() -> MilvusClient:
    global _SYNC_CLIENT
    if _SYNC_CLIENT is None:
        _SYNC_CLIENT = MilvusClient(uri=settings.milvus_uri)
    return _SYNC_CLIENT


def _entry_text(entry: dict[str, Any]) -> str:
    """embedding 输入：病名 + 症状关键词（短文本，语义核心）。"""
    kws = "、".join(entry["symptom_keywords"][:8]) or "见描述"
    return f"{entry['disease_name']}：症状关键词 {kws}"


def _ensure_collection(dim: int) -> None:
    c = _client()
    if c.has_collection(COLLECTION):
        return
    schema = c.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("pk", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field("text", DataType.VARCHAR, max_length=4096)
    schema.add_field("disease_name", DataType.VARCHAR, max_length=256)
    schema.add_field("symptom_keywords", DataType.JSON)
    schema.add_field("species_code", DataType.VARCHAR, max_length=32)  # ""=公共文档
    schema.add_field("female_only", DataType.BOOL)
    schema.add_field("risk_level", DataType.VARCHAR, max_length=8)
    schema.add_field("source", DataType.VARCHAR, max_length=256)
    schema.add_field("med_names", DataType.JSON)
    schema.add_field("feed_advice", DataType.VARCHAR, max_length=1024)
    schema.add_field("doc_id", DataType.INT64)      # 0=内置种子
    schema.add_field("entry_kind", DataType.VARCHAR, max_length=16)  # seed/kb_doc

    index_params = c.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    c.create_collection(COLLECTION, schema=schema, index_params=index_params)


def _row(entry: dict[str, Any], vector: list[float]) -> dict[str, Any]:
    return {
        "vector": vector,
        "text": _entry_text(entry),
        "disease_name": str(entry["disease_name"])[:256],
        "symptom_keywords": list(entry.get("symptom_keywords") or []),
        "species_code": str(entry.get("species_code") or "")[:32],
        "female_only": bool(entry.get("female_only")),
        "risk_level": str(entry.get("risk_level") or "mid")[:8],
        "source": str(entry.get("source") or "")[:256],
        "med_names": list(entry.get("med_names") or []),
        "feed_advice": str(entry.get("feed_advice") or "")[:1024],
        "doc_id": int(entry.get("doc_id") or 0),
        "entry_kind": str(entry.get("entry_kind") or ("kb_doc" if entry.get("doc_id") else "seed"))[:16],
    }


def _insert_rows(rows: list[dict[str, Any]]) -> None:
    if rows:
        _client().insert(COLLECTION, rows)


def _replace_all_sync(entries: list[dict[str, Any]], vectors: list[list[float]]) -> None:
    """全量重建：release + drop + 按当次向量维度重建集合 + 一次性灌库（幂等）。

    注意：Milvus v3 对"已加载集合直接 drop"会阻塞（实测挂死），必须先 release。
    """
    c = _client()
    if c.has_collection(COLLECTION):
        try:
            c.release_collection(COLLECTION)
        except Exception:  # noqa: BLE001 — 未加载/已释放时忽略
            pass
        c.drop_collection(COLLECTION)
    _ensure_collection(dim=len(vectors[0]))
    _insert_rows([_row(e, v) for e, v in zip(entries, vectors)])


def _count_sync() -> int:
    c = _client()
    if not c.has_collection(COLLECTION):
        return 0
    # 注意：get_collection_stats 的 row_count 只统计已 flush 数据（growing segment 滞后），
    # 启动惰性建索引的判空必须用实时 count(*)，否则刚灌完的库会被误判为空而重复灌库
    res = c.query(COLLECTION, filter="pk >= 0", output_fields=["count(*)"])
    return int(res[0]["count(*)"]) if res else 0


def _search_sync(vector: list[float], species_code: str, top_k: int) -> list[dict[str, Any]]:
    """metadata 物种过滤 + 相似度阈值，输出与 kb_seed 条目同构。"""
    c = _client()
    if not c.has_collection(COLLECTION):
        return []
    flt = f'species_code in ["", "{species_code}"]'
    res = c.search(
        COLLECTION,
        data=[vector],
        limit=top_k,
        filter=flt,
        output_fields=[
            "disease_name", "symptom_keywords", "species_code", "female_only",
            "risk_level", "source", "med_names", "feed_advice", "doc_id", "entry_kind",
        ],
    )[0]
    out: list[dict[str, Any]] = []
    for h in res:
        if float(h.get("distance", 0)) < settings.vector_min_score:
            continue  # 弱相关丢弃（与关键词路径"零命中即拒答"口径对齐）
        e = h["entity"]
        out.append({
            "disease_name": e.get("disease_name"),
            "risk_level": e.get("risk_level") or "mid",
            "symptom_keywords": list(e.get("symptom_keywords") or []),
            "source": e.get("source") or "",
            "med_names": list(e.get("med_names") or []),
            "feed_advice": e.get("feed_advice") or "",
            "female_only": bool(e.get("female_only")),
            "species_code": e.get("species_code") or "",
            "doc_id": int(e.get("doc_id") or 0),
            "from_kb_doc": e.get("entry_kind") == "kb_doc",
            "vector_score": round(float(h.get("distance", 0)), 4),
        })
    return out


# =====================================================================
# 异步门面（业务层只调这里；Milvus 同步 SDK 经 to_thread 不阻塞事件循环）
# =====================================================================

async def collection_count() -> int:
    return await asyncio.to_thread(_count_sync)


async def search_kb(vector: list[float], species_code: str, top_k: int = 5) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_search_sync, vector, species_code, top_k)


def _delete_doc_sync(doc_id: int) -> None:
    c = _client()
    if c.has_collection(COLLECTION):
        c.delete(COLLECTION, filter=f"doc_id == {int(doc_id)}")


async def upsert_doc(doc_id: int, entry: dict[str, Any], vector: list[float]) -> None:
    """单文档增量索引（F16 上传/启用触发）：删旧插新，幂等。"""
    await asyncio.to_thread(_delete_doc_sync, doc_id)
    row = _row(entry, vector)
    row["doc_id"] = int(doc_id)
    row["entry_kind"] = "kb_doc"
    await asyncio.to_thread(_insert_rows, [row])


async def delete_doc(doc_id: int) -> None:
    """停用/删除文档时从向量库摘除（Milvus 无 is_enable 字段，必须显式删）。"""
    await asyncio.to_thread(_delete_doc_sync, doc_id)


async def reindex_all() -> int:
    """全量重建索引：内置种子 + 启用中的 F16 文档。返回灌库条数。"""
    entries: list[dict[str, Any]] = []
    for e in load_entries():
        row = dict(e)
        row.setdefault("doc_id", 0)
        row["entry_kind"] = "seed"
        entries.append(row)
    async with SessionLocal() as db:
        docs = (
            await db.execute(
                select(KbDocument).where(KbDocument.is_enable.is_(True), KbDocument.is_deleted.is_(False))
            )
        ).scalars().all()
    for d in docs:
        row = doc_to_kb_entry(d)
        row["entry_kind"] = "kb_doc"
        entries.append(row)

    vectors = await embed_texts([_entry_text(e) for e in entries])
    await asyncio.to_thread(_replace_all_sync, entries, vectors)
    return len(entries)


async def ensure_indexed() -> int:
    """启动期惰性建索引：集合缺失或为空才灌库（避免每次重启重复花 embedding 钱）。"""
    try:
        if await collection_count() > 0:
            return 0
        return await reindex_all()
    except Exception as e:  # noqa: BLE001 — 向量库不可用不拖垮启动
        import logging

        logging.getLogger("sf.vector").warning("向量索引初始化失败（回退关键词检索）: %s", e)
        return -1
