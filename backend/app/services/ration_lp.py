"""最小成本配方求解（LP）— 把"查模板"升级为"按饲养标准区间解一次"。

变量 `x_i` = 原料 i 在日粮中的百分比。目标最小化 `Σ x_i·单价`（等比于 元/头/天，
因为日采食量对同一圈舍是常数），约束全部来自已有数据：

- `Σ x_i = 100`、`x_i ≥ 0`；
- 每条营养指标按 `nutrition_lib.indicators` 的区间：`min ≤ Σ x_i·n_ij/100 ≤ max`
  （`max=null` 视为只有下限，与 `nutrition_check` 同口径）；
- **预混料锁死在标准模板自身的比例**：它负责补齐维生素/微量元素，不是能量蛋白原料，
  让 LP 自由调它会解出"靠预混料堆赖氨酸和钙"的荒谬配方（预混料钙 12%、赖氨酸 6%，
  单价又低，不设限必然被大量选用）；
- **牛羊粗饲料下限**取该品种标准模板自身的粗饲料占比，不另编数字；
- 单料上限 `CAP_PCT` 是演示参数（非国标），防止某一原料独大，已写进 basis 文案。

两处刻意设计的重试：
1. 营养区间按 `BOUND_MARGINS` 从 0 开始**逐档加余量**，取第一个"舍入到 1 位小数后
   仍达标"的解。不留余量时最优解常正好压在边界（实测消化能 13.0，舍入后 12.99 判不
   达标）；一上来固定留 1.5% 又会把贴着数据上限的下限直接判成无解（排除豆粕时消化能
   可达上限只有 13.19，13×1.015=13.195 → 无解）。
2. 不可行时先丢**软约束**（粗饲料下限）重解并记录丢了什么；仍无解才返回 None，
   由调用方回退模板。绝不输出"解出来了但营养不达标"的配方。
"""

from __future__ import annotations

from typing import Any

INDICATOR_KEY = {"粗蛋白": "cp_pct", "赖氨酸": "lys_pct", "消化能": "de_mj", "钙": "ca_pct"}

# 单料上限（%）：演示参数，需配方师/兽医复核后替换为正式约束
CAP_PCT: dict[str, float] = {
    "玉米": 70, "豆粕": 25, "菜籽粕": 18, "鱼粉": 8, "麸皮": 25,
    "稻谷": 30, "青贮玉米": 45, "干草粉": 40, "预混料": 10,
}
DEFAULT_CAP = 60.0
MIN_USEFUL_PCT = 1.0  # 低于 1% 实操难称量，固定为 0 后重解
# 舍入安全余量阶梯：LP 最优解常压在营养边界上（实测消化能正好 13.0），比例保留 1 位
# 小数后会掉到 12.99 判为不达标。一上来就固定留 1.5% 余量也不行——排除豆粕时消化能
# 可达上限只有 13.19，13×1.015=13.195 直接无解。因此从 0 开始逐档加，取第一个
# 舍入后仍达标的解。
BOUND_MARGINS = (0.0, 0.004, 0.008, 0.012, 0.015)
FORAGE_NAMES = ("青贮玉米", "干草粉")

Row = tuple[str, list[float], float]  # (标签, 系数行, 右端值)，语义为 系数·x ≤ 右端


def _template_ratio(std: dict[str, Any], names: tuple[str, ...]) -> float:
    return sum(float(i.get("ratio_pct") or 0) for i in std.get("formula") or [] if i.get("name") in names)


def _solve(names: list[str], cost: list[float], rows: list[Row], bounds: list[tuple[float, float]]) -> list[float] | None:
    from scipy.optimize import linprog  # 局部导入：未装 scipy 时由调用方降级

    res = linprog(
        c=cost,
        A_ub=[r[1] for r in rows],
        b_ub=[r[2] for r in rows],
        A_eq=[[1.0] * len(names)],
        b_eq=[100.0],
        bounds=bounds,
        method="highs",
    )
    return [float(v) for v in res.x] if (res.success and res.x is not None) else None


