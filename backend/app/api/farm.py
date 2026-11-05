"""F02 场舍存栏（03-API 4.1~4.4）+ F14 存栏流水强关联耳标。

口径与 mock 层逐条对齐：
- 快照纪律：pens.male/female_count 只由 stock_change 流水驱动；
- 强关联：入栏类必带 male_tags/female_tags（数量=增量、场内未占用）；
  离栏类必带 exit_tag_nos（本圈在栏、公母分布一致）；转群必带 tag_nos 并迁移个体；
- 建圈初始存栏自动生成耳标（P{penId}-M/F001 风格，来源=初始建档）；
- 校验全部前置，任一失败不写流水不建档（async session 回滚等效事务）。
"""

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.response import (
    E_FORBIDDEN,
    E_NEGATIVE_STOCK,
    E_NOT_FOUND,
    E_VALIDATION,
    ApiError,
    ok,
)
from app.core.security import get_current_user, require_farmer
from app.models import AnimalIndividual, Farm, IndividualEvent, LedgerRecord, Pen, StockChange, User

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])

CHANGE_TYPE_LABEL = {
    "birth": "产仔",
    "death": "死淘",
    "sale": "出栏",
    "purchase": "购入",
    "manual": "手动调整",
    "transfer_out": "转群转出",
    "transfer_in": "转群转入",
}
BATCH_STAGES = {"nursing", "weaning", "growing", "fattening", "gestation", "lactation"}
VARIETY_NAMES = {
    "pig_fattening": "育肥猪", "pig_sow": "母猪", "pig_piglet": "仔猪",
    "cattle_beef": "肉牛", "cattle_dairy": "奶牛",
    "cattle_simmental": "西门塔尔牛", "cattle_angus": "安格斯牛",
    "cattle_charolais": "夏洛莱牛", "cattle_luxi_yellow": "鲁西黄牛", "cattle_qinchuan": "秦川牛",
    "sheep_hu": "湖羊", "sheep_small_tailed_han": "小尾寒羊",
    "sheep_dorper": "杜泊羊", "sheep_suffolk": "萨福克羊",
    "sheep_tan": "滩羊", "sheep_wool": "绵羊（本地土种）", "sheep_boer": "波尔山羊", "common": "通用",
}
INDIVIDUAL_STATUS_LABEL = {"active": "在栏", "sold": "已出栏", "dead": "已死淘", "removed": "已调减"}


# ---------- 归属解析 ----------

async def resolve_farm(db: AsyncSession, user: User, farm_id: int) -> Farm:
    farm = await db.get(Farm, farm_id)
    if farm is None or farm.is_deleted:
        raise ApiError(E_NOT_FOUND, "该养殖场不存在或已删除")
    if farm.user_id != user.user_id:
        raise ApiError(E_FORBIDDEN, "无权限访问该养殖场")
    return farm


async def resolve_pen(db: AsyncSession, user: User, pen_id: int) -> tuple[Pen, Farm]:
    pen = await db.get(Pen, pen_id)
    if pen is None or pen.is_deleted:
        raise ApiError(E_NOT_FOUND, "该圈舍不存在或已删除")
    farm = await resolve_farm(db, user, pen.farm_id)
    return pen, farm


# ---------- 通用小件 ----------

def clamp_count(v: Any) -> int:
    n = v if isinstance(v, int) and not isinstance(v, bool) else None
    if n is None or n < 0:
        raise ApiError(E_VALIDATION, "请输入正确的数量", {"field": "count"})
    return n


def parse_tag_input(v: Any, field: str) -> list[str]:
    """耳标数组解析：去空白、请求内去重、1~32 位不含空格（03-API 16.x）。"""
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ApiError(E_VALIDATION, "耳标格式不正确", {"field": field})
    out: list[str] = []
    for raw in v:
        tag = raw.strip()
        if not tag:
            continue
        if len(tag) > 32 or any(ch.isspace() for ch in tag):
            raise ApiError(E_VALIDATION, f"耳标号 {tag or '（空）'} 格式不正确（1~32 位且不含空格）", {"field": field})
        if tag in out:
            raise ApiError(E_VALIDATION, f"耳标号 {tag} 重复填写", {"field": field})
        out.append(tag)
    return out


