"""Embedding Client — DashScope compatible-mode /embeddings 统一封装（B5 向量检索）。

- 模型：settings.embedding_model（默认 qwen3.7-text-embedding，2026-10-12 用户拍板）
- 与主 LLM 同一 base_url / api_key（config 三节预留结构）
- 失败统一抛 EmbeddingError，由调用方决定降级（检索层回退关键词计分，EX-03 同款纪律）
- 批量分片（每批 ≤10 条）对齐 DashScope 批量上限；文本超长截断（embedding 输入上限 8192 token，
  知识条目均为短文本，保险截断 2000 字符）
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings

EMBED_BATCH_SIZE = 10
EMBED_TEXT_MAX_CHARS = 2000


class EmbeddingError(Exception):
    """上游 Embedding 调用失败（网络/鉴权/结构异常）。"""


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量文本向量化，返回与输入顺序一致的向量列表。空输入返回空列表。"""
    if not texts:
        return []
    if not settings.key_configured:
        raise EmbeddingError("LLM_API_KEY 未配置")
    clean = [(t or "").strip()[:EMBED_TEXT_MAX_CHARS] or "（空）" for t in texts]

    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(0, len(clean), EMBED_BATCH_SIZE):
            batch = clean[i : i + EMBED_BATCH_SIZE]
            try:
                resp = await client.post(
                    f"{settings.llm_base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {settings.api_key}"},
                    json={"model": settings.embedding_model, "input": batch},
                )
            except httpx.HTTPError as e:
                raise EmbeddingError(f"Embedding 网络异常: {type(e).__name__}") from e
            if resp.status_code != 200:
                raise EmbeddingError(f"Embedding 上游 HTTP {resp.status_code}")
            try:
                data: list[dict[str, Any]] = resp.json()["data"]
                rows = sorted(data, key=lambda d: int(d.get("index", 0)))
                batch_vecs = [list(map(float, d["embedding"])) for d in rows]
            except (KeyError, IndexError, TypeError, ValueError) as e:
                raise EmbeddingError("Embedding 响应结构异常") from e
            if len(batch_vecs) != len(batch):
                raise EmbeddingError("Embedding 返回条数与请求不一致")
            vectors.extend(batch_vecs)
    return vectors
