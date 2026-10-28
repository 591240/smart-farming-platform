"""F07 全生命周期管理与出栏休药期（03-API 五节；状态机/提醒/休药期与 mock 同规则）。

- 母畜状态机：open→bred→pregnant→nursing→open…（40003 拦截非法跳变，状态只经事件变更）
- 分娩：活仔写入存栏（birth 流水）+ 自动建档（F14）+ 断奶提醒
- 出栏：休药期硬校验（40001 三要素）→ 整栏自动清档 / 部分必选耳标 → 三表联动
- 提醒：预产期（due−3）/ 断奶（分娩+lactation_days）/ 免疫程序（anchor=birth/due_date），惰性幂等生成
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func as fn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.farm import (
    add_individual,
    exit_individuals,
    resolve_pen,
    write_stock_change,
)
from app.api.rules import (
    BREED_STATUS_LABEL,
    BATCH_STAGES,
    EVENT_ALLOWED_FROM,
    EVENT_LABEL,
    INDIVIDUAL_EVENTS,
    MEDICINE_LIB_SEED,
    lifecycle_params,
)
from app.core.db import get_db
from app.core.response import (
    E_INVALID_TRANSITION,
    E_NEGATIVE_STOCK,
    E_NOT_FOUND,
    E_VALIDATION,
    E_WITHDRAWAL,
    ApiError,
    ok,
)
from app.core.security import get_current_user, require_farmer
from app.core.timeutil import today_cst
from app.models import (
    AnimalIndividual,
    BreedingFemale,
    Farm,
    LedgerRecord,
    LifecycleEvent,
    MedicineLib,
    MedicineUsage,
    Pen,
    Reminder,
    StockChange,
    User,
)

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])


def _today() -> date:
    return today_cst()


def _add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def _signed_days(a: date, b: date) -> int:
    return (b - a).days


async def resolve_female(db: AsyncSession, user: User, female_id: int) -> tuple[BreedingFemale, Pen, Farm]:
    female = await db.get(BreedingFemale, female_id)
    if female is None or female.is_deleted:
        raise ApiError(E_NOT_FOUND, "该母畜档案不存在或已删除")
    pen, farm = await resolve_pen(db, user, female.pen_id)
    return female, pen, farm


# ---------- FR-01 母畜档案 CRUD ----------

class FemaleBody(BaseModel):
    pen_id: int
    tag_no: str = Field(default="")
    parity: int = 0
    breed_status: str = Field(default="open")
    breed_date: str | None = None
    remark: str = Field(default="")


@router.post("/breeding-female")
async def create_female(body: FemaleBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, body.pen_id)
    tag_no = body.tag_no.strip()
    if not tag_no:
        raise ApiError(E_VALIDATION, "请填写耳标编号", {"field": "tag_no"})
    dup = (
        await db.execute(
            select(fn.count()).select_from(BreedingFemale).where(
                BreedingFemale.pen_id == body.pen_id, BreedingFemale.tag_no == tag_no, BreedingFemale.is_deleted == False  # noqa: E712
            )
        )
    ).scalar_one()
    if dup:
        raise ApiError(E_VALIDATION, f"该圈舍已存在编号 {tag_no}", {"field": "tag_no"})
    if body.parity < 0:
        raise ApiError(E_VALIDATION, "胎次需为非负整数", {"field": "parity"})
    status = body.breed_status or "open"
    if status not in BREED_STATUS_LABEL:
        raise ApiError(E_VALIDATION, "无效的繁殖状态", {"field": "breed_status"})

    breed_date = _parse_date(body.breed_date) if body.breed_date else None
    due_date = None
    if breed_date and status in ("bred", "pregnant"):
        due_date = _add_days(breed_date, lifecycle_params(pen.species_code)["gestation_days"])

    female = BreedingFemale(
        pen_id=body.pen_id, tag_no=tag_no, variety_code=pen.variety_code, parity=body.parity,
        breed_status=status, breed_date=breed_date, due_date=due_date, remark=body.remark.strip(),
    )
    db.add(female)
    await db.flush()
    # 补录配种日期即生成 breeding 事件（台账可溯源，与事件驱动口径一致）
    if breed_date and status in ("bred", "pregnant"):
        db.add(LifecycleEvent(female_id=female.female_id, pen_id=pen.pen_id, event_type="breeding", event_date=breed_date,
                              payload={"补录建档": "是"}))
        db.add(LedgerRecord(
            farm_id=pen.farm_id, pen_id=pen.pen_id, record_type="lifecycle",
            summary=f"{pen.pen_name} {tag_no} 建档补录配种（{breed_date.isoformat()}{('，预产期 ' + due_date.isoformat()) if due_date else ''}）",
            stock_change_id=0, alert_id=0,
            record_ext={"tag_no": tag_no, "event_type": "breeding", "event_date": breed_date.isoformat(),
                        **({"due_date": due_date.isoformat()} if due_date else {})},
        ))
    await db.commit()
    return ok({"female_id": female.female_id, "breed_status": female.breed_status,
               "breed_status_label": BREED_STATUS_LABEL[status], "due_date": due_date.isoformat() if due_date else None})


def _parse_date(raw: str | None) -> date:
    if not raw or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ApiError(E_VALIDATION, "日期格式不正确", {"field": "event_date"})
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise ApiError(E_VALIDATION, "日期格式不正确", {"field": "event_date"}) from e


@router.get("/breeding-female")
async def list_females(
    pen_id: int | None = Query(default=None),
    farm_id: int | None = Query(default=None),
    breed_status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    q = (
        select(BreedingFemale, Pen)
        .join(Pen, Pen.pen_id == BreedingFemale.pen_id)
        .join(Farm, Farm.farm_id == Pen.farm_id)
        .where(BreedingFemale.is_deleted == False, Farm.user_id == user.user_id, Farm.is_deleted == False)  # noqa: E712
    )
    if pen_id:
        q = q.where(BreedingFemale.pen_id == pen_id)
    if farm_id:
        # 已由 Farm.user_id 联表限定租户：传别人的 farm_id 只会得到空列表，不会越权
        q = q.where(Pen.farm_id == farm_id)
    if breed_status and breed_status in BREED_STATUS_LABEL:
        q = q.where(BreedingFemale.breed_status == breed_status)
    q = q.order_by(BreedingFemale.create_time.desc(), BreedingFemale.female_id.desc())
    total = (await db.execute(select(fn.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).all()
    items = [
        {
            "female_id": f.female_id,
            "pen_id": f.pen_id,
            "pen_name": p.pen_name,
            "tag_no": f.tag_no,
            "variety_code": f.variety_code,
            "parity": f.parity,
            "breed_status": f.breed_status,
            "breed_status_label": BREED_STATUS_LABEL[f.breed_status],
            "breed_date": f.breed_date.isoformat() if f.breed_date else None,
            "due_date": f.due_date.isoformat() if f.due_date else None,
            "remark": f.remark,
            "create_time": f.create_time.isoformat(),
        }
        for f, p in rows
    ]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


class FemaleUpdateBody(BaseModel):
    tag_no: str | None = None
    parity: int | None = None
    remark: str | None = None
    breed_status: str | None = None  # 仅用于拦截：状态只经事件变更（EX-12）
    breed_date: str | None = None
    due_date: str | None = None


@router.put("/breeding-female/{female_id}")
async def update_female(female_id: int, body: FemaleUpdateBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    female, _, _ = await resolve_female(db, user, female_id)
    # 口径：breed_status/breed_date/due_date 仅事件驱动，PUT 直接改状态 → 40003（EX-12 后端表达）
    if body.breed_status is not None or body.breed_date is not None or body.due_date is not None:
        raise ApiError(E_INVALID_TRANSITION, "繁殖状态仅能通过登记生命周期事件变更，请走事件登记")
    if body.tag_no is not None:
        tag_no = body.tag_no.strip()
        if not tag_no:
            raise ApiError(E_VALIDATION, "请填写耳标编号", {"field": "tag_no"})
        dup = (
            await db.execute(
                select(fn.count()).select_from(BreedingFemale).where(
                    BreedingFemale.pen_id == female.pen_id, BreedingFemale.tag_no == tag_no,
                    BreedingFemale.is_deleted == False, BreedingFemale.female_id != female_id,  # noqa: E712
                )
            )
        ).scalar_one()
        if dup:
            raise ApiError(E_VALIDATION, f"该圈舍已存在编号 {tag_no}", {"field": "tag_no"})
        female.tag_no = tag_no
    if body.parity is not None:
        if body.parity < 0:
            raise ApiError(E_VALIDATION, "胎次需为非负整数", {"field": "parity"})
        female.parity = body.parity
    if body.remark is not None:
        female.remark = body.remark.strip()
    await db.commit()
    return ok({"female_id": female.female_id, "updated": True})


@router.delete("/breeding-female/{female_id}")
async def delete_female(female_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    female, _, _ = await resolve_female(db, user, female_id)
    female.is_deleted = True  # 逻辑删除，事件历史保留（档案性质）
    await db.commit()
    return ok({"female_id": female.female_id, "deleted": True})


# ---------- FR-02/03 生命周期事件（状态机）+ 提醒生成 ----------

class LifecycleBody(BaseModel):
    event_type: str = Field(default="")
    female_id: int | None = None
    pen_id: int | None = None
    event_date: str = Field(default="")
    detail: dict[str, Any] | None = None
    remark: str = Field(default="")


async def _reminder_exists(db: AsyncSession, farm_id: int, rtype: str, female_id: int | None, remind_date: date) -> bool:
    result = await db.execute(
        select(fn.count()).select_from(Reminder).where(
            Reminder.farm_id == farm_id, Reminder.reminder_type == rtype,
            Reminder.female_id == female_id, Reminder.remind_date == remind_date,
        )
    )
    return result.scalar_one() > 0


def _push_reminder(db: AsyncSession, **kw: Any) -> None:
    db.add(Reminder(**kw))


async def _close_reminders(db: AsyncSession, female_id: int, types: list[str]) -> None:
    rows = (
        await db.execute(
            select(Reminder).where(Reminder.female_id == female_id, Reminder.reminder_type.in_(types), Reminder.status == "pending")  # noqa: E712
        )
    ).scalars().all()
    for r in rows:
        r.status = "done"


async def _close_weaning_for_pen(db: AsyncSession, pen_id: int) -> None:
    rows = (
        await db.execute(
            select(Reminder).where(Reminder.pen_id == pen_id, Reminder.reminder_type == "weaning", Reminder.status == "pending")  # noqa: E712
        )
    ).scalars().all()
    for r in rows:
        r.status = "done"


async def handle_birth(db: AsyncSession, female: BreedingFemale, pen: Pen, farm: Farm, event_date: date, detail: dict[str, Any] | None) -> None:
    """分娩：活仔存栏 + 自动建档（F14）+ 断奶提醒（口径 454/455）。"""
    live_male = max(0, int(detail.get("live_male", 0) or 0)) if detail else 0
    live_female = max(0, int(detail.get("live_female", 0) or 0)) if detail else 0
    dead_count = max(0, int(detail.get("dead_count", 0) or 0)) if detail else 0
    live = live_male + live_female
    if live <= 0 and dead_count <= 0:
        raise ApiError(E_VALIDATION, "请填写产仔数（活仔或死胎至少一项）", {"field": "detail.live_male"})
    female.breed_status = "nursing"
    if live > 0:
        await write_stock_change(db, pen, "birth", live_male, live_female,
                           f"{female.tag_no} 分娩活仔 {live} 头{('（死胎 ' + str(dead_count) + '）') if dead_count else ''}")
        # F14 强关联：分娩活仔自动生成耳标档案
        for _ in range(live_male):
            await add_individual(db, pen, await _next_auto_tag(db, pen, "male"), "male", "分娩自动建档")
        for _ in range(live_female):
            await add_individual(db, pen, await _next_auto_tag(db, pen, "female"), "female", "分娩自动建档")
    params = lifecycle_params(pen.species_code)
    remind_date = _add_days(event_date, params["lactation_days"])
    if not await _reminder_exists(db, farm.farm_id, "weaning", female.female_id, remind_date):
        _push_reminder(db, farm_id=farm.farm_id, pen_id=pen.pen_id, female_id=female.female_id, reminder_type="weaning",
                       remind_date=remind_date, title=f"{female.tag_no} 仔猪满 {params['lactation_days']} 天，可安排断奶",
                       content=f"{pen.pen_name} · 分娩日 {event_date.isoformat()}，断奶后仔猪转群须显式登记")
    await _close_reminders(db, female.female_id, ["due_date"])  # 预产期提醒自动办结


async def _next_auto_tag(db: AsyncSession, pen: Pen, gender: str) -> str:
    """自动编号（与 F14 auto-fill 同规则，单发场景）。"""
    letter = "M" if gender == "male" else "F"
    prefix = f"P{pen.pen_id}-{letter}"
    rows = await db.execute(
        select(AnimalIndividual.ear_tag_no).where(
            AnimalIndividual.farm_id == pen.farm_id, AnimalIndividual.is_deleted == False,  # noqa: E712
            AnimalIndividual.ear_tag_no.like(f"{prefix}%"),
        )
    )
    seq = 0
    for (tag,) in rows.all():
        suffix = tag[len(prefix):]
        if suffix.isdigit():
            seq = max(seq, int(suffix))
    return f"{prefix}{str(seq + 1).zfill(3)}"


async def ensure_reminders(db: AsyncSession, farm_id: int) -> None:
    """FR-04 惰性幂等扫描：预产期（due−3 亮相）+ 免疫程序（anchor=birth/due_date）。"""
    today = _today()
    # ① 预产期提醒：pregnant 且有 due_date，remind_date = due − 3
    sows = (
        await db.execute(
            select(BreedingFemale, Pen)
            .join(Pen, Pen.pen_id == BreedingFemale.pen_id)
            .where(Pen.farm_id == farm_id, BreedingFemale.is_deleted == False,  # noqa: E712
                   BreedingFemale.breed_status == "pregnant", BreedingFemale.due_date.is_not(None))
        )
    ).all()
    for female, pen in sows:
        remind_date = _add_days(female.due_date, -3)
        if remind_date > today:
            continue  # 未进 3 天窗口不生成（亮相规则）
        if not await _reminder_exists(db, farm_id, "due_date", female.female_id, remind_date):
            _push_reminder(db, farm_id=farm_id, pen_id=pen.pen_id, female_id=female.female_id, reminder_type="due_date",
                           remind_date=remind_date, title=f"{female.tag_no} 预产期 {female.due_date.isoformat()}（倒计时 {max(0, _signed_days(today, female.due_date))} 天）",
                           content=f"{pen.pen_name} · 配种日 {female.breed_date.isoformat() if female.breed_date else '—'}，预产期按 {lifecycle_params(pen.species_code)['gestation_days']} 天妊娠期推算")
    # ② 免疫程序提醒：anchor=birth 按出生事件日龄；anchor=due_date 按预产期推算
    params_by_species: dict[str, dict] = {}
    births = (
        await db.execute(
            select(LifecycleEvent, Pen)
            .join(Pen, Pen.pen_id == LifecycleEvent.pen_id)
            .where(Pen.farm_id == farm_id, LifecycleEvent.event_type == "birth")
        )
    ).all()
    for ev, pen in births:
        params = params_by_species.setdefault(pen.species_code, lifecycle_params(pen.species_code))
        for item in params["vaccination_program"]:
            if item["anchor"] != "birth":
                continue
            remind_date = _add_days(ev.event_date, item["timing_day"])
            if remind_date > today or await _reminder_exists(db, farm_id, "vaccine", None, remind_date):
                continue
            _push_reminder(db, farm_id=farm_id, pen_id=pen.pen_id, female_id=None, reminder_type="vaccine",
                           remind_date=remind_date, title=f"{item['vaccine']} 免疫提醒（{item['target']}）",
                           content=f"{pen.pen_name} · {ev.event_date.isoformat()} 分娩批次，{item['note']}")
    for female, pen in sows:
        params = params_by_species.setdefault(pen.species_code, lifecycle_params(pen.species_code))
        for item in params["vaccination_program"]:
            if item["anchor"] != "due_date" or not female.due_date:
                continue
            remind_date = _add_days(female.due_date, item["timing_day"])
            if remind_date > today or await _reminder_exists(db, farm_id, "vaccine", female.female_id, remind_date):
                continue
            _push_reminder(db, farm_id=farm_id, pen_id=pen.pen_id, female_id=female.female_id, reminder_type="vaccine",
                           remind_date=remind_date, title=f"{item['vaccine']} 免疫提醒（{item['target']}）",
                           content=f"{pen.pen_name} · {female.tag_no} 预产期 {female.due_date.isoformat()}，{item['note']}")


@router.post("/lifecycle/event")
async def lifecycle_event(body: LifecycleBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    event_type = body.event_type
    if event_type not in EVENT_ALLOWED_FROM:
        if event_type == "slaughter":
            raise ApiError(E_VALIDATION, "出栏请走「出栏登记」流程（含休药期校验）", {"field": "event_type"})
        raise ApiError(E_VALIDATION, "无效的事件类型", {"field": "event_type"})
    event_date = _parse_date(body.event_date) if body.event_date else _today()  # 缺省回落今天（mock 同款）
    if event_date > _today():
        raise ApiError(E_VALIDATION, "事件日期不能晚于今天", {"field": "event_date"})
    detail = body.detail or None

    result: dict[str, Any] = {}
    female: BreedingFemale | None = None
    pen: Pen
    farm: Farm
    if event_type in INDIVIDUAL_EVENTS:
        if not body.female_id:
            raise ApiError(E_VALIDATION, "请选择母畜", {"field": "female_id"})
        female, pen, farm = await resolve_female(db, user, body.female_id)
        allowed = EVENT_ALLOWED_FROM.get(event_type)
        if allowed and female.breed_status not in allowed:
            raise ApiError(E_INVALID_TRANSITION, f"当前状态为「{BREED_STATUS_LABEL[female.breed_status]}」，不允许登记「{EVENT_LABEL[event_type]}」")
    else:
        if not body.pen_id:
            raise ApiError(E_VALIDATION, "请选择圈舍", {"field": "pen_id"})
        pen, farm = await resolve_pen(db, user, body.pen_id)

    remark = body.remark.strip()
    now_iso = datetime.now(timezone.utc).isoformat()

    if event_type == "breeding":
        female.breed_status = "bred"
        female.breed_date = event_date
        female.due_date = _add_days(event_date, lifecycle_params(pen.species_code)["gestation_days"])
        result = {"female_status": "bred", "due_date": female.due_date.isoformat()}
    elif event_type == "pregnancy_check":
        verdict = str((detail or {}).get("result", "")).strip()
        if not verdict:
            raise ApiError(E_VALIDATION, "请填写妊娠检查结果", {"field": "detail.result"})
        if verdict == "阳性":
            female.breed_status = "pregnant"
            result = {"female_status": "pregnant"}
        else:
            # 「待复查」不得写成已妊娠：配种时已按妊娠期算好预产期并挂了分娩/免疫提醒，
            # 若此处无条件置 pregnant，提醒与 F07 出栏口径就建立在未确认的检查上（阴性走「流产」回退空怀）。
            result = {"female_status": female.breed_status, "recheck": True}
    elif event_type == "birth":
        await handle_birth(db, female, pen, farm, event_date, detail)  # type: ignore[arg-type]
        result = {"female_status": "nursing"}
    elif event_type == "weaning":
        female.breed_status = "open"
        female.parity += 1  # 口径 457：weaning +1，abortion 不变
        await _close_reminders(db, female.female_id, ["weaning"])
        await _close_weaning_for_pen(db, pen.pen_id)
        result = {"female_status": "open", "parity": female.parity}
    elif event_type == "abortion":
        female.breed_status = "open"
        await _close_reminders(db, female.female_id, ["due_date"])
        result = {"female_status": "open"}
    elif event_type == "cull":
        await write_stock_change(db, pen, "death", 0, -1, f"{female.tag_no} 淘汰")
        # F14：淘汰个体同步离栏——优先耳标精确匹配，否则同性别 FIFO 兜底
        act = (
            await db.execute(
                select(AnimalIndividual).where(
                    AnimalIndividual.pen_id == pen.pen_id, AnimalIndividual.status == "active", AnimalIndividual.is_deleted == False  # noqa: E712
                )
            )
        ).scalars().all()
        culled = next((i for i in act if i.ear_tag_no == female.tag_no), None) or next((i for i in act if i.gender == "female"), None)
        if culled:
            exit_individuals(db, [culled], "removed", f"淘汰（母畜档案 {female.tag_no}）")
        female.breed_status = "culled"
        await _close_reminders(db, female.female_id, ["due_date", "weaning"])
        result = {"female_status": "culled"}
    elif event_type in ("castration", "dehorning"):
        if not detail:
            raise ApiError(E_VALIDATION, "请填写操作详情（日期与方式）", {"field": "detail"})
    elif event_type == "stage_change":
        to_stage = str((detail or {}).get("to_stage", ""))
        if to_stage not in BATCH_STAGES:
            raise ApiError(E_VALIDATION, "无效的目标阶段", {"field": "detail.to_stage"})
        pen.batch_stage = to_stage

    # 落 lifecycle_event + lifecycle 台账
    ev = LifecycleEvent(female_id=female.female_id if female else None, pen_id=pen.pen_id,
                        event_type=event_type, event_date=event_date, payload=detail or {})
    db.add(ev)
    await db.flush()
    ext: dict[str, Any] = {"event_type": event_type, "event_date": event_date.isoformat()}
    if female:
        ext["tag_no"] = female.tag_no
    if event_type == "birth":
        ext["live_count"] = int((detail or {}).get("live_male", 0) or 0) + int((detail or {}).get("live_female", 0) or 0)
        ext["dead_count"] = int((detail or {}).get("dead_count", 0) or 0)
    if event_type == "weaning":
        ext["parity"] = female.parity  # type: ignore[union-attr]
    db.add(LedgerRecord(
        farm_id=farm.farm_id, pen_id=pen.pen_id, record_type="lifecycle",
        summary=f"{pen.pen_name} {(female.tag_no + ' ') if female else ''}{EVENT_LABEL[event_type]}{(' · ' + remark) if remark else ''}",
        stock_change_id=0, alert_id=0, record_ext=ext,
    ))
    await db.commit()
    return ok({"event_id": ev.event_id, **result})


# ---------- FR-03/04 提醒 ----------

@router.get("/lifecycle/reminders")
async def list_reminders(farm_id: int = Query(...), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    farm = await db.get(Farm, farm_id)
    if farm is None or farm.is_deleted or farm.user_id != user.user_id:
        raise ApiError(E_NOT_FOUND, "该养殖场不存在或已删除")
    await ensure_reminders(db, farm.farm_id)
    await db.commit()
    today = _today()
    pens = {p.pen_id: p.pen_name for p in (await db.execute(select(Pen))).scalars()}
    females = {f.female_id: f.tag_no for f in (await db.execute(select(BreedingFemale))).scalars()}
    rows = (await db.execute(select(Reminder).where(Reminder.farm_id == farm_id))).scalars().all()
    items = [
        {
            "reminder_id": r.reminder_id,
            "reminder_type": r.reminder_type,
            "title": r.title,
            "content": r.content,
            "remind_date": r.remind_date.isoformat(),
            "pen_name": pens.get(r.pen_id) if r.pen_id else None,
            "tag_no": females.get(r.female_id) if r.female_id else None,
            "status": r.status,
            "handled_note": r.handled_note or None,
            "days_left": _signed_days(today, r.remind_date),
        }
        for r in rows
    ]
    items.sort(key=lambda x: (2 if x["status"] != "pending" else (0 if x["reminder_type"] == "due_date" else 1), x["remind_date"]))
    return ok({"items": items})


class ReminderActionBody(BaseModel):
    action: str = Field(default="done")
    note: str = Field(default="")


@router.post("/reminder/{reminder_id}/handle")
async def reminder_action(reminder_id: int, body: ReminderActionBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    reminder = await db.get(Reminder, reminder_id)
    if reminder is None:
        raise ApiError(E_NOT_FOUND, "该提醒不存在")
    farm = await db.get(Farm, reminder.farm_id)
    if farm is None or farm.user_id != user.user_id:
        raise ApiError(E_FORBIDDEN, "无权限访问该提醒")
    if reminder.status == "expired":
        raise ApiError(E_VALIDATION, "提醒已过期，仅可忽略")
    if reminder.status == "done":
        raise ApiError(E_VALIDATION, "该提醒已处理")
    if body.action != "done":
        raise ApiError(E_VALIDATION, "无效的处理动作", {"field": "action"})
    reminder.status = "done"
    reminder.handled_note = body.note.strip()
    await db.commit()
    return ok({"reminder_id": reminder.reminder_id, "status": "done"})


# ---------- FR-05 用药记录 ----------

class MedicineUsageBody(BaseModel):
    pen_id: int
    med_id: int
    usage_date: str = Field(default="")
    dosage: str = Field(default="")
    operator_note: str = Field(default="")


@router.post("/medicine/usage")
async def create_medicine_usage(body: MedicineUsageBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, farm = await resolve_pen(db, user, body.pen_id)
    med = await db.get(MedicineLib, body.med_id)
    if med is None:
        raise ApiError(E_VALIDATION, "兽药库中不存在该药品", {"field": "med_id"})
    if med.is_forbidden:
        raise ApiError(E_VALIDATION, f"「{med.med_name}」为违禁药品，不可登记", {"field": "med_id"})
    usage_date = _parse_date(body.usage_date) if body.usage_date else _today()
    if usage_date > _today():
        raise ApiError(E_VALIDATION, "用药日期不能晚于今天", {"field": "usage_date"})
    withdrawal_until = _add_days(usage_date, med.withdrawal_period)  # 口径 455：服务端计算
    usage = MedicineUsage(pen_id=body.pen_id, med_id=med.med_id, usage_date=usage_date,
                          dosage=body.dosage.strip(), withdrawal_until=withdrawal_until,
                          operator_note=body.operator_note.strip())
    db.add(usage)
    await db.flush()
    db.add(LedgerRecord(
        farm_id=farm.farm_id, pen_id=pen.pen_id, record_type="medicine",
        summary=f"{pen.pen_name} 用药：{med.med_name}（休药期至 {withdrawal_until.isoformat()}）",
        stock_change_id=0, alert_id=0,
        record_ext={"med_name": med.med_name, "usage_date": usage_date.isoformat(),
                    "withdrawal_until": withdrawal_until.isoformat(), "withdrawal_period": med.withdrawal_period},
    ))
    await db.commit()
    return ok({"usage_id": usage.usage_id, "withdrawal_until": withdrawal_until.isoformat()})


@router.get("/medicine/list")
async def list_medicine_lib(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = (await db.execute(select(MedicineLib).where(MedicineLib.is_forbidden == False).order_by(MedicineLib.med_id))).scalars().all()  # noqa: E712
    return ok({"items": [
        {"med_id": m.med_id, "med_name": m.med_name, "withdrawal_period": m.withdrawal_period,
         "disease_name": m.disease_name, "species_code": m.species_code}
        for m in rows
    ]})


@router.get("/medicine/usage")
async def list_medicine_usages(pen_id: int = Query(...), days: int = Query(default=90, ge=1, le=365), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    await resolve_pen(db, user, pen_id)
    today = _today()
    from_date = _add_days(today, -days)
    rows = (
        await db.execute(
            select(MedicineUsage, MedicineLib)
            .join(MedicineLib, MedicineLib.med_id == MedicineUsage.med_id, isouter=True)
            .where(MedicineUsage.pen_id == pen_id, MedicineUsage.usage_date >= from_date)
            .order_by(MedicineUsage.usage_date.desc(), MedicineUsage.usage_id.desc())
        )
    ).all()
    items = [
        {
            "usage_id": u.usage_id,
            "med_id": u.med_id,
            "med_name": m.med_name if m else "未知药品",
            "usage_date": u.usage_date.isoformat(),
            "dosage": u.dosage,
            "withdrawal_until": u.withdrawal_until.isoformat(),
            "withdrawal_period": m.withdrawal_period if m else None,
            "withdrawal_left_days": max(0, _signed_days(today, u.withdrawal_until)),
            "operator_note": u.operator_note,
        }
        for u, m in rows
    ]
    return ok({"items": items})


# ---------- FR-06 出栏登记（休药期硬校验 + 三表联动 + F14 耳标）----------

def _check_withdrawal(usages: list[MedicineUsage], meds: dict[int, MedicineLib], today: date) -> list[dict[str, Any]]:
    blocking = []
    for u in usages:
        if u.withdrawal_until >= today:
            med = meds.get(u.med_id)
            blocking.append({
                "med_name": med.med_name if med else "未知药品",
                "usage_date": u.usage_date.isoformat(),
                "withdrawal_until": u.withdrawal_until.isoformat(),
                "left_days": _signed_days(today, u.withdrawal_until),
            })
    blocking.sort(key=lambda x: -x["left_days"])
    return blocking


class SlaughterBody(BaseModel):
    # C-05 口径定稿：这两个字段是**本次出栏头数**，不是存栏快照，也不带正负方向。
    # 与 /pen/{id}/stock-change 的 `male_delta/female_delta` 故意不同名：增量字段可正可负、
    # 方向由符号给出；出栏接口的方向由接口本身确定（恒为出库），所以用无方向的 *_count。
    # 负数与全零都在 handler 里按 {code} 信封拒（不加 pydantic ge=0——本服务没有
    # RequestValidationError 处理器，那会绕过 {code,message,data} 契约返回裸 422）。
    male_count: int = Field(default=0, description="本次出栏公畜头数（非存栏数、非增量）")
    female_count: int = Field(default=0, description="本次出栏母畜头数（非存栏数、非增量）")
    avg_weight_kg: float | None = None
    destination: str = Field(default="")
    force: bool = False
    force_note: str = Field(default="")
    tag_nos: list[str] | None = None


@router.post("/pen/{pen_id}/slaughter")
async def slaughter(pen_id: int, body: SlaughterBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from app.api.farm import active_individuals, parse_tag_input, resolve_exit_individuals

    pen, farm = await resolve_pen(db, user, pen_id)
    male_count, female_count = body.male_count, body.female_count
    if male_count < 0 or female_count < 0:
        raise ApiError(E_VALIDATION, "请填写正确的出栏数量", {"field": "male_count"})
    if male_count == 0 and female_count == 0:
        raise ApiError(E_VALIDATION, "请填写出栏数量", {"field": "male_count"})
    if male_count > pen.male_count or female_count > pen.female_count:
        raise ApiError(E_NEGATIVE_STOCK, f"出栏数量超过当前存栏（公 {pen.male_count} / 母 {pen.female_count}）")

    force_note = body.force_note.strip()
    # 休药期硬校验（决策 2 / C-08）：withdrawal_until ≥ today 即拦截，剩余最多优先展示
    usages = (await db.execute(select(MedicineUsage).where(MedicineUsage.pen_id == pen.pen_id))).scalars().all()
    meds = {m.med_id: m for m in (await db.execute(select(MedicineLib))).scalars().all()}
    blocking = _check_withdrawal(usages, meds, _today())
    if blocking and not body.force:
        top = blocking[0]
        raise ApiError(E_WITHDRAWAL, "休药期未满，禁止出栏", {
            "med_name": top["med_name"], "usage_date": top["usage_date"],
            "withdrawal_until": top["withdrawal_until"], "left_days": top["left_days"], "blocking": blocking,
        })
    if body.force and len(force_note) < 10:
        # 强制出栏是食品安全口径上的"被监管方自批"，当前角色体系里没有可用的审批人
        # （本路由挂 require_farmer，admin/vet 无农场上下文，无法代操作）。
        # 因此不假装有审批流，而是把门槛抬到"必须写清许可方与原因"并原样进台账，
        # 让事后追溯有据可查；审批流缺口登记在 docs/08。
        raise ApiError(
            E_VALIDATION,
            "强制出栏需填写不少于 10 字的承诺说明（写清许可方与原因，将原样计入台账）",
            {"field": "force_note"},
        )

    avg_weight = body.avg_weight_kg
    if avg_weight is not None and (avg_weight <= 0 or avg_weight > 1000):
        raise ApiError(E_VALIDATION, "请填写正确的出栏体重", {"field": "avg_weight_kg"})
    destination = body.destination.strip()

    # F14 强关联耳标：传入则校验分布；未传且整栏 → 自动全部离栏；部分未传 → 40002/42201
    slaughter_tags = parse_tag_input(body.tag_nos, "tag_nos")
    slaughter_list = []
    is_full_exit = male_count == pen.male_count and female_count == pen.female_count
    if slaughter_tags:
        slaughter_list = await resolve_exit_individuals(db, pen, slaughter_tags)
        if len(slaughter_tags) != male_count + female_count or sum(1 for i in slaughter_list if i.gender == "male") != male_count:
            raise ApiError(E_VALIDATION, f"出栏耳标与数量不符（需公 {male_count} / 母 {female_count}）", {"field": "tag_nos"})
    elif is_full_exit:
        act = await active_individuals(db, pen.pen_id)
        if len(act) != male_count + female_count:
            raise ApiError(E_NEGATIVE_STOCK, "该圈在栏耳标数与存栏不一致，请先在「个体耳标」页一键补齐后再出栏")
        slaughter_list = list(act)
    else:
        act = await active_individuals(db, pen.pen_id)
        if not act:
            raise ApiError(E_NEGATIVE_STOCK, "部分出栏需精确到个体：该圈尚无耳标档案，请先在「个体耳标」页一键补齐，或改为整栏出栏")
        raise ApiError(E_VALIDATION, "部分出栏需选择出栏耳标（精确到个体）", {"field": "tag_nos"})

    remark = f"出栏至{destination or '—'}{('（强制出栏：' + force_note + '）') if body.force else ''}"
    change = await write_stock_change(db, pen, "sale", -male_count, -female_count, remark)
    event = LifecycleEvent(female_id=None, pen_id=pen.pen_id, event_type="slaughter", event_date=_today(),
                           payload={"male_count": male_count, "female_count": female_count,
                                    **({"avg_weight_kg": round(avg_weight, 2)} if avg_weight else {}),
                                    **({"destination": destination} if destination else {}),
                                    "withdrawal_check": "forced" if body.force else "passed",
                                    **({"force_note": force_note} if body.force else {})})
    db.add(event)
    await db.flush()
    head_count = male_count + female_count
    db.add(LedgerRecord(
        farm_id=farm.farm_id, pen_id=pen.pen_id, record_type="slaughter",
        summary=f"{pen.pen_name} 出栏 公{male_count}/母{female_count}（合计 {head_count} 头）{(' · 至 ' + destination) if destination else ''}{' · 强制出栏' if body.force else ''}",
        stock_change_id=0, alert_id=0,
        record_ext={"male_count": male_count, "female_count": female_count, "head_count": head_count,
                    **({"avg_weight_kg": round(avg_weight, 2)} if avg_weight else {}),
                    **({"destination": destination} if destination else {}),
                    "withdrawal_check": "forced" if body.force else "passed",
                    **({"force_note": force_note} if body.force else {})},
        lifecycle_event_id=event.event_id,
    ))
    # F14：出栏个体落档（含 exit 事件）
    exit_individuals(db, slaughter_list, "sold", remark)
    if pen.male_count + pen.female_count == 0:
        pen.batch_stage = "sold"
    await db.commit()
    return ok({"change_id": change.change_id, "withdrawal_check": "forced" if body.force else "passed", "record_id": None})
