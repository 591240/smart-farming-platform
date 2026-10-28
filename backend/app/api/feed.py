"""F06 配方大脑 — 生成 / 替代方案 / 生效 / 原料价（03-API 六节 6.3~6.6）。

引擎在 `services/feed_engine.py`（确定性模板计算，不调 LLM：配方要可复算、可审计）。

一处刻意收紧：mock 的角色门卫正则里漏了 `feed` 前缀（/feed/price 对 vet/admin 也放行），
本实现统一挂 `require_farmer`——原料价是养殖户的经营数据，不属于"全局配置库"。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.farm import resolve_pen
from app.core.db import get_db
from app.core.response import E_NOT_FOUND, E_VALIDATION, ApiError, ok
from app.core.security import get_current_user, require_admin, require_farmer
from app.core.timeutil import today_cst
from app.models import Farm, FeedFormula, FeedNutritionLib, FeedPrice, LedgerRecord, Pen, User
from app.services import feed_engine as fe
from app.services import price_sync as ps
from app.services import weather as wx

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])
# 行情采集会改写**全局**参考价（`feed_prices.user_id IS NULL`），进而改变别人的配方成本，
# 因此它不属于 farmer 路由。单挂一条 admin 路由，而不是在函数里再判一次角色——
# 与 router 级 `require_farmer` 冲突的端点，本就不该长在这个 router 上。
admin_router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_admin)])

CN_INDEX = "一二三四五"


def _today_str() -> str:
    return today_cst().isoformat()


class GenerateBody(BaseModel):
    pen_id: int
    constraints: dict[str, Any] | None = None


def _excluded(constraints: dict[str, Any] | None) -> set[str]:
    raw = (constraints or {}).get("exclude_ingredients")
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if str(x).strip()}


async def _candidate_rows(db: AsyncSession, pen: Pen, exclude: set[str], user_id: int | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """返回 [(std, composed)]，已通过比例与价格校验。"""
    std = await fe.match_nutrition_standard(db, pen)
    if std is None:
        raise ApiError(E_VALIDATION, "该圈舍品种/阶段暂无营养标准模板，无法生成配方")
    prices = await fe.latest_prices(db, user_id)
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for label, items, note in fe.build_candidates(std, exclude, prices):
        if not fe.ratio_sum_ok(items):
            continue  # 模板自身比例异常 → 丢弃该候选，不整体报错
        try:
            composed = fe.compose(std, items, label, prices, basis_note=note)
        except ValueError:
            continue
        out.append((std, composed))
    if not out:
        names = "、".join(sorted(exclude)) if exclude else "当前"
        raise ApiError(E_VALIDATION, f"排除 {names} 后暂无可用替代方案，请调整约束")
    return out


async def _persist_candidates(db: AsyncSession, pen: Pen, pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[FeedFormula]:
    rows: list[FeedFormula] = []
    for std, c in pairs:
        row = FeedFormula(
            pen_id=pen.pen_id,
            species_code=pen.species_code or "pig",
            variety_code=pen.variety_code,
            stage=std.get("stage") or pen.batch_stage,
            sex_type=std.get("sex_type", "all"),
            label=c["label"],
            ingredients=c["ingredients"],
            feeds_per_day=c["feeds_per_day"],
            cost_per_head_day=c["cost_per_head_day"],
            nutrition_check=c["nutrition_check"],
            basis=c["basis"],
            disclaimer=c["disclaimer"],
            source="ai",
            is_active=False,
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


@router.post("/feed/formula/generate")
async def generate_formula(
    body: GenerateBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, body.pen_id)
    exclude = _excluded(body.constraints)
    pairs = await _candidate_rows(db, pen, exclude, user.user_id)
    items = pairs[0][1]["ingredients"]
    total = sum(fe.to_float(i["ratio_pct"]) for i in items)
    if not (97 <= total <= 101):  # EX-6c：合计偏离 100% 直接拒（替代逻辑若有 bug 会在此暴露）
        raise ApiError(E_VALIDATION, f"配方原料比例合计需为 100%（当前 {total:g}%）", {"field": "ratio_pct"})
    missing = [i["name"] for i in items if await _price_missing(db, i["name"], user.user_id)]
    if missing:
        raise ApiError(E_VALIDATION, f"原料「{'、'.join(missing)}」暂无价格数据，无法核算成本")
    rows = await _persist_candidates(db, pen, pairs[:1])
    await db.commit()
    return ok(fe.formula_to_api(rows[0]))


async def _price_missing(db: AsyncSession, name: str, user_id: int | None = None) -> bool:
    """该原料对**这个用户**有没有价（全局参考层或本人覆盖层）。

    不按用户过滤时，只要库里任何一户录过价，本人生成配方就不再报「暂无价格数据」，
    而 `latest_prices(user)` 里其实没有这一项——成本会算成缺项却仍然返回成功。
    """
    row = (
        await db.execute(
            select(FeedPrice.price_id)
            .where(FeedPrice.ingredient_name == name, FeedPrice.user_id.is_(None) | (FeedPrice.user_id == user_id))
            .limit(1)
        )
    ).scalars().first()
    return row is None


@router.post("/feed/formula/alternatives")
async def formula_alternatives(
    body: GenerateBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    pen, _ = await resolve_pen(db, user, body.pen_id)
    exclude = _excluded(body.constraints)
    pairs = await _candidate_rows(db, pen, exclude, user.user_id)
    rows = await _persist_candidates(db, pen, pairs)
    await db.commit()
    options = []
    for idx, (row, (_std, composed)) in enumerate(zip(rows, pairs)):
        check = composed["nutrition_check"]
        miss = fe.missing_standards(check)
        item: dict[str, Any] = {
            "formula_id": row.formula_id,
            "label": f"方案{CN_INDEX[idx] if idx < len(CN_INDEX) else idx + 1}（{composed['label']}）",
            "cost_per_head_day": composed["cost_per_head_day"],
            "nutrition_pass": not miss,
            "formula": fe.formula_to_api(row),
        }
        if miss:
            item["miss"] = miss
        options.append(item)
    return ok({"options": options})


@router.post("/feed/formula/{formula_id}/activate")
async def activate_formula(
    formula_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    target = await db.get(FeedFormula, formula_id)
    if target is None:
        raise ApiError(E_NOT_FOUND, "该配方不存在")
    if target.pen_id is None:
        raise ApiError(E_VALIDATION, "该配方未绑定圈舍，无法生效")
    pen, _ = await resolve_pen(db, user, target.pen_id)
    others = (
        await db.execute(select(FeedFormula).where(FeedFormula.pen_id == pen.pen_id, FeedFormula.is_active.is_(True)))
    ).scalars().all()
    for row in others:
        row.is_active = False
    target.is_active = True
    target.activate_time = datetime.now(timezone.utc)
    composition = "·".join(f"{i['name']}{fe.to_float(i['ratio_pct']):g}%" for i in (target.ingredients or []))
    cost = fe.to_float(target.cost_per_head_day or 0)
    ledger = LedgerRecord(
        farm_id=pen.farm_id, pen_id=pen.pen_id, record_type="feed",
        summary=f"{pen.pen_name} 更换配方：{composition} · {cost:.2f} 元/头/天",
        record_ext={"formula_id": target.formula_id, "cost_per_head_day": cost},
    )
    db.add(ledger)
    await db.flush()
    ledger_id = ledger.record_id
    await db.commit()
    return ok({"formula_id": target.formula_id, "is_active": True, "ledger_record_id": ledger_id})


# ---------- 营养标准（F06 展示口径，03-API 未编号）----------

def _std_to_item(r: FeedNutritionLib) -> dict[str, Any]:
    def _num(v: Any) -> float | None:
        return None if v is None else fe.to_float(v)

    return {
        "feed_id": r.feed_id,
        "species_code": r.species_code,
        "variety_code": r.variety_code,
        "stage": r.stage,
        "sex_type": r.sex_type,
        "sow_phase": r.sow_phase,
        "min_weight": _num(r.min_weight),
        "max_weight": _num(r.max_weight),
        "nutrition": r.nutrition or {},
        "formula": r.formula or [],
        "weather_adjust": r.weather_adjust,
        "source": r.source,
    }


@router.get("/feed/standards")
async def list_nutrition_standards(
    species_code: str = Query(default=""),
    variety_code: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """营养标准档位（供「分阶段饲喂方案」卡片读库）。

    此前这张卡片是浏览器直接 filter 前端常量 `FEED_NUTRITION_LIB_SEED`，而配方计算读的是
    `feed_nutrition_lib` 表——同一份标准存两处，库里改了界面不跟（表里的种子本来就是从那个
    常量导出的，见 scripts/export_feed_seed.ts）。展示与计算从此前是同一份数据。
    """
    q = select(FeedNutritionLib)
    if species_code:
        q = q.where(FeedNutritionLib.species_code == species_code)
    if variety_code:
        q = q.where(FeedNutritionLib.variety_code == variety_code)
    rows = (await db.execute(q.order_by(FeedNutritionLib.feed_id))).scalars().all()
    return ok({"total": len(rows), "items": [_std_to_item(r) for r in rows]})


# ---------- 6.6 原料价格 ----------

@router.get("/feed/price")
async def list_feed_prices(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    prices = await fe.latest_prices(db, user.user_id)
    globals_ = await fe.latest_prices(db)  # 全局参考层，用于把"这是我改的价"如实标出来
    items = sorted(prices.values(), key=lambda x: (-x["price_age_days"], x["ingredient_name"]))
    return ok({
        "items": [
            {
                "ingredient_name": p["ingredient_name"],
                "unit_price": p["unit_price"],
                "price_date": p["price_date"].isoformat(),
                "is_builtin": p["is_builtin"],
                "price_age_days": p["price_age_days"],
                "price_source": p.get("price_source", "manual"),
                "source_site": p.get("source_site", ""),
                "confidence": p.get("confidence", ""),
                # C-18：own=本人改价覆盖，global=共享参考价（内置或行情采集）
                "scope": p.get("scope", "global"),
                "global_unit_price": (globals_.get(p["ingredient_name"]) or {}).get("unit_price"),
            }
            for p in items
        ]
    })


# ---------- 行情采集（检索阶段 30~190s，只能点火后台跑） ----------

@admin_router.post("/feed/price/sync")
async def sync_feed_prices(
    body: SyncBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    names = [n.strip() for n in (body.ingredient_names or []) if n.strip()] or None
    unknown = [n for n in (names or []) if n not in ps.BOUNDS]
    if unknown:
        raise ApiError(E_VALIDATION, f"暂无这些原料的行情口径：{'、'.join(unknown)}", {"field": "ingredient_names"})
    if ps.status()["running"]:
        return ok({"started": False, "reason": "already_running"})
    asyncio.create_task(ps.run_once(names))  # 不 await：请求即时返回，前端轮询 sync-status
    return ok({"started": True, "targets": names or sorted(ps.BOUNDS)})


@router.get("/feed/price/sync-status")
async def sync_feed_prices_status(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    st = ps.status()
    prices = await fe.latest_prices(db)
    fresh = sum(1 for p in prices.values() if p.get("price_source") == "llm_search" and p["price_age_days"] <= ps.MAX_AGE_DAYS)
    return ok({**st, "market_covered": fresh, "total_ingredients": len(prices)})


class PriceBody(BaseModel):
    ingredient_name: str = Field(default="")
    unit_price: float | None = None
    price_date: str = Field(default="")


class SyncBody(BaseModel):
    ingredient_names: list[str] = Field(default_factory=list)


@router.post("/feed/price")
async def update_feed_price(
    body: PriceBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    name = body.ingredient_name.strip()
    if not name:
        raise ApiError(E_VALIDATION, "请填写原料名称", {"field": "ingredient_name"})
    if body.unit_price is None or float(body.unit_price) <= 0:
        raise ApiError(E_VALIDATION, "单价需大于 0", {"field": "unit_price"})
    try:
        price_date = datetime.strptime(body.price_date or _today_str(), "%Y-%m-%d").date()
    except ValueError:
        raise ApiError(E_VALIDATION, "日期格式需为 yyyy-MM-dd", {"field": "price_date"}) from None

    prior_builtin = (
        await db.execute(select(FeedPrice.price_id).where(FeedPrice.ingredient_name == name, FeedPrice.is_builtin.is_(True)).limit(1))
    ).scalars().first()
    db.add(
        FeedPrice(
            user_id=user.user_id,  # C-18：农户改价 = 本人覆盖行，不动全局参考价（NULL 那些行）
            ingredient_name=name,
            unit_price=round(float(body.unit_price), 2),
            price_date=price_date,
            is_builtin=prior_builtin is not None,
            price_source="manual",
            source_site="用户录入",
        )
    )
    await db.commit()

    # 改价联动：重算当前账号名下含该原料的**生效**配方成本（数据量小，同步算即可，见遗留项 C-02）。
    # 价格与配方两侧都按 user 收敛：latest_prices(user) 取"本人覆盖 > 全局参考"，
    # 配方再按 Pen→Farm.user_id 过滤，甲改价既不动乙的价、也不算乙的成本。
    prices = await fe.latest_prices(db, user.user_id)
    affected: list[dict[str, Any]] = []
    actives = (
        await db.execute(
            select(FeedFormula)
            .join(Pen, Pen.pen_id == FeedFormula.pen_id)
            .join(Farm, Farm.farm_id == Pen.farm_id)
            .where(FeedFormula.is_active.is_(True), Farm.user_id == user.user_id)
        )
    ).scalars().all()
    for formula in actives:
        if not any(i.get("name") == name for i in (formula.ingredients or [])):
            continue
        cost = 0.0
        ok_all = True
        for i in formula.ingredients or []:
            price = prices.get(i.get("name", ""))
            if price is None:
                ok_all = False
                break
            cost += fe.to_float(i.get("daily_kg_per_head", 0)) * fe.to_float(price["unit_price"])
        if not ok_all:
            continue
        formula.cost_per_head_day = fe.round2(cost)
        pen = await db.get(Pen, formula.pen_id) if formula.pen_id else None
        affected.append({
            "formula_id": formula.formula_id, "pen_id": formula.pen_id,
            "pen_name": pen.pen_name if pen else "", "cost_per_head_day": fe.round2(cost),
        })
    await db.commit()
    return ok({"updated": True, "affected": affected})
