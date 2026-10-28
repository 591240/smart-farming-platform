"""LangGraph 主图编排 + PostgresSaver checkpoint 装配（01-架构 9.1/9.4）。

图结构（V1.0 只实装问诊子图，主图调度退化为一条意图路由）：

    START
      └─ intent_recognize ──(非 consult / 违禁诱导 / 写指令)──> fallback_reply ─> END
             │
             └─(consult)─> load_context_retrieve ──(定位失败)──> clarify_reply ─> END
                                  │              └─(EX-04/EX-C4)─> kb_reply ─> END
                                  └─> diagnose ─> medication_check ─> record
                                                                        │
                                              (low/mid) ────────────────┴──> END
                                              (high) ──> hitl_gate(interrupt) ──> record_review ─> END

thread 模型：**每轮一个新 thread_id**（`c-{session_id}-{rand}`），不按会话复用。
理由见 state.py 文档字符串——同 thread 的 run 结束后再 invoke 会从 START 重跑，
第二轮问题若落在未恢复的 checkpoint 上会撞坏挂起状态。

Windows 注意：psycopg 异步模式要求 SelectorEventLoop，必须用 `backend/run.py` 启动
（`python -m uvicorn` 在 Windows 上是 ProactorEventLoop，连接池会 30s 超时）。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool

from app.agents import nodes
from app.agents.state import ConsultState
from app.core.config import settings

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None
_graph: Any = None


def checkpoint_conninfo() -> str:
    """把 SQLAlchemy 的 asyncpg DSN 转成 psycopg 可用的 conninfo。"""
    url = settings.database_url
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def build_graph(checkpointer: AsyncPostgresSaver | None = None):
    g: StateGraph = StateGraph(ConsultState)
    g.add_node("intent_recognize", nodes.intent_recognize)
    g.add_node("vision_describe", nodes.vision_describe)
    g.add_node("fallback_reply", nodes.fallback_reply)
    g.add_node("data_query_dispatch", nodes.data_query_dispatch)
    g.add_node("weather_answer", nodes.weather_answer)
    g.add_node("load_context_retrieve", nodes.load_context_retrieve)
    g.add_node("clarify_reply", nodes.clarify_reply)
    g.add_node("kb_reply", nodes.kb_reply)
    g.add_node("diagnose", nodes.diagnose)
    g.add_node("medication_check", nodes.medication_check)
    g.add_node("record", nodes.record)
    g.add_node("hitl_gate", nodes.hitl_gate)
    g.add_node("record_review", nodes.record_review)

    g.add_edge(START, "intent_recognize")
    g.add_conditional_edges(
        "intent_recognize",
        nodes.route_after_intent,
        {
            "fallback_reply": "fallback_reply",
            "vision_describe": "vision_describe",
            "load_context_retrieve": "load_context_retrieve",
            "data_query_dispatch": "data_query_dispatch",
            "weather_answer": "weather_answer",
        },
    )
    g.add_edge("fallback_reply", END)
    g.add_edge("data_query_dispatch", END)
    g.add_edge("weather_answer", END)
    g.add_edge("vision_describe", "load_context_retrieve")
    g.add_conditional_edges(
        "load_context_retrieve",
        nodes.route_after_context,
        {"clarify_reply": "clarify_reply", "kb_reply": "kb_reply", "diagnose": "diagnose"},
    )
    g.add_edge("clarify_reply", END)
    g.add_edge("kb_reply", END)
    g.add_edge("diagnose", "medication_check")
    g.add_edge("medication_check", "record")
    g.add_conditional_edges("record", nodes.route_after_record, {"hitl_gate": "hitl_gate", "__end__": END})
    g.add_edge("hitl_gate", "record_review")
    g.add_edge("record_review", END)
    return g.compile(checkpointer=checkpointer)


async def init_agent_runtime() -> None:
    """应用启动时装配连接池 + checkpointer（幂等）。

    建表由 saver.setup() 完成，落在独立 schema（默认 langgraph，勿手工操作）——
    与业务表隔离，业务侧 Alembic 迁移不碰它。
    """
    global _pool, _saver, _graph
    if _graph is not None:
        return
    schema = settings.checkpoint_schema
    _pool = AsyncConnectionPool(
        conninfo=checkpoint_conninfo(),
        max_size=settings.checkpoint_pool_size,
        open=False,
        # prepare_threshold=0：池化连接下禁用 prepared statement 缓存（LangGraph 官方建议，
        # 避免 pgbouncer/连接复用时 "cached plan must not change result type"）
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "options": f"-csearch_path={schema}",
        },
    )
    await _pool.open(wait=True, timeout=15)
    async with _pool.connection() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    _saver = AsyncPostgresSaver(_pool)
    await _saver.setup()
    _graph = build_graph(_saver)


async def close_agent_runtime() -> None:
    global _graph, _saver, _pool
    _graph, _saver = None, None
    if _pool is not None:
        await _pool.close()
        _pool = None


async def prune_checkpoints() -> int:
    """清理不可再恢复的 checkpoint，返回删除的 checkpoints 行数。

    判据不是时间而是**可恢复性**：thread_id 按轮生成，一轮跑完（低中危直接结束、高危已复核终态）
    后它的 checkpoint 就只剩历史价值——会话消息与台账早已落在业务表里，删掉不影响任何回看。
    唯一必须保留的是"还挂在 hitl_gate 上等兽医"的轮次，即 pending 状态 vet_review 的 thread_id。
    （G-09：按保留期清理需要 created_at 列，LangGraph 标准表没有，故改用这个更准的判据。）
    """
    if _pool is None:
        return 0
    keep_sql = (
        "SELECT thread_id FROM public.vet_reviews "
        "WHERE review_status = 'pending' AND thread_id IS NOT NULL"
    )
    async with _pool.connection() as conn:
        async with conn.transaction():
            ids = (
                await conn.execute(
                    f"SELECT DISTINCT checkpoint_id FROM checkpoints WHERE thread_id NOT IN ({keep_sql})"
                )
            ).fetchall()
            if not ids:
                return 0
            dropped = await conn.execute(
                f"DELETE FROM checkpoint_writes WHERE checkpoint_id IN "
                f"(SELECT checkpoint_id FROM checkpoints WHERE thread_id NOT IN ({keep_sql}))"
            )
            await conn.execute(
                f"DELETE FROM checkpoint_blobs WHERE thread_id NOT IN ({keep_sql})"
            )
            cur = await conn.execute(
                f"DELETE FROM checkpoints WHERE thread_id NOT IN ({keep_sql})"
            )
            return cur.rowcount or 0


def get_graph():
    """取已编译的图；未初始化时返回无 checkpointer 版本（仅用于单测/离线跑通链路）。"""
    if _graph is not None:
        return _graph
    return build_graph(None)


def thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}