async def assert_ear_tag_free(db: AsyncSession, farm_id: int, tag: str, exclude_id: int = 0) -> None:
    from sqlalchemy import func as fn

    q = select(fn.count()).select_from(AnimalIndividual).where(
        AnimalIndividual.is_deleted == False,  # noqa: E712
        AnimalIndividual.farm_id == farm_id,
        AnimalIndividual.ear_tag_no == tag,
    )
    if exclude_id:
        q = q.where(AnimalIndividual.individual_id != exclude_id)
    if (await db.execute(q)).scalar_one() > 0:
        raise ApiError(E_VALIDATION, f"耳标号 {tag} 已存在（场内唯一）", {"field": "ear_tag_no"})


async def active_individuals(db: AsyncSession, pen_id: int) -> list[AnimalIndividual]:
    rows = await db.execute(
        select(AnimalIndividual).where(
            AnimalIndividual.pen_id == pen_id,
            AnimalIndividual.status == "active",
            AnimalIndividual.is_deleted == False,  # noqa: E712
        )
    )
    return list(rows.scalars())


async def gender_headroom(db: AsyncSession, pen: Pen) -> dict[str, int]:
    act = await active_individuals(db, pen.pen_id)
    return {
        "male": pen.male_count - sum(1 for i in act if i.gender == "male"),
        "female": pen.female_count - sum(1 for i in act if i.gender == "female"),
    }


def auto_tag_prefix(pen: Pen) -> str:
    return f"P{pen.pen_id}"


async def next_auto_tags(db: AsyncSession, pen: Pen, gender: str, count: int) -> list[str]:
    """自动编号：同前缀同性别序号顺延（扫全场避让）。"""
    from sqlalchemy import func as fn

    letter = "M" if gender == "male" else "F"
    prefix = f"{auto_tag_prefix(pen)}-{letter}"
    rows = await db.execute(
        select(AnimalIndividual.ear_tag_no).where(
            AnimalIndividual.farm_id == pen.farm_id,
            AnimalIndividual.is_deleted == False,  # noqa: E712
            AnimalIndividual.ear_tag_no.like(f"{prefix}%"),
        )
    )
    seq = 0
    stem_len = len(prefix)
    for (tag,) in rows.all():
        suffix = tag[stem_len:]
        if suffix.isdigit():
            seq = max(seq, int(suffix))
    return [f"{prefix}{str(seq + k).zfill(3)}" for k in range(1, count + 1)]


def create_individual(pen: Pen, ear_tag_no: str, gender: str, source: str, birth_date=None, remark: str = "") -> AnimalIndividual:
    """纯构造（不落库）；落库请用 add_individual（自动带 F17 建档事件）。"""
    return AnimalIndividual(
        farm_id=pen.farm_id,
        pen_id=pen.pen_id,
        ear_tag_no=ear_tag_no,
        gender=gender,
        variety_code=pen.variety_code,
        birth_date=birth_date,
        source=source,
        status="active",
        exit_note="",
        exit_time=None,
        remark=remark,
        traits="",
        lineage="",
        is_deleted=False,
    )


async def add_individual(db: AsyncSession, pen: Pen, ear_tag_no: str, gender: str, source: str, birth_date=None, remark: str = "") -> AnimalIndividual:
    """建档 + F17 register 事件（时间线起点），一次 flush 取 individual_id。"""
    ind = create_individual(pen, ear_tag_no, gender, source, birth_date, remark)
    db.add(ind)
    await db.flush()
    db.add(IndividualEvent(individual_id=ind.individual_id, event_type="register", event_date=date.today(), payload={"source": source}))
    return ind


async def resolve_exit_individuals(db: AsyncSession, pen: Pen, tags: list[str]) -> list[AnimalIndividual]:
    """校验一批离栏耳标：场内存在、在栏、位于本圈。"""
    out: list[AnimalIndividual] = []
    for tag in tags:
        row = await db.execute(
            select(AnimalIndividual).where(
                AnimalIndividual.farm_id == pen.farm_id,
                AnimalIndividual.ear_tag_no == tag,
                AnimalIndividual.is_deleted == False,  # noqa: E712
            )
        )
        ind = row.scalar_one_or_none()
        if ind is None:
            raise ApiError(E_VALIDATION, f"耳标 {tag} 不存在（请核对本场耳标号）", {"field": "exit_tag_nos"})
        if ind.status != "active":
            raise ApiError(E_VALIDATION, f"耳标 {tag} 已离栏（{INDIVIDUAL_STATUS_LABEL[ind.status]}）", {"field": "exit_tag_nos"})
        if ind.pen_id != pen.pen_id:
            cur = await db.get(Pen, ind.pen_id)
            raise ApiError(E_VALIDATION, f"耳标 {tag} 当前在「{cur.pen_name if cur else ind.pen_id}」圈，不在本圈", {"field": "exit_tag_nos"})
        out.append(ind)
    return out


