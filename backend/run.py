"""后端启动器 — 必须在事件循环创建之前把循环换成 SelectorEventLoop。

为什么不能直接 `uvicorn app.main:app`：
Windows 上 asyncio 默认 ProactorEventLoop，而 psycopg（LangGraph 的
langgraph-checkpoint-postgres 依赖）异步模式只支持 SelectorEventLoop，
连接池会在 30s 后抛 PoolTimeout，`/api/agent/chat` 与 `/api/vet/review` 全部失败。
uvicorn 自己创建事件循环、app 模块在循环之后才导入，因此策略只能在启动器里定。

用法：`python run.py`（见根目录 启动后端.bat / 启动项目.bat）
"""

import asyncio
import sys

import uvicorn

from app.core.config import settings


def main() -> None:
    server = uvicorn.Server(
        uvicorn.Config("app.main:app", host=settings.ai_host, port=settings.ai_port, log_level="info")
    )
    if sys.platform == "win32":
        if hasattr(asyncio, "Runner"):  # Python 3.12+：显式指定 loop_factory，不用已弃用的策略 API
            asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
            return
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
