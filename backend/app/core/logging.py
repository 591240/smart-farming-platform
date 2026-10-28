"""日志与请求 ID — 01-架构十四节的轻量可观测实现。

没引 loguru（环境未装、也不想为一行格式化加依赖）：stdlib + ContextVar 足够，
把 request_id 贯穿 API → Agent → 台账，异常才能力求归因到同一条请求链。

SSE 响应体在中间件返回之后才逐帧生成，因此 ContextVar 在流内需要由流函数自己回填
（见 api/agent.py 的 _stream）。
"""

from __future__ import annotations

import contextvars
import logging
import sys
from uuid import uuid4

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_FMT = "%(asctime)s %(levelname)-7s %(request_id)s [%(name)s] %(message)s"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FMT, datefmt="%H:%M:%S"))
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def new_request_id() -> str:
    return uuid4().hex[:12]
