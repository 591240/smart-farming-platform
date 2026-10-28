"""限流闸 — 滑动窗口计数统一走 core/statestore（Redis 有序集合 + Lua 原子占位）。

两类闸：
- **LLM 出口按用户**（20 次/分）：这些接口每次调用都在花真金白银的 token，客户端重试会
  成倍放大成本。业务接口有租户隔离和数据量上的天然上限，不需要同一套限流。
- **未认证入口按来源 IP**（登录/注册/设备上行）：它们发生在鉴权之前，拿不到 user_id，
  而它们恰恰是被爆破和被灌数的入口。三个阈值都在 config 可配——一个养殖场常是多人共用
  一条出口 IP，设备侧又常由网关代报多路，写死必然误伤真实用户。

配了 Redis 之后 N 个 worker 共享同一份配额；没配则退回本进程计数，单 worker 依然准确。
"""

from __future__ import annotations

from fastapi import Depends, Request

from app.core import statestore
from app.core.config import settings
from app.core.response import E_RATE_LIMITED, ApiError
from app.core.security import get_current_user
from app.models import User

# 单用户每分钟允许的 LLM 调用次数。按正常使用口径定：一轮问诊 1~2 次调用（意图兜底 +
# 诊断），识图另算 1 次；20 次足够一个人连续问十几轮，写循环刷接口会在第 21 次被拦。
LLM_RATE_PER_MINUTE = 20
_WINDOW_MS = 60_000


async def llm_rate_limit(user: User = Depends(get_current_user)) -> User:
    """挂在 LLM 端点上的依赖：超限直接 42901，不进入上游调用。"""
    if not await statestore.allow_within(f"rl:llm:{user.user_id}", LLM_RATE_PER_MINUTE, _WINDOW_MS):
        raise ApiError(E_RATE_LIMITED, "AI 调用过于频繁，请稍后再试")
    return user


# ---------- 未认证入口的按 IP 闸 ----------
# 登录锁定（auth.py 按 phone 计数）挡的是"针对单个账号猛试"，挡不住另外两件事：
# ① 轮换手机号撞库；② 故意把真实农户的号失败 5 次、把他锁在门外（拒绝服务）。
# 按 IP 的速率闸补的就是这两刀——不追求精确，只要让这两种打法变昂贵且必然留痕。

_HOUR_MS = 3_600_000


def client_ip(request: Request) -> str:
    """取真实来源 IP。前提是后端只监听 127.0.0.1、公网唯一入口是 nginx（见 deploy/nginx.conf）。
    若把 8000 直接暴露到公网，XFF 可被伪造——那是部署错误，不是这段代码能兜的。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def login_rate_limit(request: Request) -> None:
    if not await statestore.allow_within(f"rl:login:{client_ip(request)}", settings.login_rate_per_minute, _WINDOW_MS):
        raise ApiError(E_RATE_LIMITED, "尝试过于频繁，请稍后再试")


async def register_rate_limit(request: Request) -> None:
    if not await statestore.allow_within(f"rl:reg:{client_ip(request)}", settings.register_rate_per_hour, _HOUR_MS):
        raise ApiError(E_RATE_LIMITED, "注册过于频繁，请稍后再试")


async def ingest_rate_limit(request: Request) -> None:
    """设备上行按 IP 而非按设备限：网关一次代报几十个设备是正常形态，阈值必须为此留量。"""
    if not await statestore.allow_within(f"rl:ingest:{client_ip(request)}", settings.ingest_rate_per_minute, _WINDOW_MS):
        raise ApiError(E_RATE_LIMITED, "上报过于频繁")
