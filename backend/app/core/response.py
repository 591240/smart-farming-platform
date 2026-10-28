"""统一响应与错误码 — 与前端 mock 层完全同构（03-API 1.2/1.3）。

前端 apiFetch 约定：code=0 成功取 data；非 0 抛 ApiError(code, message, data)。
本模块的 ApiError 由 FastAPI exception_handler 统一转成 HTTP 200 + 业务错误码，
保证前端切真后错误处理零改动。
"""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

# 错误码（03-API 1.3 + 各功能 PRD 具体化，与 mock-backend ERR 一致）
E_WITHDRAWAL = 40001        # F07 休药期未满
E_NEGATIVE_STOCK = 40002    # F02 存栏不足/导致负数
E_INVALID_TRANSITION = 40003
E_CONFIRM_NEEDED = 40005
E_INVALID_TOKEN = 40101
E_BAD_CREDENTIALS = 40102
E_OLD_PASSWORD_WRONG = 40103
E_NOT_FOUND = 40401
E_PHONE_REGISTERED = 40901
E_ROLE_FORBIDDEN = 40301
E_FORBIDDEN = 40302
E_VALIDATION = 42201
E_SERVER = 50001
E_KB_NO_RESULT = 50002       # F05 EX-04：知识库无该病症资料（明确拒答，禁止编造）
E_WEATHER_UNAVAILABLE = 50003  # F08 EX-02：气象不可用（无缓存可退时）
E_RATE_LIMITED = 42901       # LLM 出口限流（单用户每分钟配额）


class ApiError(Exception):
    """业务错误：携带 code/message/data，由全局 handler 转响应。"""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=200, content={"code": exc.code, "message": exc.message, "data": exc.data})


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """pydantic 入参校验失败（FastAPI 默认返回裸 422 {detail:[...]}）。

    不接管的话会绕过 {code,message,data} 契约：前端 `result.code` 取到 undefined，
    弹窗直接显示 "undefined"。这里取第一条错误转成 42201，字段名原样带回去。
    """
    errors = getattr(exc, "errors", lambda: [])() or []
    first = errors[0] if errors else {}
    loc = [str(p) for p in (first.get("loc") or []) if p != "body"]
    field = ".".join(loc) or "参数"
    detail = str(first.get("msg") or "参数不合法")
    return JSONResponse(
        status_code=200,
        content={
            "code": E_VALIDATION,
            "message": f"{field} {detail}".strip(),
            "data": {"field": field, "detail": detail},
        },
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # 兜底：不泄露内部细节（堆栈在服务端日志）
    return JSONResponse(status_code=200, content={"code": 50001, "message": "服务异常，请稍后重试", "data": None})
