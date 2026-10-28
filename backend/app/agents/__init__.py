"""LangGraph 工作流包 — F05 问诊链路 + F10 HITL 门（01-架构第二部分 9 节的目标形态落地）。

对外只暴露三个入口：
- `init_agent_runtime` / `close_agent_runtime`：应用 lifespan 装配与释放连接池 + checkpointer
- `get_graph`：已编译的图（astream 出 SSE 帧 / ainvoke(Command(resume=...)) 恢复）
- `thread_config`：thread_id 配置构造
"""

from app.agents.graph import (
    build_graph,
    checkpoint_conninfo,
    close_agent_runtime,
    get_graph,
    init_agent_runtime,
    prune_checkpoints,
    thread_config,
)

__all__ = [
    "build_graph",
    "checkpoint_conninfo",
    "close_agent_runtime",
    "get_graph",
    "init_agent_runtime",
    "prune_checkpoints",
    "thread_config",
]