def exit_individuals(db: AsyncSession, individuals: list[AnimalIndividual], status: str, note: str) -> None:
    """离栏落档（状态 + 离栏说明 + F17 exit 事件；流水由调用方的 write_stock_change 负责）。"""
    now = datetime.now(timezone.utc)
    for ind in individuals:
        ind.status = status
        ind.exit_note = note
        ind.exit_time = now
        db.add(IndividualEvent(individual_id=ind.individual_id, event_type="exit", event_date=date.today(), payload={"status": status, "note": note}))


async def auto_fill_pen_individuals(db: AsyncSession, pen: Pen, source: str = "一键补齐") -> dict[str, int]:
    """按性别头寸补齐个体；headroom ≤ 0 的性别跳过。"""
    head = await gender_headroom(db, pen)
    created = {"male": 0, "female": 0}
    for gender in ("male", "female"):
        n = head[gender]
        if n <= 0:
            continue
        for tag in await next_auto_tags(db, pen, gender, n):
            await assert_ear_tag_free(db, pen.farm_id, tag)
            await add_individual(db, pen, tag, gender, source)
            created[gender] += 1
    return created


def write_ledger(db: AsyncSession, farm_id: int, pen: Pen, change: StockChange) -> None:
    def sign(d: int) -> str:
        return f"+{d}" if d > 0 else str(d)

    summary = (
        f"{pen.pen_name} {CHANGE_TYPE_LABEL[change.change_type]} "
        f"公{sign(change.male_delta)}/母{sign(change.female_delta)}"
        + (f" · {change.remark}" if change.remark else "")
    )
    db.add(
        LedgerRecord(
            farm_id=farm_id,
            pen_id=pen.pen_id,
            record_type="stock_change",
            summary=summary,
            stock_change_id=change.change_id or 0,
            alert_id=0,
            record_ext={"male_delta": change.male_delta, "female_delta": change.female_delta},
        )
    )


