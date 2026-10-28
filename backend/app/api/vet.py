"""F10 HITL 兽医复核 — LangGraph interrupt 的真实后端（03-API 11.1/11.2 + PRD 3.5.8）。

工作流语义（01-架构 9.4）：
- 高危病例在问诊图的 `hitl_gate` 节点 `interrupt()` 挂起，checkpoint 落 PostgreSQL
  （schema `langgraph`），thread_id 记在 vet_reviews.thread_id；
- 兽医复核 → `Command(resume=复核结果)` 唤醒同一 thread，从 hitl_gate 续跑
  `record_review` 落 vet_review 留痕台账；
- **超时降级不走 resume**（EX-06）：≥48h 的 pending 惰性置 timeout，checkpoint 弃用，
  避免唤醒旧工作流造成双写。disease 台账在问诊时已落，"未经兽医复核"角标由读侧派生。

角色边界：vet 可复核；admin 仅只读放宽（徽标计数/超时督办下钻，无复核权）；farmer 不可见。
响应为最小字段集白名单，不含养殖户 phone/user_id（RK-2：新增字段须重过安全评审）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import get_graph, thread_config
from app.agents.kb import FORBIDDEN_MENTIONS
from app.core.db import get_db
from app.core.response import E_NOT_FOUND, E_ROLE_FORBIDDEN, E_VALIDATION, ApiError, ok
from app.core.security import get_current_user
from app.models import Farm, MedicineLib, Pen, User, VetReview

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])

REVIEW_WARN_HOURS = 24    # EX-05：≥24h 置顶标红（含等号）
REVIEW_TIMEOUT_HOURS = 48  # EX-06：≥48h 惰性降级（含等号）


def _hours_since(dt: datetime | None) -> float:
    if dt is None:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


async def sweep_timeout_reviews(db: AsyncSession) -> int:
    """EX-06 惰性降级扫描（切真后由 APScheduler 定时任务承接，本函数保留为入口兜底）。

    只改状态不 resume：台账在问诊时已落，读侧按 review_status 派生"未经兽医复核"角标。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=REVIEW_TIMEOUT_HOURS)
    rows = (
        await db.execute(
            select(VetReview).where(VetReview.review_status == "pending", VetReview.create_time < cutoff)
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.review_status = "timeout"
        r.review_time = now  # 复用记降级时间（F10 FR-04 具体化，DDL 无独立列）
    if rows:
        await db.commit()
    return len(rows)


@router.get("/vet/pending-cases")
async def pending_cases(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    if user.role not in ("vet", "admin"):
        raise ApiError(E_ROLE_FORBIDDEN, "仅兽医与管理员可查看待复核病例")
    await sweep_timeout_reviews(db)  # 入口先降级，任何视图看到真实状态

    rows = (
        await db.execute(select(VetReview).where(VetReview.review_status == "pending"))
    ).scalars().all()
    farm_ids = {r.farm_id for r in rows}
    pen_ids = {r.pen_id for r in rows}
    farms = {
        f.farm_id: f.farm_name
        for f in (await db.execute(select(Farm).where(Farm.farm_id.in_(farm_ids or {0})))).scalars().all()
    }
    pens = {
        p.pen_id: p.pen_name
        for p in (await db.execute(select(Pen).where(Pen.pen_id.in_(pen_ids or {0})))).scalars().all()
    }

    items: list[dict[str, Any]] = []
    for r in rows:
        raw = _hours_since(r.create_time)
        items.append(
            {
                "review_id": r.review_id,
                "alert_id": r.alert_id,
                "session_id": r.session_id,
                "waiting_hours": round(raw * 10) / 10,  # 展示与阈值同值取整
                "timeout_level": "warn" if raw >= REVIEW_WARN_HOURS else "normal",
                "original_diagnosis": r.original_diagnosis,
                "risk_level": r.risk_level,
                "farm_name": farms.get(r.farm_id, ""),  # 服务端派生：vet 无租户上下文，不能让前端反查
                "pen_name": pens.get(r.pen_id, ""),
                "create_time": r.create_time.isoformat() if r.create_time else "",
            }
        )
    # 超时置顶标红，其余按等待时长倒序
    items.sort(key=lambda x: (0 if x["timeout_level"] == "warn" else 1, -x["waiting_hours"]))
    return ok({"items": items})


class ReviewReq(BaseModel):
    review_id: int
    action: str = Field(pattern="^(approve|reject)$")
    review_opinion: str = ""
    revised_diagnosis: str = ""
    revised_med: str = ""


async def _validate_revised_med(db: AsyncSession, revised_med: str) -> str | None:
    """驳回方案的兽药库校验（PRD 3.5.8 V1.7：底线对人和 AI 一视同仁）。"""
    if any(w in revised_med for w in FORBIDDEN_MENTIONS):
        return "违禁药品不可用于复核方案"
    rows = (await db.execute(select(MedicineLib))).scalars().all()
    if any(m.is_forbidden and m.med_name and m.med_name in revised_med for m in rows):
        return "违禁药品不可用于复核方案"
    if not any(not m.is_forbidden and m.med_name and m.med_name in revised_med for m in rows):
        return "兽药库中不存在该药品"
    return None


@router.post("/vet/review")
async def submit_review(
    body: ReviewReq, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    if user.role != "vet":
        raise ApiError(E_ROLE_FORBIDDEN, "仅执业兽医可执行病例复核")
    await sweep_timeout_reviews(db)  # 复核前二次扫描：超时病例此刻已 timeout → 走 40401

    review = await db.get(VetReview, body.review_id)
    if review is None:
        raise ApiError(E_NOT_FOUND, "病例不存在")
    if review.review_status == "timeout":
        raise ApiError(E_NOT_FOUND, "病例已超时降级，无法复核")
    if review.review_status != "pending":
        raise ApiError(E_NOT_FOUND, "病例已复核，无法重复操作")

    # 校验顺序（F10 FR-03）：必填 → 药库 → 恢复工作流；任一步失败零部分状态
    opinion = body.review_opinion.strip()
    if len(opinion) > 500:
        raise ApiError(E_VALIDATION, "复核意见不能超过 500 字", {"field": "review_opinion"})
    revised_diagnosis = body.revised_diagnosis.strip()
    revised_med = body.revised_med.strip()
    if body.action == "reject":
        if not opinion:
            raise ApiError(E_VALIDATION, "请填写复核意见", {"field": "review_opinion"})
        if not revised_diagnosis:
            raise ApiError(E_VALIDATION, "请填写修改后诊断", {"field": "revised_diagnosis"})
        if not revised_med:
            raise ApiError(E_VALIDATION, "请填写修改后用药", {"field": "revised_med"})
        med_err = await _validate_revised_med(db, revised_med)
        if med_err:
            raise ApiError(E_VALIDATION, med_err, {"field": "revised_med"})

    if not review.thread_id:
        # 无 thread_id = 不是本图挂起的病例（历史数据/mock 迁移），无法 resume
        raise ApiError(E_NOT_FOUND, "该病例无可恢复的工作流状态（缺少 checkpoint 定位键）")

    graph = get_graph()
    config = thread_config(review.thread_id)
    snapshot = await graph.aget_state(config)
    if not snapshot or "hitl_gate" not in (snapshot.next or ()):
        raise ApiError(E_NOT_FOUND, "工作流状态已丢失或已恢复，无法重复复核")

    started = time.monotonic()
    result = await graph.ainvoke(
        Command(
            resume={
                "action": body.action,
                "review_opinion": opinion,
                "revised_diagnosis": revised_diagnosis,
                "revised_med": revised_med,
                "vet_user_id": user.user_id,
            }
        ),
        config,
    )
    logger_resume_ms = int((time.monotonic() - started) * 1000)

    await db.refresh(review)
    return ok(
        {
            "review_id": review.review_id,
            "review_status": review.review_status,
            "ledger_record_id": result.get("review_ledger_id") or 0,
            "resume_ms": logger_resume_ms,  # 可观测：resume 续跑耗时（01-架构十四节指标）
        }
    )
