"""F06 喂食中心 — 饲喂执行工作台与数据反馈闭环（03-API 六节 6.1/6.2/6.7/6.8/6.9 + 十四节）。

三条容易写错的口径，都在这里定死：
- **planned_kg 快照**：登记那一刻写入，之后改配方/改存栏都不回写——日历的历史对照必须稳定；
- **料肉比拒绝计算而非返回 0**（EX-6b）：缺称重、缺出栏体重、增重非正，各给各的理由文案；
- **期初体重口径** = 该批次首次称重均重 × 出栏时存栏头数（不是出栏均重减初生重）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func as fn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.farm import resolve_pen
from app.api.rules import BATCH_STAGES
from app.core.db import get_db
from app.core.response import E_FORBIDDEN, E_NOT_FOUND, E_CONFIRM_NEEDED, E_VALIDATION, ApiError, ok
from app.core.security import get_current_user, require_farmer
from app.core.timeutil import today_cst
from app.models import Farm, FeedFormula, FeedingRecord, LedgerRecord, LifecycleEvent, Pen, User, WeightRecord
from app.services import feed_engine as fe
from app.services import weather as wx

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])

CN_NUM = {"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def _today() -> date:
    return today_cst()


def _check_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ApiError(E_VALIDATION, "日期格式需为 yyyy-MM-dd", {"field": field}) from None


async def _my_pens(db: AsyncSession, user: User) -> list[Pen]:
    farm_ids = (
        await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id, Farm.is_deleted.is_(False)))
    ).scalars().all()
    if not farm_ids:
        return []
    return list(
        (
            await db.execute(
                select(Pen).where(Pen.farm_id.in_(farm_ids), Pen.is_deleted.is_(False)).order_by(Pen.pen_id)
            )
        ).scalars().all()
    )


async def _latest_slaughter(db: AsyncSession, pen_id: int) -> dict[str, Any] | None:
    """出栏信息取生命周期事件（F07 唯一写入方；mock 的 slaughter_record 切片表已停写）。"""
    row = (
        await db.execute(
            select(LifecycleEvent)
            .where(LifecycleEvent.pen_id == pen_id, LifecycleEvent.event_type == "slaughter")
            .order_by(LifecycleEvent.event_date.desc(), LifecycleEvent.event_id.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        return None
    detail = row.payload or {}
    head = int(detail.get("male_count") or 0) + int(detail.get("female_count") or 0)
    avg = detail.get("avg_weight_kg")
    return {
        "slaughter_date": row.event_date,
        "head_count": head,
        "total_weight_kg": fe.round2(float(avg) * head) if avg and head else None,
    }


# ---------- 6.1 饲喂计划 ----------

@router.get("/pen/{pen_id}/feed-plan")
async def get_feed_plan(
    pen_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    pen, farm = await resolve_pen(db, user, pen_id)
    temp_max = await wx.today_max_temp_for_pen(db, farm)  # 只读缓存，不触发外部调用
    plan = await fe.calc_daily_plan(db, pen, temp_max)
    return ok({"pen_id": pen.pen_id, **plan})


# ---------- 6.2 饲喂登记 ----------

class FeedingBody(BaseModel):
    pen_id: int
    feed_date: str = Field(default="")
    feed_type: str = "concentrate"
    amount_kg: float | None = None
    as_planned: bool = False
    confirm: bool = False
    remark: str = Field(default="")


@router.post("/feeding/record")
async def create_feeding_record(
    body: FeedingBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    pen, farm = await resolve_pen(db, user, body.pen_id)
    feed_date = _check_date(body.feed_date or _today().isoformat(), "feed_date")
    if body.feed_type not in fe.FEED_TYPES:
        raise ApiError(E_VALIDATION, "无效的饲料类型", {"field": "feed_type"})

    plan = await fe.calc_daily_plan(db, pen, await wx.today_max_temp_for_pen(db, farm))
    ref = plan.get("daily_plan_kg")
    amount = body.amount_kg
    if body.as_planned and amount is None:
        if ref is None:
            raise ApiError(E_VALIDATION, "暂无饲喂计划，无法按计划打卡（请先生成配方或手动填写数量）", {"field": "as_planned"})
        amount = ref
    if amount is None:
        raise ApiError(E_VALIDATION, "请填写投喂量或选择按计划打卡", {"field": "amount_kg"})
    if float(amount) <= 0:
        raise ApiError(E_VALIDATION, "投喂量需大于 0", {"field": "amount_kg"})
    amount = fe.round2(float(amount))

    # EX-14：无计划基准（手动圈）不触发确认——没有锚点就没有"3 倍"可言
    if ref is not None and amount > ref * 3 and not body.confirm:
        raise ApiError(
            E_CONFIRM_NEEDED,
            "投喂量明显偏大（超参考量 3 倍），确认无误请再点一次确认",
            {"confirm_type": "feed_over", "reference_kg": ref},
        )

    existing = (
        await db.execute(
            select(FeedingRecord).where(
                FeedingRecord.pen_id == pen.pen_id,
                FeedingRecord.feed_date == feed_date,
                FeedingRecord.feed_type == body.feed_type,
            )
        )
    ).scalars().first()
    if existing is None:
        existing = FeedingRecord(pen_id=pen.pen_id, feed_date=feed_date, feed_type=body.feed_type)
        db.add(existing)
        db.add(
            LedgerRecord(
                farm_id=pen.farm_id, pen_id=pen.pen_id, record_type="feed",
                summary=f"{pen.pen_name} 饲喂登记 {feed_date} {amount}kg",
                record_ext={"feed_type": body.feed_type, "planned_kg": ref, "as_planned": body.as_planned},
            )
        )
    existing.amount_kg = amount
    existing.planned_kg = ref  # 快照：此后改配方/改存栏都不回写
    existing.as_planned = bool(body.as_planned)
    existing.remark = (body.remark or "").strip()[:128]
    await db.commit()
    return ok({"feed_record_id": existing.feed_record_id, "planned_kg": ref, "amount_kg": amount, "as_planned": bool(body.as_planned)})


@router.get("/feeding/record")
async def list_feeding_records(
    pen_id: int = Query(...),
    start_date: str = Query(default="", alias="from"),
    end_date: str = Query(default="", alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=31, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, pen_id)
    q = select(FeedingRecord).where(FeedingRecord.pen_id == pen.pen_id)
    if start_date:
        q = q.where(FeedingRecord.feed_date >= _check_date(start_date, "from"))
    if end_date:
        q = q.where(FeedingRecord.feed_date <= _check_date(end_date, "to"))
    total = (await db.execute(select(fn.count()).select_from(q.subquery()))).scalar_one()
    rows = (
        await db.execute(q.order_by(FeedingRecord.feed_date.desc(), FeedingRecord.feed_record_id.desc()).offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()
    return ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [
            {
                "feed_record_id": r.feed_record_id, "pen_id": r.pen_id,
                "feed_date": r.feed_date.isoformat(), "feed_type": r.feed_type,
                "amount_kg": fe.to_float(r.amount_kg), "planned_kg": None if r.planned_kg is None else fe.to_float(r.planned_kg),
                "as_planned": r.as_planned, "remark": r.remark, "create_time": r.create_time.isoformat(),
            }
            for r in rows
        ],
    })


# ---------- 6.7 称重登记 ----------

class WeightBody(BaseModel):
    pen_id: int
    weigh_date: str = Field(default="")
    avg_weight_kg: float | None = None
    sample_count: int | None = None
    confirm: bool = False
    remark: str = Field(default="")


@router.post("/feeding/weight")
async def create_weight_record(
    body: WeightBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, body.pen_id)
    weigh_date = _check_date(body.weigh_date or _today().isoformat(), "weigh_date")
    if body.avg_weight_kg is None or not (0 < float(body.avg_weight_kg) <= 500):
        raise ApiError(E_VALIDATION, "体重需在 0~500kg 之间", {"field": "avg_weight_kg"})
    if not body.sample_count or int(body.sample_count) <= 0:
        raise ApiError(E_VALIDATION, "请填写称重头数", {"field": "sample_count"})
    avg = fe.round2(float(body.avg_weight_kg))

    prev = (
        await db.execute(
            select(WeightRecord)
            .where(WeightRecord.pen_id == pen.pen_id, WeightRecord.weigh_date < weigh_date)
            .order_by(WeightRecord.weigh_date.desc(), WeightRecord.weight_id.desc())
            .limit(1)
        )
    ).scalars().first()

    # EX-15：较上次骤降 >10% 需确认（可能是录入错，也可能是真掉膘——两者处置完全不同）
    if prev and avg < fe.to_float(prev.avg_weight_kg) * 0.9 and not body.confirm:
        drop_pct = fe.round2((1 - avg / fe.to_float(prev.avg_weight_kg)) * 100)
        raise ApiError(
            E_CONFIRM_NEEDED,
            f"均重较上次下降 {drop_pct}%，请确认录入是否正确",
            {"confirm_type": "weight_drop", "prev_avg_weight_kg": fe.to_float(prev.avg_weight_kg), "drop_pct": drop_pct},
        )

    gain = None
    if prev:
        gain = fe.round4((avg - fe.to_float(prev.avg_weight_kg)) / fe.days_between(prev.weigh_date, weigh_date))
    standard = fe.load_seed()["standard_daily_gain"].get(pen.variety_code or "")
    if pen.batch_stage in ("gestation", "lactation"):
        standard = None  # 母畜妊娠/哺乳不适用批次日增重标准
    vs = None
    if gain is not None and standard:
        ratio = fe.round3(gain / float(standard))
        vs = {"standard": float(standard), "ratio": ratio, "level": "low" if ratio < 0.8 else "normal"}

    existing = (
        await db.execute(
            select(WeightRecord).where(WeightRecord.pen_id == pen.pen_id, WeightRecord.weigh_date == weigh_date)
        )
    ).scalars().first()
    if existing is None:
        existing = WeightRecord(pen_id=pen.pen_id, weigh_date=weigh_date)
        db.add(existing)
    existing.avg_weight_kg = avg
    existing.sample_count = int(body.sample_count)
    existing.daily_gain_kg = gain
    existing.remark = (body.remark or "").strip()[:128]
    await db.commit()
    return ok({"weight_id": existing.weight_id, "daily_gain_kg": gain, "vs_standard": vs})


# ---------- 6.8 日增重曲线 ----------

@router.get("/pen/{pen_id}/daily-gain")
async def get_daily_gain(pen_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, pen_id)
    rows = (
        await db.execute(
            select(WeightRecord).where(WeightRecord.pen_id == pen.pen_id).order_by(WeightRecord.weigh_date, WeightRecord.weight_id)
        )
    ).scalars().all()
    standard = fe.load_seed()["standard_daily_gain"].get(pen.variety_code or "")
    if pen.batch_stage in ("gestation", "lactation"):
        standard = None
    return ok({
        "points": [{"date": r.weigh_date.isoformat(), "avg_weight": fe.to_float(r.avg_weight_kg)} for r in rows],
        "daily_gain": [
            {"date": r.weigh_date.isoformat(), "gain": fe.to_float(r.daily_gain_kg)}
            for r in rows if r.daily_gain_kg is not None
        ],
        "standard_line": None if standard is None else float(standard),
    })


# ---------- 6.9 料肉比结算 ----------

@router.get("/pen/{pen_id}/feed-conversion")
async def get_feed_conversion(pen_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, pen_id)
    active = (
        await db.execute(select(FeedFormula).where(FeedFormula.pen_id == pen.pen_id, FeedFormula.is_active.is_(True)))
    ).scalars().first()
    avg_cost = None if active is None or active.cost_per_head_day is None else fe.to_float(active.cost_per_head_day)
    slaughter = await _latest_slaughter(db, pen.pen_id)
    total_feed = 0.0
    if slaughter is not None:  # 只累加出栏日之前的投喂量
        total_feed = fe.to_float(
            (
                await db.execute(
                    select(fn.coalesce(fn.sum(FeedingRecord.amount_kg), 0)).where(
                        FeedingRecord.pen_id == pen.pen_id,
                        FeedingRecord.feed_date <= slaughter["slaughter_date"],
                    )
                )
            ).scalar_one()
        )

    if slaughter is None:
        return ok({"total_feed_kg": None, "total_gain_kg": None, "fcr": None, "reason": "批次未出栏", "avg_cost_per_head_day": avg_cost})

    first = (
        await db.execute(
            select(WeightRecord)
            .where(WeightRecord.pen_id == pen.pen_id)
            .order_by(WeightRecord.weigh_date, WeightRecord.weight_id)
            .limit(1)
        )
    ).scalars().first()
    base = {
        "total_feed_kg": total_feed, "slaughter_date": slaughter["slaughter_date"].isoformat(),
        "avg_cost_per_head_day": avg_cost, "head_count": slaughter["head_count"],
        "slaughter_total_weight_kg": slaughter["total_weight_kg"],
    }
    if first is None:  # EX-6b：缺称重数据绝不返回 0，明确告知无法计算
        return ok({**base, "total_gain_kg": None, "fcr": None, "reason": "缺少称重数据，无法计算料肉比"})
    if slaughter["total_weight_kg"] is None:
        return ok({**base, "total_gain_kg": None, "fcr": None, "reason": "缺少出栏体重，无法计算料肉比",
                   "first_weigh_date": first.weigh_date.isoformat(), "first_avg_weight_kg": fe.to_float(first.avg_weight_kg)})

    base_weight = fe.round2(fe.to_float(first.avg_weight_kg) * slaughter["head_count"])  # 期初体重口径
    gain = fe.round2(slaughter["total_weight_kg"] - base_weight)
    if gain <= 0:
        return ok({**base, "total_gain_kg": gain, "fcr": None, "reason": "出栏总重未高于期初体重口径，无法计算料肉比",
                   "first_weigh_date": first.weigh_date.isoformat(), "first_avg_weight_kg": fe.to_float(first.avg_weight_kg)})
    return ok({
        **base, "total_gain_kg": gain, "fcr": fe.round2(total_feed / gain),
        "first_weigh_date": first.weigh_date.isoformat(), "first_avg_weight_kg": fe.to_float(first.avg_weight_kg),
    })


# ---------- 十四节 喂食日历汇总 ----------

@router.get("/feeding/overview")
async def feeding_overview(
    date_str: str = Query(default="", alias="date"),
    farm_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    day = _check_date(date_str, "date") if date_str else _today()
    pens = await _my_pens(db, user)
    if farm_id is not None:
        if farm_id not in {p.farm_id for p in pens}:
            raise ApiError(E_FORBIDDEN, "无权限访问该场区的饲喂汇总")
        pens = [p for p in pens if p.farm_id == farm_id]
    farm_cache: dict[int, Farm | None] = {}
    for p in pens:
        farm_cache[p.farm_id] = await db.get(Farm, p.farm_id)
    # 只读本人（或本场）圈舍的记录：此前按日期全表取，再靠上层丢弃别人的行
    records = (
        await db.execute(
            select(FeedingRecord).where(FeedingRecord.feed_date == day, FeedingRecord.pen_id.in_([p.pen_id for p in pens] or [0]))
        )
    ).scalars().all()
    by_pen: dict[int, list[FeedingRecord]] = {}
    for r in records:
        by_pen.setdefault(r.pen_id, []).append(r)

    items: list[dict[str, Any]] = []
    for pen in pens:
        temp_max = await wx.today_max_temp_for_pen(db, farm_cache.get(pen.farm_id))
        plan = await fe.calc_daily_plan(db, pen, temp_max)
        rows = by_pen.get(pen.pen_id) or []
        registered = bool(rows)
        # 当日计划以**登记的快照**为准（配方"次日生效"，不让改配方回头改写今天）
        snapshot = next((fe.to_float(r.planned_kg) for r in sorted(rows, key=lambda x: x.feed_record_id) if r.planned_kg is not None), None)
        items.append({
            "pen_id": pen.pen_id,
            "pen_name": pen.pen_name,
            "planned_kg": snapshot if snapshot is not None else plan.get("daily_plan_kg"),
            "actual_kg": fe.round2(sum(fe.to_float(r.amount_kg) for r in rows)) if registered else None,
            "registered": registered,
            "plan_source": plan.get("plan_source"),
            "basis": plan.get("basis"),
        })
    return ok({"date": day.isoformat(), "items": items})


# ---------- 6.10 演示数据注入（真后端版，替代前端 mock 的 seedFeedingDemoData）----------

DEMO_REMARK = "演示数据（喂食中心载入）"


@router.post("/feeding/seed-demo")
async def seed_feeding_demo(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """给本人名下"有存栏但没饲喂记录"的圈灌一周演示数据。

    与 mock 版的三点差异，都是刻意的：

    1. **计划量由引擎现算**（`calc_daily_plan`：生效配方 → 营养标准 → 无计划），
       所以注入的 planned_kg 与该圈真实口径一致，不是硬编码常量；
    2. **不注入出栏记录**：真后端的出栏要过休药期拦截与耳标选择（F07/F14），
       demo 若绕过这两道去写 stock_change，就是在制造"看起来真"的假台账；
    3. **幂等且不覆盖**：名下所有圈都已有饲喂记录时直接返回，不重复注入、不改写用户数据。

    每条记录都带 `remark=演示数据（喂食中心载入）`，台账同样落这条——
    这样"哪些是演示的"在数据库里可查，而不是只写在 UI 上。
    """
    farm_ids = list((await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id, Farm.is_deleted == False))).scalars())  # noqa: E712
    pens = list((await db.execute(select(Pen).where(Pen.farm_id.in_(farm_ids or [0]), Pen.is_deleted == False))).scalars())  # noqa: E712
    if not pens:
        raise ApiError(E_VALIDATION, "请先创建养殖场与圈舍", {"field": "pen_id"})

    recorded = set((await db.execute(select(FeedingRecord.pen_id).distinct())).scalars())
    pen = next((p for p in pens if p.male_count + p.female_count > 0 and p.pen_id not in recorded), None)
    pen = pen or next((p for p in pens if p.pen_id not in recorded), None)
    if pen is None:
        return ok({"seeded": False, "pen_id": pens[0].pen_id, "pen_name": pens[0].pen_name,
                   "message": "名下所有圈舍均已有饲喂记录，演示数据未重复注入"})

    stock = pen.male_count + pen.female_count
    farm = await db.get(Farm, pen.farm_id)
    plan = await fe.calc_daily_plan(db, pen, await wx.today_max_temp_for_pen(db, farm))
    planned = plan.get("daily_plan_kg")
    today = _today()
    dstr = lambda off: today + timedelta(days=off)  # noqa: E731

    for off in range(-9, 1):
        is_today = off == 0
        amount = fe.round2(planned * (0.8 if is_today else 1)) if planned else fe.round2(stock * 2)
        ref = None if not planned else fe.round2(planned)
        as_planned = bool(planned) and not is_today
        db.add(FeedingRecord(
            pen_id=pen.pen_id, feed_date=dstr(off), feed_type="concentrate", amount_kg=amount,
            planned_kg=ref, as_planned=as_planned, remark=DEMO_REMARK,
        ))
        db.add(LedgerRecord(
            farm_id=pen.farm_id, pen_id=pen.pen_id, record_type="feed",
            summary=f"{pen.pen_name} 饲喂登记 {dstr(off).isoformat()} {amount}kg",
            record_ext={"feed_type": "concentrate", "planned_kg": ref, "as_planned": as_planned, "demo": True},
        ))

    is_piglet = pen.variety_code == "pig_piglet"
    weighs: list[tuple[int, float]] = [(-25, 15.0), (-15, 18.5), (-5, 22.0)] if is_piglet else [(-25, 48.0), (-15, 53.0), (-5, 58.0)]
    prev: tuple[float, date] | None = None
    for off, w in weighs:
        day = dstr(off)
        gain = None if prev is None else fe.round4((w - prev[0]) / max(1, fe.days_between(prev[1], day)))
        db.add(WeightRecord(
            pen_id=pen.pen_id, weigh_date=day, avg_weight_kg=w,
            sample_count=min(stock, 10), daily_gain_kg=gain, remark=DEMO_REMARK,
        ))
        prev = (w, day)

    await db.commit()
    return ok({
        "seeded": True, "pen_id": pen.pen_id, "pen_name": pen.pen_name,
        "plan_source": plan.get("plan_source"),
        "message": f"已为「{pen.pen_name}」载入演示数据：近 10 天饲喂与 3 次称重（按 F06 口径不注入出栏记录）",
    })