def _nutrition_rows(std: dict[str, Any], names: list[str], seed_ing: dict[str, Any], margin: float) -> list[Row]:
    out: list[Row] = []
    for ind in std["nutrition"].get("indicators") or []:
        key = INDICATOR_KEY.get(str(ind.get("name", "")))
        if not key:
            continue
        coef = [float(seed_ing[n].get(key) or 0) for n in names]
        if not any(coef):
            continue
        lo, hi = ind.get("min"), ind.get("max")
        if lo is not None:
            out.append((f"{ind['name']}≥{lo}", [-c for c in coef], -100.0 * float(lo) * (1 + margin)))
        if hi is not None:
            out.append((f"{ind['name']}≤{hi}", coef, 100.0 * float(hi) * (1 - margin)))
    return out


def _forage_rows(names: list[str], forage_floor: float) -> list[Row]:
    idx = [i for i, n in enumerate(names) if n in FORAGE_NAMES]
    if forage_floor <= 0 or not idx:
        return []
    row = [0.0] * len(names)
    for i in idx:
        row[i] = -1.0
    return [(f"粗饲料≥{forage_floor:g}%", row, -forage_floor)]


def _rounded_items(names: list[str], x: list[float]) -> list[dict[str, Any]]:
    """保留 1 位小数，舍入残差并入最大项，保证合计恒等 100。"""
    ratios = [round(v, 1) for v in x]
    if sum(ratios) <= 0:
        return []
    top = max(range(len(ratios)), key=lambda i: ratios[i])
    ratios[top] = round(ratios[top] + (100.0 - sum(ratios)), 1)
    return [{"name": n, "ratio_pct": r} for n, r in zip(names, ratios) if r > 0]


def _attempt(
    std: dict[str, Any], names: list[str], prices: dict[str, dict[str, Any]], seed_ing: dict[str, Any], margin: float
) -> tuple[list[dict[str, Any]], list[str]] | None:
    premix = _template_ratio(std, ("预混料",))
    bounds: list[tuple[float, float]] = [
        (premix, premix) if n == "预混料" and premix > 0 else (0.0, CAP_PCT.get(n, DEFAULT_CAP)) for n in names
    ]
    hard = _nutrition_rows(std, names, seed_ing, margin)
    active = hard + _forage_rows(names, _template_ratio(std, FORAGE_NAMES))
    cost = [float(prices[n]["unit_price"]) for n in names]

    dropped: list[str] = []
    x = _solve(names, cost, active, bounds)
    if x is None and len(active) != len(hard):  # 粗饲料下限导致无解 → 丢弃它重解并记录
        dropped = [r[0] for r in active if r[0].startswith("粗饲料")]
        active = hard
        x = _solve(names, cost, active, bounds)
    if x is None:
        return None

    for _ in range(3):  # 剔除难称量的微量原料后重解，保证比例仍可执行
        tiny = {i for i, v in enumerate(x) if 0 < v < MIN_USEFUL_PCT and bounds[i][0] == 0}
        if not tiny:
            break
        bounds = [(lo, 0.0 if i in tiny else hi) for i, (lo, hi) in enumerate(bounds)]
        again = _solve(names, cost, active, bounds)
        if again is None:
            break
        x = again

    from app.services.feed_engine import nutrition_check  # 局部导入：避开与 feed_engine 的循环依赖

    items = _rounded_items(names, x)
    if not items:
        return None
    if premix > 0 and not any(i["name"] == "预混料" for i in items):
        return None  # 预混料被剔除 → 维生素/微量元素无从补齐，解不可信
    if any(not c.get("pass", True) for c in nutrition_check(items, std["nutrition"].get("indicators") or []).values()):
        return None  # 舍入后掉出区间 → 交给带余量的第二轮
    return items, dropped


def least_cost(
    std: dict[str, Any], prices: dict[str, dict[str, Any]], exclude: set[str], seed_ing: dict[str, Any]
) -> tuple[list[dict[str, Any]], str] | None:
    """返回 (配方 items, 说明文案)；无解或数据不足返回 None（调用方回退模板）。"""
    names = [n for n in seed_ing if n in prices and n not in exclude]
    if len(names) < 3:
        return None
    for margin in BOUND_MARGINS:
        got = _attempt(std, names, prices, seed_ing, margin)
        if got is None:
            continue
        items, dropped = got
        note = "最小成本 LP 求解：营养区间取自本条饲养标准，预混料锁定模板比例，单料上限为演示参数待复核"
        if dropped:
            note += f"；不可行时已丢弃软约束：{'、'.join(dropped)}"
        return items, note
    return None
