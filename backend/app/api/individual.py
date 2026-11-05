"""F14 个体耳标档案（03-API 十六节 16.1~16.5）。

强关联对账：在栏个体数 == 圈舍存栏快照；差额仅来自历史未建档数据 → 一键补齐收敛。
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
    INDIVIDUAL_STATUS_LABEL,
    VARIETY_NAMES,
    active_individuals,
    assert_ear_tag_free,
    auto_fill_pen_individuals,
    create_individual,
    gender_headroom,
    resolve_pen,
)
from app.core.db import get_db
from app.core.response import E_FORBIDDEN, E_NOT_FOUND, E_VALIDATION, ApiError, ok
from app.core.security import get_current_user, require_farmer
from app.models import AnimalIndividual, Farm, IndividualEvent, LedgerRecord, Pen, User

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])

STATUS_SET = set(INDIVIDUAL_STATUS_LABEL)


async def _own_pen_ids(db: AsyncSession, user: User) -> list[int]:
    rows = await db.execute(
        select(Pen.pen_id)
        .join(Farm, Farm.farm_id == Pen.farm_id)
        .where(Pen.is_deleted == False, Farm.user_id == user.user_id, Farm.is_deleted == False)  # noqa: E712
        .order_by(Pen.pen_id)
    )
    return list(rows.scalars())


async def resolve_own_pen(db: AsyncSession, user: User, pen_id: int) -> tuple[Pen, Any]:
    return await resolve_pen(db, user, pen_id)


# ---------- 16.1 列表与对账 ----------

@router.get("/individual/list")
async def list_individuals(
    pen_id: int | None = Query(default=None),
    status: str = Query(default="active"),
    keyword: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    pen_ids = await _own_pen_ids(db, user)
    if pen_id is not None:
        if pen_id not in pen_ids:
            raise ApiError(E_FORBIDDEN, "无权限访问该圈舍")
        scope = [pen_id]
    else:
        scope = pen_ids

    q = select(AnimalIndividual).where(
        AnimalIndividual.is_deleted == False,  # noqa: E712
        AnimalIndividual.pen_id.in_(scope),
    )
    if status != "all" and status in STATUS_SET:
        q = q.where(AnimalIndividual.status == status)
    if keyword.strip():
        kw = keyword.strip()
        q = q.where(
            AnimalIndividual.ear_tag_no.contains(kw)
            | AnimalIndividual.remark.contains(kw)
            | AnimalIndividual.source.contains(kw)
        )
    q = q.order_by(AnimalIndividual.create_time.desc(), AnimalIndividual.individual_id.desc())

    total = (await db.execute(select(fn.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    pens_map = {p.pen_id: p for p in (await db.execute(select(Pen).where(Pen.pen_id.in_(scope)))).scalars()}

    items = [
        {
            "individual_id": i.individual_id,
            "ear_tag_no": i.ear_tag_no,
            "pen_id": i.pen_id,
            "pen_name": pens_map[i.pen_id].pen_name if i.pen_id in pens_map else "—",
            "gender": i.gender,
            "gender_label": "公" if i.gender == "male" else "母",
            "variety_code": i.variety_code,
            "variety_name": (VARIETY_NAMES.get(i.variety_code or "", "通用") if i.variety_code and i.variety_code != "common" else ("通用" if i.variety_code == "common" else None)),
            "birth_date": i.birth_date.isoformat() if i.birth_date else None,
            "source": i.source,
            "status": i.status,
            "status_label": INDIVIDUAL_STATUS_LABEL[i.status],
            "exit_note": i.exit_note,
            "exit_time": i.exit_time.isoformat() if i.exit_time else None,
            "remark": i.remark,
            "traits": i.traits,
            "lineage": i.lineage,
            "create_time": i.create_time.isoformat(),
        }
        for i in rows
    ]

    # 对账 summary：与筛选无关，恒为 scope 口径
    pens = []
    for pid in scope:
        p = pens_map.get(pid)
        if p is None:
            continue
        act = await active_individuals(db, pid)
        stock_total = p.male_count + p.female_count
        pens.append({
            "pen_id": pid,
            "pen_name": p.pen_name,
            "stock_total": stock_total,
            "active_total": len(act),
            "diff": stock_total - len(act),
        })
    summary = {
        "stock_total": sum(x["stock_total"] for x in pens),
        "active_total": sum(x["active_total"] for x in pens),
        "diff": sum(x["diff"] for x in pens),
    }
    return ok({"summary": summary, "pens": pens, "total": total, "page": page, "page_size": page_size, "items": items})


# ---------- 16.2 单个建档 ----------

class IndividualBody(BaseModel):
    pen_id: int
    ear_tag_no: str = Field(default="")
    gender: str = Field(default="")
    birth_date: str | None = None
    source: str = Field(default="")
    remark: str = Field(default="")
    traits: str = Field(default="")
    lineage: str = Field(default="")


def _parse_birth_date(raw: str | None) -> date | None:
    if not raw:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ApiError(E_VALIDATION, "出生日期格式不正确", {"field": "birth_date"})
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise ApiError(E_VALIDATION, "出生日期格式不正确", {"field": "birth_date"}) from e


@router.post("/individual")
async def create_individual_ep(body: IndividualBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, _ = await resolve_own_pen(db, user, body.pen_id)
    if body.gender not in ("male", "female"):
        raise ApiError(E_VALIDATION, "请选择性别", {"field": "gender"})
    tag = body.ear_tag_no.strip()
    if not tag or len(tag) > 32 or any(ch.isspace() for ch in tag):
        raise ApiError(E_VALIDATION, f"耳标号 {tag or '（空）'} 格式不正确（1~32 位且不含空格）", {"field": "ear_tag_no"})
    await assert_ear_tag_free(db, pen.farm_id, tag)
    birth = _parse_birth_date(body.birth_date)

    head = await gender_headroom(db, pen)
    if head[body.gender] <= 0:
        gender_text = "公" if body.gender == "male" else "母"
        raise ApiError(
            E_VALIDATION,
            f"「{pen.pen_name}」{gender_text}存栏已与在栏耳标数一致，无补录额度；如为新入栏动物请走「调整存栏」流水",
            {"field": "gender"},
        )
    ind = create_individual(pen, tag, body.gender, body.source.strip() or "人工建档", birth_date=birth, remark=body.remark.strip())
    ind.traits = (body.traits or "").strip()[:128]
    ind.lineage = (body.lineage or "").strip()[:128]
    db.add(ind)
    await db.flush()
    # F17：建档事件（时间线起点）
    db.add(IndividualEvent(individual_id=ind.individual_id, event_type="register", event_date=date.today(), payload={"source": ind.source}))
    await db.commit()
    return ok({"individual_id": ind.individual_id, "ear_tag_no": ind.ear_tag_no})


# ---------- 16.3 编辑 / 纠错删档 ----------

class IndividualUpdateBody(BaseModel):
    ear_tag_no: str | None = None
    birth_date: str | None = None
    source: str | None = None
    remark: str | None = None
    traits: str | None = None
    lineage: str | None = None


async def resolve_individual(db: AsyncSession, user: User, individual_id: int) -> AnimalIndividual:
    ind = await db.get(AnimalIndividual, individual_id)
    if ind is None or ind.is_deleted:
        raise ApiError(E_NOT_FOUND, "该个体档案不存在或已删除")
    await resolve_own_pen(db, user, ind.pen_id)  # 归属校验
    return ind


@router.put("/individual/{individual_id}")
async def update_individual(individual_id: int, body: IndividualUpdateBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ind = await resolve_individual(db, user, individual_id)
    pen = await db.get(Pen, ind.pen_id)
    assert pen is not None
    if body.ear_tag_no is not None:
        tag = body.ear_tag_no.strip()
        if not tag or len(tag) > 32 or any(ch.isspace() for ch in tag):
            raise ApiError(E_VALIDATION, f"耳标号 {tag or '（空）'} 格式不正确（1~32 位且不含空格）", {"field": "ear_tag_no"})
        await assert_ear_tag_free(db, ind.farm_id, tag, exclude_id=ind.individual_id)
        if tag != ind.ear_tag_no:
            # F17：换标留痕（法规要求换新标识须在档案记录；旧号终身占位不复用）
            db.add(IndividualEvent(individual_id=ind.individual_id, event_type="rename", event_date=date.today(),
                                   payload={"old_no": ind.ear_tag_no, "new_no": tag}))
            ind.ear_tag_no = tag
    if body.birth_date is not None:
        ind.birth_date = _parse_birth_date(body.birth_date)
    if body.source is not None:
        ind.source = body.source.strip() or ind.source
    if body.remark is not None:
        ind.remark = body.remark.strip()
    if body.traits is not None:
        ind.traits = body.traits.strip()[:128]
    if body.lineage is not None:
        ind.lineage = body.lineage.strip()[:128]
    await db.commit()
    return ok({"individual_id": ind.individual_id, "updated": True})


@router.delete("/individual/{individual_id}")
async def delete_individual(individual_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ind = await resolve_individual(db, user, individual_id)
    if ind.status != "active":
        raise ApiError(E_VALIDATION, "已离栏个体为历史档案，不可删除", {"field": "status"})
    ind.is_deleted = True
    await db.commit()
    return ok({"individual_id": ind.individual_id, "deleted": True})


# ---------- 16.4 一键补齐 ----------

class AutoFillBody(BaseModel):
    prefix: str | None = None


@router.post("/pen/{pen_id}/individuals/auto-fill")
async def auto_fill(pen_id: int, body: AutoFillBody | None = None, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, _ = await resolve_own_pen(db, user, pen_id)
    head = await gender_headroom(db, pen)
    if head["male"] <= 0 and head["female"] <= 0:
        act = await active_individuals(db, pen.pen_id)
        return ok({
            "created_male": 0,
            "created_female": 0,
            "active_total": len(act),
            "stock_total": pen.male_count + pen.female_count,
            "message": "账实已一致，无需补齐",
        })
    created = await auto_fill_pen_individuals(db, pen)
    # 台账留痕（只建个体档案，存栏不变）
    db.add(
        LedgerRecord(
            farm_id=pen.farm_id,
            pen_id=pen.pen_id,
            record_type="individual",
            summary=f"{pen.pen_name} 耳标补齐 公+{created['male']}/母+{created['female']}（仅建个体档案，存栏不变）",
            stock_change_id=0,
            alert_id=0,
            record_ext={"created_male": created["male"], "created_female": created["female"]},
        )
    )
    await db.commit()
    act = await active_individuals(db, pen.pen_id)
    return ok({
        "created_male": created["male"],
        "created_female": created["female"],
        "active_total": len(act),
        "stock_total": pen.male_count + pen.female_count,
        "message": f"已补齐 公{created['male']}/母{created['female']}",
    })


# ---------- F17 个体档案详情（一畜一档：03-API 十七节 17.1~17.3）----------

INDIVIDUAL_EVENT_LABEL = {
    "register": "建档",
    "rename": "换标",
    "transfer": "转群",
    "exit": "离栏",
    "weight": "体重测定",
    "immunization": "免疫/驱虫",
    "treatment": "发病/诊疗",
    "note": "备注",
}
MANUAL_EVENTS = {"weight", "immunization", "treatment", "note"}


def _event_summary(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "register":
        return f"来源：{payload.get('source', '—')}"
    if event_type == "rename":
        return f"{payload.get('old_no')} → {payload.get('new_no')}"
    if event_type == "transfer":
        return f"{payload.get('from_pen')} → {payload.get('to_pen')}"
    if event_type == "exit":
        return str(payload.get("note", ""))
    if event_type == "weight":
        base = f"{payload.get('weight_kg')} kg"
        return f"{base} · {payload['note']}" if payload.get("note") else base
    if event_type == "immunization":
        parts = [payload.get("vaccine_name", ""), payload.get("dose", "")]
        if payload.get("next_date"):
            parts.append(f"下次免疫 {payload['next_date']}")
        return " · ".join(str(x) for x in parts if x)
    if event_type == "treatment":
        keys = ("symptom", "diagnosis", "medication", "outcome", "vet_name")
        return " · ".join(str(payload[k]) for k in keys if payload.get(k))
    return str(payload.get("content", ""))


@router.get("/individual/{individual_id}/detail")
async def individual_detail(individual_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ind = await resolve_individual(db, user, individual_id)
    pen = await db.get(Pen, ind.pen_id)
    rows = (
        await db.execute(
            select(IndividualEvent)
            .where(IndividualEvent.individual_id == individual_id)
            .order_by(IndividualEvent.event_date.desc(), IndividualEvent.event_id.desc())
            .limit(200)
        )
    ).scalars().all()

    # 体重曲线：weight 事件升序 + 相邻日增重（间隔 ≥1 天）
    weight_rows = (
        await db.execute(
            select(IndividualEvent)
            .where(IndividualEvent.individual_id == individual_id, IndividualEvent.event_type == "weight")
            .order_by(IndividualEvent.event_date.asc(), IndividualEvent.event_id.asc())
        )
    ).scalars().all()
    series: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for ev in weight_rows:
        kg = float(ev.payload.get("weight_kg", 0))
        gain = None
        if prev is not None:
            days = (ev.event_date - prev["date"]).days
            if days >= 1:
                gain = round((kg - prev["weight_kg"]) / days, 2)
        series.append({"date": ev.event_date.isoformat(), "weight_kg": kg, "daily_gain": gain})
        prev = {"date": ev.event_date, "weight_kg": kg}

    def summary_dict(i: AnimalIndividual) -> dict[str, Any]:
        return {
            "individual_id": i.individual_id,
            "ear_tag_no": i.ear_tag_no,
            "pen_id": i.pen_id,
            "pen_name": pen.pen_name if pen else "—",
            "gender": i.gender,
            "gender_label": "公" if i.gender == "male" else "母",
            "variety_code": i.variety_code,
            "variety_name": (VARIETY_NAMES.get(i.variety_code or "", "通用") if i.variety_code and i.variety_code != "common" else ("通用" if i.variety_code == "common" else None)),
            "birth_date": i.birth_date.isoformat() if i.birth_date else None,
            "source": i.source,
            "status": i.status,
            "status_label": INDIVIDUAL_STATUS_LABEL[i.status],
            "exit_note": i.exit_note,
            "exit_time": i.exit_time.isoformat() if i.exit_time else None,
            "remark": i.remark,
            "traits": i.traits,
            "lineage": i.lineage,
            "create_time": i.create_time.isoformat(),
        }

    events = [
        {
            "event_id": ev.event_id,
            "event_type": ev.event_type,
            "event_type_label": INDIVIDUAL_EVENT_LABEL.get(ev.event_type, ev.event_type),
            "event_date": ev.event_date.isoformat(),
            "payload": ev.payload or {},
            "summary": _event_summary(ev.event_type, ev.payload or {}),
            "manual": ev.event_type in MANUAL_EVENTS,
            "create_time": ev.create_time.isoformat(),
        }
        for ev in rows
    ]
    return ok({"individual": summary_dict(ind), "events": events, "weight_series": series})


class IndividualEventBody(BaseModel):
    event_type: str = Field(default="")
    event_date: str = Field(default="")
    weight_kg: float | None = None
    vaccine_name: str | None = None
    dose: str | None = None
    next_date: str | None = None
    symptom: str | None = None
    diagnosis: str | None = None
    medication: str | None = None
    outcome: str | None = None
    vet_name: str | None = None
    content: str | None = None
    note: str | None = None


@router.post("/individual/{individual_id}/event")
async def create_individual_event(individual_id: int, body: IndividualEventBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ind = await resolve_individual(db, user, individual_id)
    if body.event_type not in MANUAL_EVENTS:
        raise ApiError(E_VALIDATION, "无效的事件类型（流水联动事件由系统自动记录）", {"field": "event_type"})
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.event_date or ""):
        raise ApiError(E_VALIDATION, "请填写正确的事件日期", {"field": "event_date"})
    try:
        event_date = date.fromisoformat(body.event_date)
    except ValueError as e:
        raise ApiError(E_VALIDATION, "请填写正确的事件日期", {"field": "event_date"}) from e
    if event_date > date.today():
        raise ApiError(E_VALIDATION, "事件日期不能晚于今天", {"field": "event_date"})
    if ind.birth_date and event_date < ind.birth_date:
        raise ApiError(E_VALIDATION, "事件日期不能早于出生日期", {"field": "event_date"})

    def cut(v: str | None, n: int) -> str:
        return (v or "").strip()[:n]

    payload: dict[str, Any] = {}
    if body.event_type == "weight":
        kg = body.weight_kg
        if kg is None or not (0 < kg <= 500):
            raise ApiError(E_VALIDATION, "体重需在 0~500kg 之间", {"field": "weight_kg"})
        payload["weight_kg"] = round(kg, 2)
        if cut(body.note, 128):
            payload["note"] = cut(body.note, 128)
    elif body.event_type == "immunization":
        name = cut(body.vaccine_name, 64)
        if not name:
            raise ApiError(E_VALIDATION, "请填写疫苗/驱虫药名称", {"field": "vaccine_name"})
        payload["vaccine_name"] = name
        if cut(body.dose, 64):
            payload["dose"] = cut(body.dose, 64)
        if body.next_date:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.next_date):
                raise ApiError(E_VALIDATION, "下次免疫日期格式不正确", {"field": "next_date"})
            payload["next_date"] = body.next_date
        if cut(body.note, 128):
            payload["note"] = cut(body.note, 128)
    elif body.event_type == "treatment":
        symptom = cut(body.symptom, 128)
        if not symptom:
            raise ApiError(E_VALIDATION, "请填写症状描述", {"field": "symptom"})
        payload["symptom"] = symptom
        for key, col in (("diagnosis", 128), ("medication", 128), ("outcome", 64), ("vet_name", 32)):
            val = cut(getattr(body, key), col)
            if val:
                payload[key] = val
    else:
        content = cut(body.content, 128)
        if not content:
            raise ApiError(E_VALIDATION, "请填写备注内容", {"field": "content"})
        payload["content"] = content

    ev = IndividualEvent(individual_id=individual_id, event_type=body.event_type, event_date=event_date, payload=payload)
    db.add(ev)
    await db.commit()
    return ok({"event_id": ev.event_id, "event_type": ev.event_type, "event_date": ev.event_date.isoformat()})


@router.delete("/individual/event/{event_id}")
async def delete_individual_event(event_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ev = await db.get(IndividualEvent, event_id)
    if ev is None:
        raise ApiError(E_NOT_FOUND, "该事件不存在或已删除")
    await resolve_individual(db, user, ev.individual_id)  # 归属校验
    if ev.event_type not in MANUAL_EVENTS:
        raise ApiError(E_VALIDATION, "档案事件（建档/换标/转群/离栏）不可删除", {"field": "event_type"})
    await db.delete(ev)
    await db.commit()
    return ok({"event_id": event_id, "deleted": True})
