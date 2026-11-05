"""F06 配方引擎 — 确定性模板引擎，不调 LLM（F06 PRD 决策 1：配方必须可复算、可审计）。

数据源：`app/agents/feed_seed.json`（scripts/export_feed_seed.ts 从前端 feed-data.ts 机械导出），
落库为 feed_nutrition_lib / feed_prices，本模块只做计算。

口径要点（与 mock 逐值一致，冒烟按字面断言）：
- `daily_kg_per_head = round2(标准日采食量 × ratio_pct / 100)`，**先取整再算钱**；
- `cost_per_head_day = round2(Σ daily_kg_per_head × 最新价)`；
- 计划量 = `round2(Σ 各原料日喂量) × 存栏`，再按气象规则下调；
- 营养达标 = 加权 `Σ(ratio_pct × 成分) / Σ ratio_pct`，闭区间，单边 null 视为不设限；
- 高温下调阈值（喂食 32/30℃）与气象告警阈值（35/30℃）**是两套数**，不可混用（RK-3）。
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BreedingFemale, FeedFormula, FeedNutritionLib, FeedPrice, Pen

_SEED_PATH = Path(__file__).resolve().parent.parent / "agents" / "feed_seed.json"

FEED_TYPES = {"concentrate", "roughage", "silage", "other"}
FORMULA_DISCLAIMER = "AI 生成，仅供参考"
NO_PLAN_BASIS = "请先生成配方"

# 指标名 → 成分字段
NUTRIENT_KEY = {"粗蛋白": "cp_pct", "赖氨酸": "lys_pct", "消化能": "de_mj", "钙": "ca_pct"}


@lru_cache(maxsize=1)
def load_seed() -> dict[str, Any]:
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def to_float(v: Any) -> float:
    """DB Numeric/Decimal 与 JSON float 混用，统一成 float。"""
    if isinstance(v, Decimal):
        return float(v)
    return float(v or 0)


def round2(v: float) -> float:
    return round(v + 0.0, 2)


def round3(v: float) -> float:
    return round(v + 0.0, 3)


def round4(v: float) -> float:
    return round(v + 0.0, 4)


def days_between(a: date, b: date) -> int:
    """至少 1 天，防同日除零。"""
    return max(1, round((b - a).days))


# ===== 种子落库（幂等）=====

async def seed_feed_data(db: AsyncSession) -> None:
    """营养库 + 原料价初始化；按业务键判重，用户改价的历史行永不受影响。"""
    seed = load_seed()
    have = set((await db.execute(select(FeedNutritionLib.variety_code, FeedNutritionLib.stage, FeedNutritionLib.sex_type, FeedNutritionLib.sow_phase))).all())
    for row in seed["nutrition_lib"]:
        key = (row["variety_code"], row.get("stage"), row.get("sex_type", "all"), row.get("sow_phase"))
        if key in have:
            continue
        db.add(
            FeedNutritionLib(
                species_code=row["species_code"],
                variety_code=row["variety_code"],
                stage=row.get("stage"),
                sex_type=row.get("sex_type", "all"),
                sow_phase=row.get("sow_phase"),
                min_weight=row.get("min_weight"),
                max_weight=row.get("max_weight"),
                nutrition=row["nutrition"],
                formula=row["formula"],
                weather_adjust=row.get("weather_adjust"),
                source=row.get("source", ""),
            )
        )
    caliber = seed.get("price_calibration", "")
    existing_names = set((await db.execute(select(FeedPrice.ingredient_name))).scalars())
    today = datetime.now(timezone.utc).date()
    for p in seed["price_seed"]:
        if p["ingredient_name"] in existing_names:
            continue  # 只补新原料，绝不覆盖用户历史价
        db.add(
            FeedPrice(
                ingredient_name=p["ingredient_name"],
                unit_price=p["unit_price"],
                price_date=today - timedelta(days=p["days_ago"]),
                is_builtin=True,
                seed_caliber=caliber,
            )
        )
    await db.commit()


# ===== 价格 =====

async def latest_prices(db: AsyncSession, user_id: int | None = None) -> dict[str, dict[str, Any]]:
    """每种原料的**生效价**：本人改价覆盖 > 全局参考价（02-数据库 3.5 / C-18）。

    桶内取 `(price_date, price_id)` 最大者；`user_id` 命中行优先于 `user_id IS NULL` 的全局行，
    其他农户的改价行一律不参与（此前 `feed_prices` 没有租户维度，甲改玉米价会重算并改写
    乙的生效配方成本——mock 时代每人一份 localStorage 天然隔离，切真后这层隔离丢了）。
    `user_id=None` 只看全局行：内置种子与行情采集都是全局口径，不该被任何个人改价污染。
    """
    rows = (
        await db.execute(select(FeedPrice).order_by(FeedPrice.ingredient_name, FeedPrice.price_date, FeedPrice.price_id))
    ).scalars().all()
    global_latest: dict[str, FeedPrice] = {}
    own_latest: dict[str, FeedPrice] = {}
    for r in rows:
        if r.user_id is None:
            global_latest[r.ingredient_name] = r
        elif user_id is not None and r.user_id == user_id:
            own_latest[r.ingredient_name] = r
    chosen = {**global_latest, **own_latest}
    out: dict[str, dict[str, Any]] = {}
    for name, r in chosen.items():
        out[name] = {
            "ingredient_name": r.ingredient_name,
            "unit_price": to_float(r.unit_price),
            "price_date": r.price_date,
            "is_builtin": r.is_builtin,
            "price_source": r.price_source or ("builtin" if r.is_builtin else "manual"),
            "source_site": r.source_site or "",
            "confidence": r.confidence or "",
            "scope": "own" if r.user_id is not None else "global",
        }
    today = datetime.now(timezone.utc).date()
    for info in out.values():
        info["price_age_days"] = max(0, (today - info["price_date"]).days)  # 0=今天更新（EX-17 独立口径）
    return out


# ===== 营养标准匹配 =====

def _row_dict(r: FeedNutritionLib) -> dict[str, Any]:
    return {
        "feed_id": r.feed_id,
        "species_code": r.species_code,
        "variety_code": r.variety_code,
        "stage": r.stage,
        "sex_type": r.sex_type,
        "sow_phase": r.sow_phase,
        "min_weight": r.min_weight,
        "max_weight": r.max_weight,
        "nutrition": r.nutrition or {},
        "formula": r.formula or [],
        "weather_adjust": r.weather_adjust,
        "source": r.source or "",
    }


async def _sow_phase_of(db: AsyncSession, pen_id: int) -> str:
    """母猪圈按在栏母畜实际状态分档：有哺乳中的母畜 → lactation，否则 gestation。"""
    rows = (
        await db.execute(
            select(BreedingFemale.breed_status).where(
                BreedingFemale.pen_id == pen_id, BreedingFemale.is_deleted.is_(False)
            )
        )
    ).scalars().all()
    return "lactation" if "nursing" in rows else "gestation"


async def match_nutrition_standard(db: AsyncSession, pen: Pen) -> dict[str, Any] | None:
    """五级优先匹配（品种无模板且同物种参考品种也无 → None → 上层报 42201）。

    2026-11-05 品种扩容回落：/variety/list 新增品种（杜泊羊/西门塔尔牛等）暂无
    专项营养标准，此处按同物种参考品种（羊→湖羊、牛→肉牛、猪→育肥猪）回落，
    并在 std 上带 variety_fallback_note 供 compose 拼进 basis，向用户说明依据。
    """
    rows = (
        await db.execute(select(FeedNutritionLib).where(FeedNutritionLib.variety_code == (pen.variety_code or "")))
    ).scalars().all()
    fallback_note = ""
    if not rows:
        v = pen.variety_code or ""
        species = next((p for p in ("sheep", "cattle", "pig") if v.startswith(p)), None)
        ref = {"sheep": "sheep_hu", "cattle": "cattle_beef", "pig": "pig_fattening"}.get(species)
        if ref:
            rows = (
                await db.execute(select(FeedNutritionLib).where(FeedNutritionLib.variety_code == ref))
            ).scalars().all()
            if rows:
                fallback_note = "该品种暂无专项营养标准，已按同物种参考品种档位计算，仅供近似参考"
    if not rows:
        return None
    pool = [_row_dict(r) for r in rows]
    if fallback_note:
        for s in pool:
            s["variety_fallback_note"] = fallback_note
    sex_type = "female" if (pen.male_count == 0 and pen.female_count > 0) else "all"

    if pen.variety_code == "pig_sow":
        phase = await _sow_phase_of(db, pen.pen_id)
        hit = next((s for s in pool if s.get("sow_phase") == phase), None)
        if hit:
            return hit

    for s in pool:
        if s["stage"] == pen.batch_stage and (s["sex_type"] == "all" or s["sex_type"] == sex_type):
            return s
    same_sex = [s for s in pool if s["sex_type"] == sex_type]
    if same_sex:
        # 原写法 `next((s for s in same_sex if ...) or same_sex[0])` 是优先级陷阱：
        # 生成器对象恒为真 → 永远 `next(生成器)`，没有 stage=None 的通用档时直接抛
        # StopIteration → 未设生长阶段的圈舍点「生成配方」得到 50001 服务异常。
        generic = next((s for s in same_sex if s["stage"] is None), None)
        return generic or same_sex[0]
    return pool[0]


def sow_phase_label(std: dict[str, Any], pen: Pen) -> str:
    """母猪档说明文案（追加进 basis 括号内）。"""
    if pen.variety_code != "pig_sow" or not std.get("sow_phase"):
        return ""
    return "哺乳" if std["sow_phase"] == "lactation" else "妊娠"


# ===== 营养达标校验 =====

def nutrition_check(items: list[dict[str, Any]], indicators: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ing = load_seed()["ingredient_nutrition"]
    total_ratio = sum(to_float(i["ratio_pct"]) for i in items) or 1.0
    out: dict[str, dict[str, Any]] = {}
    for ind in indicators or []:
        name = ind.get("name", "")
        key = NUTRIENT_KEY.get(name)
        if not key:
            out[name] = {"required": ind.get("required", ""), "actual": "—", "pass": True}
            continue
        value = sum(to_float(i["ratio_pct"]) * to_float(ing.get(i["name"], {}).get(key, 0)) for i in items) / total_ratio
        lo, hi = ind.get("min"), ind.get("max")
        passed = not (lo is not None and value < to_float(lo)) and not (hi is not None and value > to_float(hi))
        unit = ind.get("unit", "%")
        actual = f"{value:.2f} MJ/kg" if unit == "MJ/kg" else f"{value:.1f}{unit}"
        out[name] = {"required": ind.get("required", ""), "actual": actual, "pass": passed}
    return out


# ===== 配方组装 =====

def _daily_kg_per_head(std: dict[str, Any], ratio_pct: float) -> float:
    return round2(to_float(std["nutrition"].get("daily_feed_kg_per_head", 0)) * ratio_pct / 100)


def compose(
    std: dict[str, Any],
    formula_items: list[dict[str, Any]],
    label: str,
    prices: dict[str, dict[str, Any]],
    basis_note: str = "",
) -> dict[str, Any]:
    """把「比例」变成「可直接执行的配方」：每顿每头喂量 + 每天每头成本。"""
    ingredients = []
    cost = 0.0
    for it in formula_items:
        name = it["name"]
        kg = _daily_kg_per_head(std, to_float(it["ratio_pct"]))
        price = prices.get(name)
        if price is None:
            raise ValueError(f"原料「{name}」暂无价格数据，无法核算成本")
        cost += kg * to_float(price["unit_price"])
        ingredients.append({"name": name, "ratio_pct": to_float(it["ratio_pct"]), "daily_kg_per_head": kg})
    basis = std["source"] if not basis_note else f"{std['source']}；{basis_note}"
    if std.get("variety_fallback_note"):
        basis = f"{basis}；{std['variety_fallback_note']}" if basis else std["variety_fallback_note"]
    return {
        "label": label,
        "ingredients": ingredients,
        "feeds_per_day": int(std["nutrition"].get("feeds_per_day", 2)),
        "cost_per_head_day": round2(cost),
        "nutrition_check": nutrition_check(formula_items, std["nutrition"].get("indicators") or []),
        "basis": basis,
        "disclaimer": FORMULA_DISCLAIMER,
    }


def apply_exclude(items: list[dict[str, Any]], exclude: set[str], alt_index: int) -> list[dict[str, Any]] | None:
    """排除原料 → 按 SUBSTITUTE_MAP 顶替，比例**结转合并**（合计恒等 100）。

    替代映射为空或全被排除 → 该模板不可行返回 None（不硬凑）。
    """
    subs_map = load_seed()["substitute_map"]
    out: list[dict[str, Any]] = []
    carried: dict[str, float] = {}
    for it in items:
        name = it["name"]
        if name not in exclude:
            out.append({"name": name, "ratio_pct": to_float(it["ratio_pct"])})
            continue
        candidates = [s for s in (subs_map.get(name) or []) if s not in exclude]
        if not candidates:
            return None
        sub = candidates[alt_index % len(candidates)]
        carried[sub] = carried.get(sub, 0) + to_float(it["ratio_pct"])
    for sub, ratio in carried.items():
        hit = next((o for o in out if o["name"] == sub), None)
        if hit:
            hit["ratio_pct"] = to_float(hit["ratio_pct"]) + ratio
        else:
            out.append({"name": sub, "ratio_pct": ratio})
    return out


def _dedup_key(items: list[dict[str, Any]]) -> str:
    return "|".join(sorted(f"{i['name']}+{i['ratio_pct']}" for i in items))


def build_candidates(
    std: dict[str, Any], exclude: set[str], prices: dict[str, dict[str, Any]] | None = None
) -> list[tuple[str, list[dict[str, Any]], str]]:
    """候选配方集合：LP 最小成本解（有价格才解）+ 基础模板 + 替代模板。

    LP 排在最前是刻意的：它是按当前行情算出来的最优解，模板只作对照与兜底
    （数据不足、无解或舍入后营养不达标的情况由 `least_cost` 自己返回 None）。
    """
    stage = std.get("stage") or ""
    templates = load_seed()["alt_templates"].get(f"{std['variety_code']}|{stage}") or []
    out: list[tuple[str, list[dict[str, Any]], str]] = []
    seen: set[str] = set()

    def push(label: str, items: list[dict[str, Any]] | None, note: str = "") -> None:
        if not items:
            return
        k = _dedup_key(items)
        if k in seen:
            return
        seen.add(k)
        out.append((label, items, note))

    if prices:
        try:
            from app.services.ration_lp import least_cost

            solved = least_cost(std, prices, exclude, load_seed()["ingredient_nutrition"])
            if solved:
                push("最小成本配方", solved[0], solved[1])
        except ImportError:
            pass  # 未装 scipy → 静默走模板路径（旧行为）

    if not exclude:
        push("基础配方", [{"name": i["name"], "ratio_pct": to_float(i["ratio_pct"])} for i in std["formula"]])
        for alt in templates:
            req = set(alt.get("requiresExclude") or [])
            if req:
                continue
            push(alt["label"], [{"name": i["name"], "ratio_pct": to_float(i["ratio_pct"])} for i in alt["formula"]])
    else:
        for alt in templates:
            req = set(alt.get("requiresExclude") or [])
            if req and not (req & exclude):
                continue
            base_items = [{"name": i["name"], "ratio_pct": to_float(i["ratio_pct"])} for i in alt["formula"]]
            replaced = sorted(n for n in (req | exclude) if any(i["name"] == n for i in alt["formula"]))
            push(alt["label"], apply_exclude(base_items, exclude, 0) if replaced else base_items)

    if not out:  # 兜底：对标准配方逐轮替代（羊/牛无模板时走这条）
        base = [{"name": i["name"], "ratio_pct": to_float(i["ratio_pct"])} for i in std["formula"]]
        kept = {i["name"] for i in base if i["name"] not in exclude}
        replaced = "、".join(sorted(n for n in exclude if any(i["name"] == n for i in base))) or "当前"
        for idx in range(3):
            cand = apply_exclude(base, exclude, idx)
            if not cand:
                continue
            # 标签必须带上"换成了什么"：羊/牛走这条兜底时，两套方案的排除项同名，
            # 只写「豆粕替代」会让用户看到两个同名方案无法区分（实测复现）
            subs = "、".join(sorted(i["name"] for i in cand if i["name"] not in kept)) or "配比"
            push(f"{replaced}→{subs}替代", cand)
            if len(out) >= 2:
                break
    return out


# ===== 饲喂计划（三级优先：生效配方 → 营养标准 → 无计划）=====

def _temp_max_of(payload: dict[str, Any] | None, today: str) -> float | None:
    if not payload:
        return None
    for d in payload.get("forecast") or []:
        if d.get("date") == today:
            return to_float(d.get("temp_max", 0))
    fc = payload.get("forecast") or []
    return to_float(fc[0]["temp_max"]) if fc else None


def apply_weather_adjust(kg: float, weather_adjust: dict[str, Any] | None, temp_max: float | None) -> tuple[float, str]:
    """高温下调精料。返回 (调整后 kg, 追加到 basis 的说明)；无数据/无规则 → 原值 + 空串。"""
    if kg is None or not weather_adjust or temp_max is None:
        return kg, ""
    rule = weather_adjust.get("high_temp") or {}
    gt = rule.get("temp_gt")
    if gt is None or temp_max < to_float(gt):
        return kg, ""
    pct = to_float(rule.get("reduce_concentrate_pct", 0))
    return round2(kg * (1 - pct / 100)), f" · 高温下调 {pct:.0f}%（依据气象阈值 {int(to_float(gt))}℃）"


async def calc_daily_plan(db: AsyncSession, pen: Pen, temp_max: float | None) -> dict[str, Any]:
    """返回 {stage, stock, daily_plan_kg, meals_per_day, per_meal_kg, basis, plan_source, active_formula}。"""
    stock = pen.male_count + pen.female_count
    stage = pen.batch_stage or ""
    formula = (
        await db.execute(
            select(FeedFormula).where(FeedFormula.pen_id == pen.pen_id, FeedFormula.is_active.is_(True))
        )
    ).scalars().first()
    std = await match_nutrition_standard(db, pen)

    if formula:
        total = round2(sum(to_float(i.get("daily_kg_per_head", 0)) for i in (formula.ingredients or [])))
        adjust = std.get("weather_adjust") if std else None
        kg, note = apply_weather_adjust(total, adjust, temp_max)
        plan = round2(kg * stock)
        phase = sow_phase_label(std, pen) if std else ""
        return {
            "stage": stage,
            "stock": stock,
            "daily_plan_kg": plan,
            "meals_per_day": formula.feeds_per_day,
            "per_meal_kg": round2(plan / max(1, formula.feeds_per_day)),
            "basis": f"生效配方 · {formula.basis}" + (f"（{phase}）" if phase else "") + note,
            "plan_source": "formula",
            "active_formula": formula_to_api(formula),
        }
    if std:
        kg, note = apply_weather_adjust(to_float(std["nutrition"].get("daily_feed_kg_per_head", 0)), std.get("weather_adjust"), temp_max)
        plan = round2(kg * stock)
        phase = sow_phase_label(std, pen)
        return {
            "stage": stage,
            "stock": stock,
            "daily_plan_kg": plan,
            "meals_per_day": int(std["nutrition"].get("feeds_per_day", 2)),
            "per_meal_kg": round2(plan / max(1, int(std["nutrition"].get("feeds_per_day", 2)))),
            "basis": f"标准兜底 · {std['source']}" + (f"（{phase}）" if phase else "") + note,
            "plan_source": "standard",
            "active_formula": None,
        }
    return {
        "stage": stage,
        "stock": stock,
        "daily_plan_kg": None,
        "meals_per_day": None,
        "per_meal_kg": None,
        "basis": NO_PLAN_BASIS,
        "plan_source": "none",
        "active_formula": None,
    }


def formula_to_api(f: FeedFormula) -> dict[str, Any]:
    return {
        "formula_id": f.formula_id,
        "pen_id": f.pen_id,
        "label": f.label,
        "ingredients": f.ingredients or [],
        "feeds_per_day": f.feeds_per_day,
        "cost_per_head_day": None if f.cost_per_head_day is None else to_float(f.cost_per_head_day),
        "nutrition_check": f.nutrition_check or {},
        "basis": f.basis,
        "disclaimer": f.disclaimer,
        "is_active": bool(f.is_active),
        "activate_time": f.activate_time.isoformat() if f.activate_time else None,
    }


def missing_standards(check: dict[str, dict[str, Any]]) -> list[str]:
    return [name for name, v in check.items() if not v.get("pass")]


def ratio_sum_ok(items: list[dict[str, Any]]) -> bool:
    total = sum(to_float(i["ratio_pct"]) for i in items)
    return 97 <= total <= 101


def estimate_chunk(content_md: str) -> int:
    return max(1, math.ceil(len(content_md) / 300))
