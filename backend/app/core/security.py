"""认证装配 — bcrypt 密码散列 + JWT 签发/校验 + 当前用户依赖（F01/F10 口径）。

token payload: { user_id, role, exp }（HS256）；TTL 86400s（03-API 1.1）。
"""

import asyncio
import hashlib
import hmac
import secrets
import time
from typing import Any

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.response import E_INVALID_TOKEN, E_ROLE_FORBIDDEN, ApiError
from app.models import User

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


def issue_token(user: User) -> tuple[str, int]:
    now = int(time.time())
    expires_at = now + settings.jwt_ttl_seconds
    payload: dict[str, Any] = {"user_id": user.user_id, "role": user.role, "exp": expires_at}
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at * 1000  # expires_in 与 mock 口径一致（毫秒时间戳）


def decode_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise ApiError(E_INVALID_TOKEN, "未登录或登录已过期") from e
    # 两类令牌必须互不通用。此前只有 decode_ws_token 单向拒绝业务令牌，反向漏了：
    # 一张 5 分钟的 scope=ws 短令牌因此可以当全权业务令牌调任何接口。
    if payload.get("scope"):
        raise ApiError(E_INVALID_TOKEN, "该令牌仅限告警通道使用")
    return payload


# WebSocket 凭据（C-03）：业务 token 寿命 24h 且可能出现在反代/访问日志里，不能直接给 ws 用。
# 这里另发 5 分钟、scope=ws 的短令牌，且只走 Sec-WebSocket-Protocol 头（不进 URL）。
WS_TOKEN_TTL_SECONDS = 300


def issue_ws_token(user: User) -> tuple[str, int]:
    now = int(time.time())
    expires_at = now + WS_TOKEN_TTL_SECONDS
    payload: dict[str, Any] = {"user_id": user.user_id, "role": user.role, "scope": "ws", "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_at * 1000


def decode_ws_token(token: str) -> dict[str, Any]:
    """只接受 ws 专用短令牌——长期业务 token 即便泄露也连不上告警通道。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise ApiError(E_INVALID_TOKEN, "ws 凭据无效或已过期") from e
    if payload.get("scope") != "ws":
        raise ApiError(E_INVALID_TOKEN, "请使用 /auth/ws-token 签发的短令牌连接告警通道")
    return payload


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise ApiError(E_INVALID_TOKEN, "未登录或登录已过期")
    payload = decode_token(credentials.credentials)
    user = (await db.execute(select(User).where(User.user_id == payload.get("user_id")))).scalar_one_or_none()
    if user is None:
        raise ApiError(E_INVALID_TOKEN, "未登录或登录已过期")
    # F10 RK-6：禁用即时生效（已签发 token 命中禁用账号同样拦截）
    if user.is_enable is False:
        raise ApiError(E_INVALID_TOKEN, "账号已禁用，请联系管理员")
    return user


async def optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """设备上行接口的双轨鉴权之一：带 Bearer 就按养殖户校验，不带则返回 None 走设备密钥分支。

    与 `get_current_user` 的差别只在"不带 token 不报错"。带了无效 token 仍然 40101——
    不让一份坏 JWT 静默降级到另一条凭证通道。
    """
    if credentials is None or not credentials.credentials:
        return None
    payload = decode_token(credentials.credentials)
    user = (await db.execute(select(User).where(User.user_id == payload.get("user_id")))).scalar_one_or_none()
    if user is None:
        raise ApiError(E_INVALID_TOKEN, "未登录或登录已过期")
    if user.is_enable is False:
        raise ApiError(E_INVALID_TOKEN, "账号已禁用，请联系管理员")
    if user.role != "farmer":
        raise ApiError(E_ROLE_FORBIDDEN, "当前角色无权访问该功能")
    return user


def issue_device_secret() -> tuple[str, str]:
    """C-24：生成设备上行密钥，返回 (明文, sha256 hex)。明文只在创建/轮换时给出一次。"""
    raw = secrets.token_hex(16)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_device_secret(raw: str, stored_hash: str) -> bool:
    if not stored_hash or not raw:
        return False
    return hmac.compare_digest(hashlib.sha256(raw.encode("utf-8")).hexdigest(), stored_hash)


async def require_farmer(user: User = Depends(get_current_user)) -> User:
    """养殖户业务接口的角色门卫（01-架构十三节：授权面最小化）。

    vet 仅开放待复核病例接口，admin 仅开放全局配置库与用户管理；两者命中养殖户
    业务路径一律 40301——与前端 mock 分发器入口的同名门卫逐字对齐。
    租户过滤（farm.user_id）只保证"查不到别人的数据"，不等于"这个角色不该进来"，
    两道是不同层次的控制，缺一不可。
    """
    if user.role != "farmer":
        raise ApiError(E_ROLE_FORBIDDEN, "当前角色无权访问该功能")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """写全局共享数据的门卫：结果会影响其他租户的，不能由任意登录用户点火。

    与 `require_farmer` 对称。目前挂在 `/feed/price/sync`（LLM 联网采价会改写全局参考价，
    进而改变别人的配方成本）；`api/admin.py` 里那 6 处内联 `role != "admin"` 判断与它等价，
    尚未收敛到这个依赖。
    """
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可访问该功能")
    return user
