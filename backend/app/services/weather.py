"""F08 气象域 — 高德 REST + PostgreSQL 缓存 + climate_risk 规则引擎 + 告警生成。

三条与 mock 不同的实现选择（都是为了"真后端"而非"搬浏览器代码"）：
1. **缓存落 PostgreSQL**（weather_cache 表，主键 adcode）而不是 Redis：缓存要跨进程重启存活、
   并按 adcode 天然共享给同区养殖户，这两条关系型表给得更稳——Redis 只承接 core/statestore
   那类短生命周期状态（去重/窗口/限流）。
2. **没有"同步分发器 + 异步预取"的 handoff**：mock 因为分发器是同步函数才需要
   primeWeatherLive + lastFetchOutcome 那套接力；FastAPI 里就地 await 取数，直接省掉。
3. **不做模拟兜底**：未配 AMAP key 且无缓存 → 50003 明确不可用（EX-02），
   而不是编一份假天气。演示要假数据请配 key 或走 mock 层。

降级三态：live（刚取到）→ cache（6h 内新鲜 / 超时降级带 degraded）→ 50003。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rules import (
    RISK_COPY,
    WARNING_LEVEL_TO_RISK,
    WARNING_TYPE_TO_RISK,
    climate_risk,
)
from app.core.config import settings
from app.models import Farm, Pen, WeatherCache
from app.services import alerts

CACHE_TTL = timedelta(hours=6)
AMAP_TIMEOUT = 5.0
AMAP_FORECAST_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
SPECIES_NAME = {"pig": "猪", "sheep": "羊", "cattle": "牛"}
LEVEL_RANK = {"low": 0, "mid": 1, "high": 2}

# 降水无概率字段时的文案估算：**表序即匹配序**（雷阵雨 70 必须排在 中雨 80 之前）
PRECIP_BY_TEXT: list[tuple[str, int, float]] = [
    ("特大暴雨", 99, 140.0), ("大暴雨", 98, 90.0), ("暴雨", 95, 60.0), ("大雨", 90, 35.0),
    ("雷阵雨", 70, 15.0), ("中雨", 80, 18.0), ("小雨", 70, 5.0), ("阵雨", 60, 8.0),
    ("雨夹雪", 60, 6.0), ("暴雪", 90, 18.0), ("大雪", 80, 10.0), ("中雪", 70, 6.0),
    ("小雪", 60, 3.0),
]


class WeatherUnavailable(Exception):
    """上游取数失败（网络/鉴权/结构异常）。调用方按 EX-02 降级或报 50003。"""


def species_name(code: str | None) -> str:
    return SPECIES_NAME.get(code or "pig", "猪")


def precip_by_text(text: str) -> tuple[int, float]:
    for pattern, prob, mm in PRECIP_BY_TEXT:
        if pattern in (text or ""):
            return prob, mm
    if "雨" in (text or "") or "雪" in (text or ""):
        return 60, 5.0
    return 0, 0.0


def wind_level_from_power(power: str | None) -> int:
    """'1-3' → 3（取上限）；纯数字直接用；异常兜 3。"""
    m = re.search(r"(\d+)\s*-\s*(\d+)", power or "")
    if m:
        return int(m.group(2))
    try:
        v = int(str(power).strip())
        return v if v > 0 else 3
    except (TypeError, ValueError):
        return 3


# ===== 上游取数 =====

async def fetch_live_from_amap(adcode: str) -> dict[str, Any]:
    if not settings.amap_weather_key:
        raise WeatherUnavailable("AMAP_WEATHER_KEY 未配置")
    try:
        async with httpx.AsyncClient(timeout=AMAP_TIMEOUT) as client:
            resp = await client.get(
                AMAP_FORECAST_URL,
                params={"city": adcode, "key": settings.amap_weather_key, "extensions": "all"},
            )
    except httpx.HTTPError as e:
        raise WeatherUnavailable(f"气象服务网络异常：{type(e).__name__}") from e
    if resp.status_code != 200:
        raise WeatherUnavailable(f"amap http {resp.status_code}")
    try:
        raw = resp.json()
    except ValueError as e:
        raise WeatherUnavailable("amap 响应非 JSON") from e
    if str(raw.get("status")) != "1":
        raise WeatherUnavailable(f"amap status={raw.get('status')} info={raw.get('info', '?')}")
    forecasts = raw.get("forecasts") or []
    hit = next((f for f in forecasts if str(f.get("adcode")) == str(adcode)), forecasts[0] if forecasts else None)
    casts = (hit or {}).get("casts") or []
    if not casts:
        raise WeatherUnavailable("amap empty casts")
    forecast = []
    for c in casts[:3]:  # 硬上限 3 天
        prob, mm = precip_by_text(c.get("dayweather", ""))
        forecast.append(
            {
                "date": str(c.get("date", "")),
                "temp_min": float(c.get("nighttemp") or 0),
                "temp_max": float(c.get("daytemp") or 0),
                "text_day": c.get("dayweather") or "",
                "precip_prob": prob,
                "precip_mm": mm,
                "wind_level": wind_level_from_power(c.get("daypower")),
            }
        )
    # 高德无官方预警接口（FR-07 V1.1），warnings 恒空——预警透传分支只有接 QWeather 才有数据
    return {"forecast": forecast, "warnings": [], "fetched_at": datetime.now(timezone.utc).isoformat()}


# ===== 缓存（PostgreSQL，TTL 6h）=====

async def load_cache(db: AsyncSession, adcode: str) -> dict[str, Any] | None:
    row = await db.get(WeatherCache, adcode)
    if row is None or not (row.payload or {}).get("forecast"):
        return None
    return row.payload


async def save_cache(db: AsyncSession, adcode: str, payload: dict[str, Any]) -> None:
    row = await db.get(WeatherCache, adcode)
    fetched = datetime.fromisoformat(payload.get("fetched_at") or datetime.now(timezone.utc).isoformat())
    if row is None:
        db.add(WeatherCache(adcode=adcode, payload=payload, fetched_at=fetched, source="live"))
    else:
        row.payload = payload
        row.fetched_at = fetched
        row.source = "live"
    await db.commit()


def is_fresh(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    try:
        fetched = datetime.fromisoformat(payload.get("fetched_at") or "")
    except (TypeError, ValueError):
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched < CACHE_TTL


async def resolve_weather_for_farm(db: AsyncSession, farm: Farm) -> dict[str, Any]:
    """返回 {ok, source, degraded, payload} 或 {ok:False, reason: no_adcode|unavailable}。"""
    adcode = (farm.adcode or "").strip()
    if not adcode:
        return {"ok": False, "reason": "no_adcode"}
    cached = await load_cache(db, adcode)
    if is_fresh(cached):  # 6h 内新鲜 → 零外部调用
        return {"ok": True, "source": "cache", "degraded": False, "payload": cached}
    if not settings.amap_weather_key:
        return {"ok": False, "reason": "unavailable"} if cached is None else {
            "ok": True, "source": "cache", "degraded": True, "payload": cached
        }
    try:
        payload = await fetch_live_from_amap(adcode)
        await save_cache(db, adcode, payload)
        return {"ok": True, "source": "live", "degraded": False, "payload": payload}
    except WeatherUnavailable:
        if cached:  # EX-02：取数失败退回上次缓存并标注时间
            return {"ok": True, "source": "cache", "degraded": True, "payload": cached}
        return {"ok": False, "reason": "unavailable"}


async def today_max_temp_for_pen(db: AsyncSession, farm: Farm | None) -> float | None:
    """喂食高温下调取数入口：**只读缓存，绝不触发外部调用**（F06 FR-05 口径）。"""
    if farm is None or not (farm.adcode or "").strip():
        return None
    payload = await load_cache(db, farm.adcode)
    if not payload:
        return None
    today = datetime.now(timezone.utc).date().isoformat()
    for d in payload.get("forecast") or []:
        if d.get("date") == today:
            return float(d.get("temp_max") or 0)
    fc = payload.get("forecast") or []
    return float(fc[0].get("temp_max") or 0) if fc else None


# ===== 规则引擎 =====

def _match_risks(rule: dict[str, Any] | None, forecast: list[dict[str, Any]], warnings: list[dict[str, Any]], sp_name: str) -> list[dict[str, Any]]:
    """规则命中 + 官方预警 → 按 risk_type 合并取高，同级带官方标题者胜出。"""
    merged: dict[str, dict[str, Any]] = {}

    def upsert(risk_type: str, level: str, warning_title: str | None, base_title: str | None = None) -> None:
        cur = merged.get(risk_type)
        if cur is None or LEVEL_RANK[level] > LEVEL_RANK[cur["level"]] or (
            cur.get("warning_title") is None and warning_title is not None
        ):
            copy = RISK_COPY.get(risk_type) or {}
            merged[risk_type] = {
                "risk_type": risk_type,
                "level": level,
                "title": base_title or f"{copy.get('label', risk_type)}风险",
                "impact": copy.get("impact", "气象部门已发布预警，请注意防范"),
                "advice": list(copy.get("advice", ["关注当地气象部门发布的预警信息与防范指引"])),
                "warning_title": warning_title if warning_title is not None else (cur or {}).get("warning_title"),
            }

    if rule:
        checks = (
            ("heat_stress", lambda d: float(d["temp_max"]) > float(rule["heat_stress"]["temp_gt"]), rule.get("heat_stress")),
            ("rainstorm", lambda d: float(d["precip_mm"]) > float(rule["rainstorm"]["precip_mm_gt"]), rule.get("rainstorm")),
            ("frost", lambda d: float(d["temp_min"]) < float(rule["frost"]["temp_lt"]), rule.get("frost")),
            ("gale", lambda d: float(d["wind_level"]) >= float(rule["gale"]["wind_level_gte"]), rule.get("gale")),
        )
        for risk_type, predicate, sub in checks:
            if not sub:
                continue
            hits = sum(1 for d in forecast if predicate(d))
            if hits:  # ≥3 命中日 → high；1~2 天 → mid（预报上限就是 3 天）
                upsert(risk_type, "high" if hits >= 3 else "mid", None)

    for w in warnings or []:
        text = f"{w.get('level', '')}{w.get('title', '')}"
        risk_type = next((rt for pat, rt in WARNING_TYPE_TO_RISK if re.search(pat, str(w.get("type", "")) + text)), None)
        level = WARNING_LEVEL_TO_RISK.get(str(w.get("level", "")).lower(), "low")
        if risk_type:
            upsert(risk_type, level, w.get("title"))  # 同因合并（EX-W3 取高）
        else:  # 未映射的官方预警原样透传，标题不加物种前缀
            merged[f"warn:{w.get('type')}"] = {
                "risk_type": str(w.get("type") or "气象"),
                "level": level,
                "title": w.get("title") or f"{w.get('type')}预警",
                "impact": "气象部门已发布预警，请注意防范",
                "advice": ["关注当地气象部门发布的预警信息与防范指引"],
                "warning_title": w.get("title"),
            }

    out = sorted(merged.values(), key=lambda x: -LEVEL_RANK[x["level"]])
    for item in out:  # 规则命中的加物种前缀，官方预警标题保持原文
        if item.get("warning_title") is None:
            item["title"] = f"{sp_name}·{item['title']}"
    return out


def _data_hint(risk_type: str, payload: dict[str, Any], rule: dict[str, Any] | None) -> str:
    fc = payload.get("forecast") or []
    if not fc:
        return ""
    if risk_type == "heat_stress":
        return f"预报最高 {max(float(d['temp_max']) for d in fc):g}℃（阈值 {(rule or {}).get('heat_stress', {}).get('temp_gt', 35)}℃）"
    if risk_type == "rainstorm":
        return f"24h 降水估算 {max(float(d['precip_mm']) for d in fc):g}mm（阈值 {(rule or {}).get('rainstorm', {}).get('precip_mm_gt', 50)}mm）"
    if risk_type == "frost":
        return f"预报最低 {min(float(d['temp_min']) for d in fc):g}℃（阈值 {(rule or {}).get('frost', {}).get('temp_lt', 2)}℃）"
    if risk_type == "gale":
        return f"风力 {max(int(d['wind_level']) for d in fc)} 级（阈值 {(rule or {}).get('gale', {}).get('wind_level_gte', 6)} 级）"
    return f"{fc[0].get('text_day', '')} {fc[0]['temp_min']:g}~{fc[0]['temp_max']:g}℃"


async def generate_climate_alerts(db: AsyncSession, farm: Farm, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """圈级判定（V1.4 由场级改回圈级）：返回 items 供响应聚合，同时按 1h 窗口落告警。

    注意 items 不受合并窗口影响（每次都全量返回当前风险），窗口只 gate 落库告警——
    否则缓存刷新后看板会显示不出风险。
    """
    pens = (
        await db.execute(select(Pen).where(Pen.farm_id == farm.farm_id, Pen.is_deleted.is_(False)))
    ).scalars().all()
    forecast = payload.get("forecast") or []
    warnings = payload.get("warnings") or []
    items: list[dict[str, Any]] = []
    created = False
    for pen in pens:
        code = pen.species_code or "pig"
        rule = climate_risk(code)
        for m in _match_risks(rule, forecast, warnings, species_name(code)):
            hint = _data_hint(m["risk_type"], payload, rule)
            items.append({**m, "pen_id": pen.pen_id, "pen_name": pen.pen_name, "species": species_name(code), "data_hint": hint})
            key = f"climate:{pen.pen_id}:{m['risk_type']}:{m['level']}"  # 带 level 才能让 mid→high 升级重出
            if await alerts.in_merge_window(key):
                continue
            title = m.get("warning_title") or m["title"]
            content = (
                f"{pen.pen_name} {m['warning_title']}：气象部门已发布预警，{m['impact']}"
                if m.get("warning_title")
                else f"{pen.pen_name} {m['title']}：{m['impact']}（{hint}）"
            )
            await alerts.create_alert(db, pen, "climate", m["level"], content, {"risk_type": m["risk_type"], "title": title})
            created = True
    if created:
        await db.commit()
    return items


def aggregate_risk(items: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """响应里的 risk 汇总：按 risk_type 去重取高，标题/影响/建议取最高那条。"""
    if not items:
        return {"level": "none"}
    by_type: dict[str, dict[str, Any]] = {}
    for it in items:
        cur = by_type.get(it["risk_type"])
        if cur is None or LEVEL_RANK[it["level"]] > LEVEL_RANK[cur["level"]]:
            by_type[it["risk_type"]] = it
    ordered = sorted(by_type.values(), key=lambda x: -LEVEL_RANK[x["level"]])
    primary = ordered[0]
    return {
        "level": primary["level"],
        "species": primary["species"],
        "risk_type": primary["risk_type"],
        "title": primary.get("warning_title") or primary["title"],
        "impact": primary["impact"],
        "advice": primary["advice"],
        "data_hint": primary["data_hint"],
        "items": [
            {
                "risk_type": x["risk_type"],
                "level": x["level"],
                "title": x.get("warning_title") or x["title"],
                "pen_name": x["pen_name"],
                "data_hint": x["data_hint"],
            }
            for x in ordered
        ],
    }


async def primary_farm(db: AsyncSession, user_id: int) -> Farm | None:
    return (
        await db.execute(
            select(Farm).where(Farm.user_id == user_id, Farm.is_deleted.is_(False)).order_by(Farm.farm_id)
        )
    ).scalars().first()


def cache_age_hours(payload: dict[str, Any]) -> int:
    try:
        fetched = datetime.fromisoformat(payload.get("fetched_at") or "")
    except (TypeError, ValueError):
        return 1
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return max(1, round((datetime.now(timezone.utc) - fetched).total_seconds() / 3600))


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()
