"""原料行情采集 — LLM 联网检索 + 结构化抽取 + 本地回验。

为什么不是"让大模型报个价"：实测 qwen 在无检索时会拒答或给训练记忆里的陈旧数字，
带 enable_search 时检索索引滞后 1~3 天且会把同一条报价重复多行。因此本模块把模型
降级成**检索搬运工**，报价的数值可信性由四道本地校验兜住：

1. 摘录回查：每条报价必须逐字出现在第一步检索返回的原文里（归一化后子串匹配），
   查不到即判为模型自造，丢弃；
2. 单位换算不信模型：`元/吨`→`元/公斤` 由本模块正则解析后自己算，多值取中位数；
3. 数量级边界 + 报价日期时效：越界或超过 30 天一律丢弃；
4. 站点名核验：站点为空或含"知识库/训练/记忆/未提供"等字样即丢弃——实测模型会把
   来源写成「你的知识库」，那意味着它没联网，摘录回查对这种情况无效。

采集结果作为 `is_builtin=false` 的价格追加进 feed_prices（历史追加式，改价不覆盖），
配方成本与 元/头/天 自然随之变化。取不到的原料沿用内置参考价，不硬凑。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models import FeedPrice
from app.services import llm

logger = logging.getLogger("app.price")

# 数量级边界（元/公斤）：明显越界说明抽错单位或抓错品种
BOUNDS: dict[str, tuple[float, float]] = {
    "玉米": (1.5, 4.0),
    "豆粕": (2.5, 6.5),
    "麸皮": (0.8, 2.6),
    "菜籽粕": (1.5, 4.5),
    "鱼粉": (8.0, 30.0),
    "稻谷": (1.8, 4.0),
    "预混料": (2.0, 20.0),
}
MAX_AGE_DAYS = 30

# 实测踩到：模型会把报价的来源站点写成「你的知识库」——即它没用检索、用的是训练记忆。
# 摘录回查只能证明"数字出现在第一步输出里"，证明不了它来自网页，故站点名必须再过一道。
_MEMORY_SITE_RE = re.compile(r"知识库|训练|记忆|内部|模型|自身|无法确定|未提供|—|-")

_COLLECT_PROMPT = (
    "联网检索「{name}」最近 7 天在中国饲料市场的现货报价。"
    "不要总结、不要估算、不要用你记忆里的历史价。"
    "把检索到的每一条报价**按原样摘录**，每条一行，格式："
    "品种 | 价格(照抄原文单位，如 元/吨 或 元/公斤) | 地区 | 文章或页面日期 | 站点名。"
    "同一品种可以有多行。若确实检索不到该品种的报价，只输出一行：{name} | NONE。"
)

_MAX_PARALLEL = 4


async def collect_raw_text(names: list[str]) -> list[str]:
    """逐品种并行检索（实测一次性问全部品种时召回极差，11s 就几乎空手返回）。

    返回每个品种各自的摘录正文——`verify()` 用逐条回查，天然按品种隔离语料。
    """
    sem = asyncio.Semaphore(_MAX_PARALLEL)

    async def one(name: str) -> str:
        async with sem:
            try:
                return await llm.chat(
                    [{"role": "user", "content": _COLLECT_PROMPT.format(name=name)}],
                    temperature=0.0,
                    max_tokens=900,
                    timeout=300.0,
                    extra={"enable_search": True, "search_options": {"forced_search": True}},
                )
            except llm.LLMError as e:
                logger.warning("行情检索「%s」失败：%s", name, e)
                return ""

    return list(await asyncio.gather(*(one(n) for n in names)))

_EXTRACT_PROMPT = (
    "下面是一段饲料原料行情摘录（可能含重复行与区间报价）。请逐条抽取为 JSON，不要引入摘录之外的信息。"
    '输出 {{"prices":[{{"name":"玉米","raw_quote":"2350元/吨","quote_date":"2026-10-15",'
    '"region":"全国","site":"生意社"}}]}}。要求：'
    "1) raw_quote 必须是摘录中该条报价的原样子串（含单位），不得改写；"
    "2) quote_date 用该条自己的日期，格式 yyyy-MM-dd；找不到日期填空字符串；"
    "3) 品种只认这些：{names}；摘录外的品种一律忽略；"
    "4) 只输出 JSON。"
)

# 2350元/吨、2 280 元 / 吨、2280元/吨-2300元/吨
_QUOTE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[-~－至]\s*(\d+(?:\.\d+)?))?\s*元\s*/\s*(吨|公斤|kg|Kg|KG|t|T)")


def _norm(text: str) -> str:
    return re.sub(r"[\s*`]+", "", text)


def quote_to_yuan_per_kg(raw_quote: str) -> float | None:
    """从报价原文解析元/公斤：区间取中点，元/吨除以 1000。解析不出返回 None。"""
    m = _QUOTE_RE.search(raw_quote or "")
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    if hi < lo:
        lo, hi = hi, lo
    value = (lo + hi) / 2
    unit = m.group(3)
    return round(value / 1000 if unit in ("吨", "t", "T") else value, 4)


def _parse_date(text: str) -> date | None:
    m = re.search(r"(20\d{2})[-/.年]\s*(\d{1,2})[-/.月]\s*(\d{1,2})", text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_lines(raw_text: str) -> list[dict[str, Any]]:
    """直接解析第一步的「品种 | 价格 | 地区 | 日期 | 站点」行——比再叫一次模型可靠。

    模型在这里只负责把检索到的报价**抄成行**，解析和换算全在本地做，
    这样数字从不经过第二次模型生成，幻觉面只剩"抄错原文"一种，而那种情况
    会被 `verify()` 的边界与时效校验挡掉。
    """
    rows: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        cells = [c.strip().strip("*# ") for c in re.split(r"[|｜]", line) if c.strip()]
        if len(cells) < 3:
            continue
        name = re.sub(r"[*#\s]+", "", cells[0])
        if name not in BOUNDS:
            continue
        quote = next((c for c in cells[1:] if "元" in c and _QUOTE_RE.search(c)), "")
        if not quote:
            continue
        qdate = next((c for c in cells[1:] if _parse_date(c)), "")
        rest = [c for c in cells[1:] if c != quote and not _parse_date(c) and not c.startswith(("http", "NONE"))]
        rows.append({"name": name, "raw_quote": quote, "quote_date": qdate, "site": rest[-1] if rest else ""})
    return rows


async def extract_quotes(raw_text: str, names: list[str]) -> list[dict[str, Any]]:
    """行解析拿不到东西时的兜底：把摘录正文交给模型结构化为 JSON。"""
    body = await llm.chat(
        [{"role": "user", "content": _EXTRACT_PROMPT.format(names="、".join(names)) + "\n\n摘录正文：\n" + raw_text[:6000]}],
        temperature=0.0,
        max_tokens=1200,
        timeout=120.0,
    )
    obj = llm.extract_json(body)
    rows = obj.get("prices")
    return rows if isinstance(rows, list) else []


def verify(rows: list[dict[str, Any]], raw_text: list[str]) -> dict[str, dict[str, Any]]:
    """四道校验 → {品种: {unit_price, price_date, site, samples}}（同品种取多条中位数）。"""
    corpus = _norm("".join(raw_text))
    today = datetime.now(timezone.utc).date()
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name", "")).strip()
        quote = str(row.get("raw_quote", "")).strip()
        if name not in BOUNDS or not quote:
            continue
        if _norm(quote) not in corpus:  # 校验①：摘录回查
            continue
        price = quote_to_yuan_per_kg(quote)  # 校验②：本地换算为准
        if price is None:
            continue
        lo, hi = BOUNDS[name]
        if not (lo <= price <= hi):
            continue
        qdate = _parse_date(str(row.get("quote_date", "")) + " " + quote)  # 校验③：日期时效
        if qdate is None or (today - qdate).days > MAX_AGE_DAYS or qdate > today:
            continue
        site = str(row.get("site", "")).strip()
        if not site or _MEMORY_SITE_RE.search(site):  # 校验④：站点名必须像个网页来源
            continue
        b = buckets.setdefault(name, {"prices": [], "dates": [], "sites": []})
        b["prices"].append(price)
        b["dates"].append(qdate)
        b["sites"].append(site[:64])
    out: dict[str, dict[str, Any]] = {}
    for name, b in buckets.items():
        out[name] = {
            "unit_price": round(median(b["prices"]), 2),
            "price_date": max(b["dates"]),
            "site": max(set(b["sites"]), key=b["sites"].count),
            "samples": len(b["prices"]),
        }
    return out


async def sync_market_prices(db: AsyncSession, names: list[str] | None = None) -> dict[str, Any]:
    """采集并追加写入 feed_prices。返回 {updated, missed, samples, elapsed_ms}。"""
    t0 = datetime.now(timezone.utc)
    targets = names or sorted(BOUNDS)
    raws = await collect_raw_text(targets)
    if not any(raws):
        return {"updated": [], "missed": targets, "error": "检索阶段全部失败", "elapsed_ms": _ms(t0)}
    if all("NONE" in t and len(t.strip()) < 40 for t in raws):
        logger.info("行情检索全部空手（NONE）：%s", [t[:60] for t in raws])

    corpus = "\n".join(raws)
    logger.info("行情检索原文（%s 字）：\n%s", len(corpus), corpus[:1500])
    quotes = parse_lines(corpus)
    parser = "line"
    if not quotes:  # 模型没按行格式抄 → 兜底走一次结构化抽取
        parser = "llm"
        try:
            quotes = await extract_quotes(corpus, targets)
        except llm.LLMError as e:
            logger.warning("行情兜底抽取失败：%s", e)

    verified = verify(quotes, raws)
    updated: list[dict[str, Any]] = []
    for name in targets:
        info = verified.get(name)
        if info is None:
            continue
        latest = (
            await db.execute(
                select(FeedPrice).where(FeedPrice.ingredient_name == name).order_by(FeedPrice.price_date.desc(), FeedPrice.price_id.desc())
            )
        ).scalars().first()
        if latest is not None and latest.price_date >= info["price_date"] and float(latest.unit_price) == info["unit_price"]:
            continue  # 同日同价不重复追加
        db.add(
            FeedPrice(
                ingredient_name=name,
                unit_price=info["unit_price"],
                price_date=info["price_date"],
                is_builtin=False,
                seed_caliber="",
                price_source="llm_search",
                source_site=info["site"][:64],
                confidence="high" if info["samples"] >= 2 else "medium",
            )
        )
        updated.append({
            "ingredient_name": name, "unit_price": info["unit_price"],
            "price_date": info["price_date"].isoformat(), "site": info["site"], "samples": info["samples"],
        })
    await db.commit()
    missed = [n for n in targets if n not in verified]
    return {"updated": updated, "missed": missed, "parser": parser, "elapsed_ms": _ms(t0)}


def _ms(then: datetime) -> int:
    return int((datetime.now(timezone.utc) - then).total_seconds() * 1000)


# ===== 进程内单飞状态（行情采集一次只跑一个，接口即时返回不阻塞请求） =====

_STATE: dict[str, Any] = {"running": False, "last": None}
_LOCK = asyncio.Lock()


def status() -> dict[str, Any]:
    return {"running": _STATE["running"], "last": _STATE["last"]}


async def run_once(names: list[str] | None = None) -> dict[str, Any]:
    """带单飞保护的采集入口（自开 session，供后台任务/定时任务调用）。

    检索阶段实测 30~190s，不能挂在 HTTP 请求上：路由只负责点火并即时返回。
    """
    if _STATE["running"]:
        return {"started": False, "reason": "already_running", **status()}
    async with _LOCK:
        _STATE["running"] = True
    try:
        async with SessionLocal() as db:
            result = await sync_market_prices(db, names)
        _STATE["last"] = {**result, "finished_at": datetime.now(timezone.utc).isoformat()}
        logger.info("行情采集完成：写入 %s 条，缺 %s", len(result["updated"]), result["missed"])
        return {"started": True, **result}
    except Exception as e:  # noqa: BLE001 — 后台任务异常不得冒泡到事件循环
        _STATE["last"] = {"error": f"{type(e).__name__}: {e}", "finished_at": datetime.now(timezone.utc).isoformat()}
        logger.exception("行情采集异常")
        return {"started": True, "error": str(e)}
    finally:
        _STATE["running"] = False
