"""F01 账户与首登（03-API 1.x / 2.x）— 注册/登录/个人信息/改密/主物种。

与 mock 层同构：错误码 40901（已注册）、40102（账密错误/限流）、40103（旧密码错）；
EX-A2 统一话术不泄露账号存在性；FR-02 限流：连续 5 次失败锁 15 分钟。
"""

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.ratelimit import login_rate_limit, register_rate_limit
from app.core.response import (
    E_BAD_CREDENTIALS,
    E_INVALID_TOKEN,
    E_OLD_PASSWORD_WRONG,
    E_PHONE_REGISTERED,
    E_VALIDATION,
    ApiError,
    ok,
)
from app.core.security import (
    get_current_user,
    hash_password_async,
    issue_token,
    issue_ws_token,
    verify_password_async,
)
from app.models import Farm, LoginAttempt, User

router = APIRouter(prefix="/api")

SPECIES_CODES = {"pig", "cattle", "sheep"}


def is_valid_phone(phone: str) -> bool:
    import re

    return bool(re.fullmatch(r"1[3-9]\d{9}", phone))


def validate_password(password: str) -> str | None:
    if len(password) < 8:
        return "密码需 ≥8 位且同时包含字母和数字"
    has_alpha = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_alpha and has_digit):
        return "密码需 ≥8 位且同时包含字母和数字"
    return None


def mask_phone(phone: str) -> str:
    return f"{phone[:3]}****{phone[-4:]}"


async def _guide_done(db: AsyncSession, user_id: int) -> bool:
    from sqlalchemy import func as fn

    cnt = (
        await db.execute(select(fn.count()).select_from(Farm).where(Farm.user_id == user_id, Farm.is_deleted == False))  # noqa: E712
    ).scalar_one()
    return cnt > 0


def to_user_summary(user: User, gd: bool) -> dict[str, Any]:
    return {
        "user_id": user.user_id,
        "phone": mask_phone(user.phone),
        "nickname": user.nickname,
        "role": user.role,
        "main_species_code": user.main_species_code,
        "guide_done": gd,
    }


class RegisterBody(BaseModel):
    phone: str = Field(default="")
    password: str = Field(default="")
    nickname: str = Field(default="")


@router.post("/auth/register")
async def register(
    body: RegisterBody, db: AsyncSession = Depends(get_db), _rl: None = Depends(register_rate_limit)
) -> dict[str, Any]:
    if not is_valid_phone(body.phone):
        raise ApiError(E_VALIDATION, "手机号格式不正确", {"field": "phone"})
    if err := validate_password(body.password):
        raise ApiError(E_VALIDATION, err, {"field": "password"})
    exists = (
        await db.execute(select(User).where(User.phone == body.phone))
    ).scalar_one_or_none()
    if exists:
        raise ApiError(E_PHONE_REGISTERED, "该手机号已注册，请直接登录")
    user = User(
        phone=body.phone,
        password_hash=await hash_password_async(body.password),
        nickname=body.nickname.strip() or mask_phone(body.phone),
        role="farmer",
        main_species_code=None,
        is_enable=True,
    )
    db.add(user)
    await db.commit()
    return ok({"user_id": user.user_id, "phone": user.phone, "first_login": True})


class LoginBody(BaseModel):
    phone: str = Field(default="")
    password: str = Field(default="")


@router.post("/auth/login")
async def login(
    body: LoginBody, db: AsyncSession = Depends(get_db), _rl: None = Depends(login_rate_limit)
) -> dict[str, Any]:
    # 两道独立的闸：`login_rate_limit` 按来源 IP 挡撞库和"故意失败 5 次锁死别人账号"，
    # 下面的 login_attempts 按手机号挡针对单账号的爆破。缺任何一个都留缺口。
    if not is_valid_phone(body.phone):
        raise ApiError(E_VALIDATION, "手机号格式不正确", {"field": "phone"})

    attempt = (
        await db.execute(select(LoginAttempt).where(LoginAttempt.phone == body.phone))
    ).scalar_one_or_none()
    now_ms = int(time.time() * 1000)
    if attempt and attempt.lock_until > now_ms:
        remain_min = max(1, (attempt.lock_until - now_ms + 59_999) // 60_000)
        raise ApiError(E_BAD_CREDENTIALS, f"尝试次数过多，请 {remain_min} 分钟后重试")

    user = (await db.execute(select(User).where(User.phone == body.phone))).scalar_one_or_none()
    if user is None or not (await verify_password_async(body.password, user.password_hash)):
        # EX-A2：统一话术；连续失败 ≥5 次锁 15 分钟
        cnt = (attempt.count if attempt else 0) + 1
        if cnt >= settings.lock_threshold:
            lock_until = now_ms + settings.lock_minutes * 60_000
            if attempt:
                attempt.count = 0
                attempt.lock_until = lock_until
            else:
                db.add(LoginAttempt(phone=body.phone, count=0, lock_until=lock_until))
        else:
            if attempt:
                attempt.count = cnt
            else:
                db.add(LoginAttempt(phone=body.phone, count=cnt, lock_until=0))
        await db.commit()
        raise ApiError(E_BAD_CREDENTIALS, "手机号或密码错误")

    if user.is_enable is False:
        raise ApiError(E_INVALID_TOKEN, "账号已禁用，请联系管理员")

    if attempt:
        await db.execute(delete(LoginAttempt).where(LoginAttempt.phone == body.phone))
        await db.commit()

    token, expires_in = issue_token(user)
    gd = await _guide_done(db, user.user_id)
    return ok({"token": token, "expires_in": expires_in, "user": to_user_summary(user, gd)})


@router.post("/auth/ws-token")
async def ws_token(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """签发告警通道短令牌（C-03）。

    长期业务 token 不进 WebSocket：它寿命 24h，且握手 URL 会落到反代/访问日志里。
    这里发 5 分钟、scope=ws 的一次性凭据，前端用 **Sec-WebSocket-Protocol 头**带上，
    不拼进 query string。过期后重连前先重新签发（前端退避重连时顺带续期）。
    """
    token, expires_in = issue_ws_token(user)
    return ok({"ws_token": token, "expires_in": expires_in})


@router.get("/user/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    gd = await _guide_done(db, user.user_id)
    return ok(to_user_summary(user, gd))


class SpeciesBody(BaseModel):
    main_species_code: str = Field(default="")


@router.put("/user/main-species")
async def update_species(
    body: SpeciesBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if body.main_species_code not in SPECIES_CODES:
        raise ApiError(E_VALIDATION, "无效的主物种编码", {"field": "main_species_code"})
    user.main_species_code = body.main_species_code  # EX-08：仅影响新建圈舍默认物种
    await db.commit()
    gd = await _guide_done(db, user.user_id)
    return ok(to_user_summary(user, gd))


class PasswordBody(BaseModel):
    old_password: str = Field(default="")
    new_password: str = Field(default="")


@router.put("/user/password")
async def update_password(
    body: PasswordBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not (await verify_password_async(body.old_password, user.password_hash)):
        raise ApiError(E_OLD_PASSWORD_WRONG, "当前密码不正确")
    if body.old_password == body.new_password:
        raise ApiError(E_VALIDATION, "新密码不能与当前密码相同", {"field": "new_password"})
    if err := validate_password(body.new_password):
        raise ApiError(E_VALIDATION, err, {"field": "new_password"})
    user.password_hash = await hash_password_async(body.new_password)
    await db.commit()
    return ok({"updated": True})
