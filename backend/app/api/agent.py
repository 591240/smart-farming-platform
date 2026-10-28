"""F05 AI 问诊 — SSE 流式端点 + 会话历史（03-API 九节 9.1~9.3）。

帧契约与前端 `parseSseFrame` 严格一致（`event:` / `data:` 两行 + 空行分隔），
事件枚举 intent / token / section / done / error（01-架构十一节）。前端
`apiChatStream` 切真只换传输层，渲染代码零改动。

三处刻意的实现取舍：
1. **前置校验走 HTTP 错误码，流内错误走 error 帧**：一旦 StreamingResponse 开始输出，
   HTTP 状态码与 JSON body 就发不出去了。因此会话归属、pen_id 归属在开流前校验
   （40401/40302/40301），消息为空等流内异常才发 error 帧。
2. **会话先落库再开流**：节点用各自的 SessionLocal 读会话与写消息，必须先 commit。
3. **thread_id 按轮生成**：见 agents/graph.py 文档字符串。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import get_graph, prompts, thread_config
from app.core.db import get_db
from app.core.logging import request_id_var
from app.core.ratelimit import llm_rate_limit
from app.core.response import E_FORBIDDEN, E_NOT_FOUND, E_SERVER, E_VALIDATION, ApiError, ok
from app.core.security import get_current_user, require_farmer
from app.models import AgentMessage, AgentSession, AnimalIndividual, Farm, Pen, User

logger = logging.getLogger(__name__)

# require_farmer：vet/admin 不得访问养殖户业务接口（01-架构十三节授权面最小化）
router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])

MESSAGE_MAX_CHARS = 500      # EX-D11
HISTORY_TURNS = 6            # 送入 LLM 的历史消息条数上限
TITLE_MAX_CHARS = 16

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # 经 nginx 反代时禁用缓冲，否则帧被攒到结束才吐
}


class ChatReq(BaseModel):
    # 与前端输入框 maxLength、nodes.MESSAGE_MAX_CHARS 同口径（此前是 600/500/无限制三套）
    message: str = Field(default="", max_length=MESSAGE_MAX_CHARS)
    session_id: int | None = None
    pen_id: int | None = None
    individual_id: int | None = None  # F14：问诊绑定到具体个体（耳标）
    # 上限与 /ai/vision 同口径：此前无限制，一张超大 data URL 会整段写进 agent_messages.meta
    # 并原样回吐给前端，20 次/分的配额也够把 4G 机器的内存和 PG 撑爆
    image: str | None = Field(default=None, max_length=prompts.VISION_DATA_URL_MAX_CHARS)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _make_title(message: str, has_image: bool) -> str:
    # 只发图不打字是合法轮（后端允许 message 为空 + image），此时标题不能是"图·"
    if not message:
        return "图像问诊" if has_image else "新会话"
    title = message[:TITLE_MAX_CHARS] + ("…" if len(message) > TITLE_MAX_CHARS else "")
    return f"图·{title}" if has_image else title


async def _my_farm_ids(db: AsyncSession, user: User) -> list[int]:
    rows = await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id, Farm.is_deleted.is_(False)))
    return list(rows.scalars())


async def _load_history(db: AsyncSession, session_id: int) -> list[dict[str, str]]:
    rows = (
        await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.message_id.desc())
            .limit(HISTORY_TURNS)
        )
    ).scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


async def _stream(state: dict[str, Any], thread_id: str, request_id: str = "-"):
    """把图内 get_stream_writer() 推出的帧转成 SSE 文本。

    stream_mode="custom"：只收节点主动 emit 的帧（intent/section/token/done/error），
    updates/values 不转发——帧形状由节点全权决定，避免把内部状态泄给前端。

    响应体在 HTTP 中间件返回之后才逐帧生成，ContextVar 已被重置，
    所以这里自行回填 request_id，保证流内异常日志仍能找到同一条请求链。
    """
    request_id_var.set(request_id)
    graph = get_graph()
    try:
        async for chunk in graph.astream(state, thread_config(thread_id), stream_mode="custom"):
            if isinstance(chunk, dict) and "event" in chunk:
                yield _sse(str(chunk["event"]), chunk.get("data") or {})
    except Exception:  # noqa: BLE001 — EX-03：任何异常都转友好错误帧，不把堆栈吐给前端
        logger.exception("agent chat failed thread=%s session=%s", thread_id, state.get("session_id"))
        yield _sse("error", {"code": E_SERVER, "message": "AI 服务暂时不可用，请稍后重试"})
        yield _sse("done", {"session_id": state.get("session_id")})


@router.post("/agent/chat")
async def agent_chat(
    req: ChatReq,
    request: Request,
    user: User = Depends(llm_rate_limit),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    image = req.image if isinstance(req.image, str) and req.image.startswith("data:image/") else None
    message = (req.message or "").strip()[:MESSAGE_MAX_CHARS]
    if not message and not image:
        raise ApiError(E_VALIDATION, "消息不能为空")
    raw_message = message  # 标题按用户真实输入生成，别把占位文案带进会话列表
    if not message:
        message = "（图片问诊）"  # FR-11：纯图片轮以占位文案入会话

    # ----- 会话定位/创建（FR-08：session_id 可空=新建）-----
    session: AgentSession | None = None
    if req.session_id:
        session = await db.get(AgentSession, req.session_id)
        if session is None or session.is_deleted:
            raise ApiError(E_NOT_FOUND, "会话不存在")
        if session.user_id != user.user_id:
            raise ApiError(E_FORBIDDEN, "无权限访问")
    if session is None:
        session = AgentSession(user_id=user.user_id, title=_make_title(raw_message, image is not None), intent="consult")
        db.add(session)
        await db.flush()

    # ----- pen_id 归属预校验（流开启后无法再返回 JSON 错误码）-----
    farm_ids = await _my_farm_ids(db, user)
    if req.pen_id:
        pen = await db.get(Pen, req.pen_id)
        if pen is None or pen.is_deleted:
            raise ApiError(E_NOT_FOUND, "圈舍不存在")
        if pen.farm_id not in farm_ids:
            raise ApiError(E_FORBIDDEN, "无权限访问")
    if req.individual_id:
        ind = await db.get(AnimalIndividual, req.individual_id)
        if ind is None or ind.is_deleted:
            raise ApiError(E_NOT_FOUND, "个体不存在")
        if ind.farm_id not in farm_ids:
            raise ApiError(E_FORBIDDEN, "无权限访问")
        # 个体自带圈舍，前端只传 individual_id 也能定位；显式 pen_id 与个体不同圈时以个体为准
        if req.pen_id and req.pen_id != ind.pen_id:
            raise ApiError(E_VALIDATION, "所选个体不属于该圈舍")

    history = await _load_history(db, session.session_id)
    thread_id = f"c-{session.session_id}-{uuid.uuid4().hex[:12]}"

    state: dict[str, Any] = {
        "user_id": user.user_id,
        "session_id": session.session_id,
        "thread_id": thread_id,
        "message": message,
        "pen_id": req.pen_id,
        "individual_id": req.individual_id,
        "image": image,
        "main_species_code": user.main_species_code or "pig",
        "history": history,
        "review_result": None,
    }
    await db.commit()  # 节点各自开 SessionLocal，会话必须先可见

    return StreamingResponse(
        _stream(state, thread_id, getattr(request.state, "request_id", "-")),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/agent/sessions")
async def list_sessions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    rows = (
        await db.execute(
            select(AgentSession)
            .where(AgentSession.user_id == user.user_id, AgentSession.is_deleted.is_(False))
            .order_by(AgentSession.update_time.desc(), AgentSession.session_id.desc())
        )
    ).scalars().all()
    return ok(
        {
            "items": [
                {
                    "session_id": s.session_id,
                    "title": s.title,
                    "update_time": s.update_time.isoformat() if s.update_time else s.create_time.isoformat(),
                }
                for s in rows
            ]
        }
    )


@router.get("/agent/sessions/{session_id}/messages")
async def session_messages(
    session_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    session = await db.get(AgentSession, session_id)
    if session is None or session.is_deleted:
        raise ApiError(E_NOT_FOUND, "会话不存在")
    if session.user_id != user.user_id:
        raise ApiError(E_FORBIDDEN, "无权限访问")
    rows = (
        await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.create_time, AgentMessage.message_id)
        )
    ).scalars().all()
    return ok(
        {
            "items": [
                {
                    "role": m.role,
                    "content": m.content,
                    "meta": m.meta or {},
                    "create_time": m.create_time.isoformat(),
                }
                for m in rows
            ]
        }
    )


@router.delete("/agent/sessions/{session_id}")
async def delete_session(
    session_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """会话软删（消息保留）：台账/用药流水不在会话内，删除会话不影响任何追溯记录。"""
    session = await db.get(AgentSession, session_id)
    if session is None or session.is_deleted:
        raise ApiError(E_NOT_FOUND, "会话不存在")
    if session.user_id != user.user_id:
        raise ApiError(E_FORBIDDEN, "无权限操作")
    session.is_deleted = True
    await db.commit()
    return ok({"deleted": True})