async def write_stock_change(db: AsyncSession, pen: Pen, change_type: str, male_delta: int, female_delta: int, remark: str, related: int | None = None) -> StockChange:
    """存栏事务核心：加行锁 → 写流水 → 更新快照 → 落台账（负数拦截在先）。

    行锁不是可选项：并发或重复提交（双击出栏、两个标签页）时，两份请求若都基于
    请求开始时的 ORM 快照算 new_count，会互相覆盖——各读到 10 头、各减 10，
    结果账上 0 头而实际出栏 20 头。FOR UPDATE 把同一圈的流水串行化，
    populate_existing 保证拿到的是锁内最新值而不是 identity map 里的旧快照。
    """
    pen = (
        await db.execute(
            select(Pen)
            .where(Pen.pen_id == pen.pen_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    new_male = pen.male_count + male_delta
    new_female = pen.female_count + female_delta
    if new_male < 0 or new_female < 0:
        raise ApiError(E_NEGATIVE_STOCK, "存栏数量不足，无法完成该操作")
    change = StockChange(
        pen_id=pen.pen_id, change_type=change_type, male_delta=male_delta, female_delta=female_delta,
        related_change_id=related, remark=remark,
    )
    db.add(change)
    await db.flush()  # 取 change_id
    pen.male_count = new_male
    pen.female_count = new_female
    write_ledger(db, pen.farm_id, pen, change)
    return change


# ---------- 4.1 养殖场 ----------

class FarmBody(BaseModel):
    farm_name: str = Field(default="")
    province: str = Field(default="")
    city: str = Field(default="")
    county: str = Field(default="")
    adcode: str = Field(default="")
    address: str = Field(default="")
    contact_person: str = Field(default="")
    phone: str = Field(default="")


@router.post("/farm")
async def create_farm(body: FarmBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if not body.farm_name.strip():
        raise ApiError(E_VALIDATION, "请填写养殖场名称", {"field": "farm_name"})
    farm = Farm(user_id=user.user_id, **body.model_dump())
    db.add(farm)
    await db.commit()
    return ok({"farm_id": farm.farm_id, "guide_done": True})


@router.get("/farm/list")
async def list_farms(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = await db.execute(
        select(Farm).where(Farm.user_id == user.user_id, Farm.is_deleted == False).order_by(Farm.farm_id)  # noqa: E712
    )
    farms = rows.scalars().all()
    pens = (
        await db.execute(select(Pen).where(Pen.is_deleted == False))  # noqa: E712
    ).scalars().all()
    items = []
    for f in farms:
        my_pens = [p for p in pens if p.farm_id == f.farm_id]
        items.append({
            "farm_id": f.farm_id,
            "farm_name": f.farm_name,
            "location": " ".join(x for x in (f.province, f.city, f.county) if x),
            "address": f.address,
            "species_code": user.main_species_code or "pig",
            "pen_count": len(my_pens),
            "total_male": sum(p.male_count for p in my_pens),
            "total_female": sum(p.female_count for p in my_pens),
            "create_time": f.create_time.isoformat(),
        })
    return ok({"items": items})


@router.put("/farm/{farm_id}")
async def update_farm(farm_id: int, body: FarmBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    farm = await resolve_farm(db, user, farm_id)
    for key, value in body.model_dump().items():
        if key == "farm_name" and not value.strip():
            raise ApiError(E_VALIDATION, "请填写养殖场名称", {"field": "farm_name"})
        setattr(farm, key, value)
    await db.commit()
    return ok({"farm_id": farm.farm_id, "updated": True})


@router.delete("/farm/{farm_id}")
async def delete_farm(farm_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    farm = await resolve_farm(db, user, farm_id)
    farm.is_deleted = True  # 逻辑删除：圈舍/流水/台账保留
    await db.commit()
    from sqlalchemy import func as fn

    remain = (
        await db.execute(select(fn.count()).select_from(Farm).where(Farm.user_id == user.user_id, Farm.is_deleted == False))  # noqa: E712
    ).scalar_one()
    return ok({"farm_id": farm.farm_id, "deleted": True, "guide_done": remain > 0})


# ---------- 4.2 圈舍 ----------

class PenBody(BaseModel):
    farm_id: int
    pen_name: str = Field(default="")
    species_code: str = Field(default="")
    variety_code: str | None = None
    male_count: int = 0
    female_count: int = 0
    batch_stage: str | None = None


def species_of_variety(variety_code: str | None) -> str:
    """品种前缀能确定物种时返回该物种，否则返回空串交由调用方兜底。

    2026-10-15 修复：建圈未传 species_code 时按品种前缀推导（此前默认 'pig'，导致羊圈
    species 落成 pig → AI 问诊按猪知识池检索、答非所问）。
    本次收口：'common'（通用）等推不出物种的品种不再静默回落 pig —— 前端 PenPayload
    不带 species_code、且品种默认「通用」，回落 pig 会让牛羊账号的新圈舍与编辑存量圈舍
    全部错位（阈值/营养标准/问诊知识池三层同时偏）。兜底顺序见 create_pen。
    """
    v = variety_code or ""
    for prefix in ("sheep", "cattle", "pig"):
        if v.startswith(prefix):
            return prefix
    return ""


@router.post("/pen")
async def create_pen(body: PenBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    await resolve_farm(db, user, body.farm_id)
    male = clamp_count(body.male_count)
    female = clamp_count(body.female_count)
    stage = body.batch_stage
    if stage not in (None, "") and stage not in BATCH_STAGES:
        raise ApiError(E_VALIDATION, "无效的生长阶段", {"field": "batch_stage"})
    pen = Pen(
        farm_id=body.farm_id,
        pen_name=body.pen_name.strip() or f"{body.farm_id}号圈舍",
        species_code=body.species_code or species_of_variety(body.variety_code) or user.main_species_code or "pig",
        variety_code=body.variety_code,
        # 快照纪律：数量恒 0 起步，由下方 purchase 流水驱动（02-数据库口径表，禁止直接赋值）
        male_count=0,
        female_count=0,
        batch_stage=stage or None,
    )
    db.add(pen)
    await db.flush()
    change_id: int | None = None
    if male + female > 0:
        change = await write_stock_change(db, pen, "purchase", male, female, "建圈初始存栏")
        change_id = change.change_id
        # F14 强关联：初始存栏同步自动生成耳标档案
        await auto_fill_pen_individuals(db, pen, source="初始建档")
    await db.commit()
    return ok({"pen_id": pen.pen_id, "change_id": change_id})


@router.get("/pen/list")
async def list_pens(farm_id: int = Query(...), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    await resolve_farm(db, user, farm_id)
    rows = await db.execute(
        select(Pen).where(Pen.farm_id == farm_id, Pen.is_deleted == False).order_by(Pen.pen_id)  # noqa: E712
    )
    items = [
        {
            "pen_id": p.pen_id,
            "pen_name": p.pen_name,
            "species_code": p.species_code,
            "variety_code": p.variety_code,
            "variety_name": (VARIETY_NAMES.get(p.variety_code or "", "通用") if p.variety_code != "common" else "通用"),
            "male_count": p.male_count,
            "female_count": p.female_count,
            "batch_stage": p.batch_stage,
            "create_time": p.create_time.isoformat(),
        }
        for p in rows.scalars()
    ]
    return ok({"items": items})


class PenUpdateBody(BaseModel):
    pen_name: str | None = None
    variety_code: str | None = None
    batch_stage: str | None = None


@router.put("/pen/{pen_id}")
async def update_pen(pen_id: int, body: PenUpdateBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, pen_id)
    if body.pen_name is not None:
        if not body.pen_name.strip():
            raise ApiError(E_VALIDATION, "请填写圈舍名称", {"field": "pen_name"})
        pen.pen_name = body.pen_name.strip()
    if body.variety_code is not None:
        pen.variety_code = body.variety_code or None
        # 换品种只在能确定物种时改写物种：'common'（通用）推不出物种，
        # 若无条件赋值会把已有的牛羊圈静默改回 pig（与建圈同源的缺陷）。
        derived = species_of_variety(pen.variety_code)
        if derived:
            pen.species_code = derived
    if body.batch_stage is not None:
        if body.batch_stage not in ("",) and body.batch_stage not in BATCH_STAGES:
            raise ApiError(E_VALIDATION, "无效的生长阶段", {"field": "batch_stage"})
        pen.batch_stage = body.batch_stage or None
    await db.commit()
    return ok({"pen_id": pen.pen_id, "updated": True})


@router.delete("/pen/{pen_id}")
async def delete_pen(pen_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, pen_id)
    pen.is_deleted = True  # 流水与台账保留（档案性质）
    await db.commit()
    return ok({"pen_id": pen.pen_id, "deleted": True})


# ---------- 4.3 存栏变动（五类，F14 强关联耳标）----------

class StockChangeBody(BaseModel):
    change_type: str = Field(default="")
    male_delta: int = 0
    female_delta: int = 0
    remark: str = Field(default="")
    male_tags: list[str] | None = None
    female_tags: list[str] | None = None
    exit_tag_nos: list[str] | None = None


@router.post("/pen/{pen_id}/stock-change")
async def stock_change(pen_id: int, body: StockChangeBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, pen_id)
    change_type = body.change_type
    if change_type not in CHANGE_TYPE_LABEL or change_type in ("transfer_out", "transfer_in"):
        raise ApiError(E_VALIDATION, "无效的变动类型", {"field": "change_type"})

    md, fd = body.male_delta, body.female_delta
    if not (isinstance(md, int) and isinstance(fd, int)):
        raise ApiError(E_VALIDATION, "请输入正确的数量", {"field": "male_delta"})
    remark = body.remark.strip()
    if change_type == "manual":
        if md == 0 and fd == 0:
            raise ApiError(E_VALIDATION, "请填写变动数量", {"field": "male_delta"})
        if not remark:
            raise ApiError(E_VALIDATION, "请填写调整原因", {"field": "remark"})
        if (md > 0 and fd < 0) or (md < 0 and fd > 0):
            raise ApiError(E_VALIDATION, "手动调整公母方向需一致", {"field": "male_delta"})
    else:
        if md < 0 or fd < 0:
            raise ApiError(E_VALIDATION, "请输入正确的数量", {"field": "male_delta"})
        if md == 0 and fd == 0:
            raise ApiError(E_VALIDATION, "请填写变动数量", {"field": "male_delta"})

    sign = -1 if change_type in ("death", "sale") else 1
    is_increase = change_type in ("birth", "purchase") or (change_type == "manual" and md + fd > 0)

    # ---- F14 强关联校验（全部前置）----
    male_tags: list[str] = []
    female_tags: list[str] = []
    exit_list: list[AnimalIndividual] = []
    if is_increase:
        male_tags = parse_tag_input(body.male_tags, "male_tags")
        female_tags = parse_tag_input(body.female_tags, "female_tags")
        if len(male_tags) != max(md, 0) or len(female_tags) != max(fd, 0):
            raise ApiError(E_VALIDATION, f"请填写与数量一致的耳标（公 {max(md, 0)} 个、母 {max(fd, 0)} 个）", {"field": "male_tags"})
        for tag in (*male_tags, *female_tags):
            await assert_ear_tag_free(db, pen.farm_id, tag)
    else:
        exit_tags = parse_tag_input(body.exit_tag_nos, "exit_tag_nos")
        if len(exit_tags) != abs(md) + abs(fd):
            raise ApiError(E_VALIDATION, f"请选择与本次数量的离栏耳标（共 {abs(md) + abs(fd)} 个）", {"field": "exit_tag_nos"})
        exit_list = await resolve_exit_individuals(db, pen, exit_tags)
        if sum(1 for i in exit_list if i.gender == "male") != abs(md):
            raise ApiError(E_VALIDATION, f"离栏耳标公母分布与数量不符（需公 {abs(md)} / 母 {abs(fd)}）", {"field": "exit_tag_nos"})

    change = await write_stock_change(db, pen, change_type, md * sign, fd * sign, remark)

    # ---- F14 个体联动 ----
    if is_increase:
        source_label = {"birth": "产仔入栏", "purchase": "购入入栏"}.get(change_type, "手动调增")
        for tag in male_tags:
            await add_individual(db, pen, tag, "male", source_label)
        for tag in female_tags:
            await add_individual(db, pen, tag, "female", source_label)
    else:
        status = {"sale": "sold", "death": "dead"}.get(change_type, "removed")
        exit_individuals(db, exit_list, status, f"{CHANGE_TYPE_LABEL[change_type]}{(' · ' + remark) if remark else ''}")

    await db.commit()
    return ok({"change_id": change.change_id, "male_count": pen.male_count, "female_count": pen.female_count})


# ---------- 4.4 转群（同场同物种，两笔互指）----------

class TransferBody(BaseModel):
    target_pen_id: int
    male_count: int = 0
    female_count: int = 0
    remark: str = Field(default="")
    tag_nos: list[str] | None = None


@router.post("/pen/{pen_id}/transfer")
async def transfer(pen_id: int, body: TransferBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    source, farm = await resolve_pen(db, user, pen_id)
    target = await db.get(Pen, body.target_pen_id)
    if target is None or target.is_deleted:
        raise ApiError(E_NOT_FOUND, "该圈舍不存在或已删除")
    if target.farm_id != farm.farm_id or target.species_code != source.species_code:
        raise ApiError(E_VALIDATION, "目标圈舍不符合转群条件（同场、同物种）", {"field": "target_pen_id"})

    male = clamp_count(body.male_count)
    female = clamp_count(body.female_count)
    if male == 0 and female == 0:
        raise ApiError(E_VALIDATION, "请填写转群数量", {"field": "male_count"})
    if source.male_count < male or source.female_count < female:
        raise ApiError(E_NEGATIVE_STOCK, "转出数量超过源圈存栏")

    # F14 强关联：必须指定个体（源圈在栏、分布匹配）
    tags = parse_tag_input(body.tag_nos, "tag_nos")
    if len(tags) != male + female:
        raise ApiError(E_VALIDATION, f"请选择与转群数量一致的耳标（公 {male} / 母 {female}，共 {male + female} 个）", {"field": "tag_nos"})
    moving = await resolve_exit_individuals(db, source, tags)
    if sum(1 for i in moving if i.gender == "male") != male:
        raise ApiError(E_VALIDATION, f"转群耳标公母分布与数量不符（需公 {male} / 母 {female}）", {"field": "tag_nos"})

    remark = body.remark.strip()
    out_change = await write_stock_change(db, source, "transfer_out", -male, -female, remark)
    in_change = await write_stock_change(db, target, "transfer_in", male, female, remark, related=out_change.change_id)
    out_change.related_change_id = in_change.change_id
    for ind in moving:
        ind.pen_id = target.pen_id  # 个体迁移（状态不变）
        # F17：转群事件留痕
        db.add(IndividualEvent(
            individual_id=ind.individual_id, event_type="transfer", event_date=date.today(),
            payload={"from_pen": source.pen_name, "to_pen": target.pen_name},
        ))
    await db.commit()
    return ok({
        "out_change_id": out_change.change_id,
        "in_change_id": in_change.change_id,
        "source_pen": {"male_count": source.male_count, "female_count": source.female_count},
        "target_pen": {"male_count": target.male_count, "female_count": target.female_count},
    })
