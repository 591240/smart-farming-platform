"""F11 数据查询 Agent — 四把只读工具查真库（03-API 9.1 data_query 分支 / F11 PRD）。

三条不可商量的纪律（附录 A 约束 5 的工程保证）：
1. **回答里的数字 100% 来自查询结果**——工具返回什么就说什么，模型不参与数值生成；
2. **查不到就明说**（empty / no_sensor / no_alert / no_review 四种 reason），禁止编造；
3. **白名单封口**：DATA_QUERY_TOOLS 之外没有实现函数，写操作物理不可达。

圈舍两级匹配（F11 V1.2）：先匹配本人圈舍，再匹配全场——命中别人的圈舍走 `denied` 话术
（不报 40302、不中断对话，SSE done 带 denied:true）；提到圈舍但全场都没有走 `penNotFound`。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func as fn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rules import METRIC_META, env_thresholds
from app.models import Alert, Farm, Pen, StockChange, User, VetReview
from app.services import feed_engine as fe

DATA_QUERY_TOOLS = ("stock_summary", "sensor_digest", "alert_digest", "review_status")

WINDOW_DEFAULT = 7
WINDOW_MAX = 90
CN_NUM = {"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
STAGE_LABEL = {
    "nursing": "哺乳期", "weaning": "保育期", "growing": "育成期", "fattening": "育肥期",
    "gestation": "妊娠期", "lactation": "泌乳期",
}
ALERT_TYPE_LABEL = {"sensor": "传感器", "climate": "气象", "vision": "视觉", "disease": "疫病", "device_offline": "设备离线"}
REVIEW_STATUS_LABEL = {"pending": "待复核", "approved": "已确认", "rejected": "已驳回修改", "timeout": "已超时降级"}
NO_FARM_TEXT = "您还没有创建养殖场，请先到【场舍管理】完成建场后再来查询。"


def _cn_to_num(text: str) -> str:
    return re.sub(r"[一二两三四五六七八九十]", lambda m: CN_NUM.get(m.group(0), m.group(0)), text)


async def _my_pens(db: AsyncSession, user: User) -> list[Pen]:
    farm_ids = (await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id, Farm.is_deleted.is_(False)))).scalars().all()
    if not farm_ids:
        return []
    return list((await db.execute(select(Pen).where(Pen.farm_id.in_(farm_ids), Pen.is_deleted.is_(False)))).scalars().all())


def _pick_tool(message: str) -> str:
    # 复核语义优先（问诊词表含单字"病"，会误吞"病例复核了吗"）
    if "复核" in message:
        return "review_status"
    if re.search(r"告警|报警", message):
        return "alert_digest"
    if re.search(r"温度|湿度|氨气|曲线", message):
        return "sensor_digest"
    return "stock_summary"


def _pick_metric(message: str) -> str:
    if "湿度" in message:
        return "humidity"
    if "氨气" in message:
        return "nh3"
    if re.search(r"CO2|二氧化碳", message, re.I):
        return "co2"
    return "temperature"


def _pick_window(message: str) -> int:
    m = re.search(r"最近\s*(\d{1,2})\s*天", message)
    if m:
        return max(1, min(WINDOW_MAX, int(m.group(1))))
    if re.search(r"今天|今日", message):
        return 1
    return WINDOW_DEFAULT


async def extract_slots(db: AsyncSession, user: User, message: str) -> dict[str, Any]:
    """两级圈舍匹配：本人圈舍 → 全场（命中他人即 denied）。"""
    text = _cn_to_num(message)
    own = await _my_pens(db, user)
    slots: dict[str, Any] = {
        "tool": _pick_tool(text), "metric": _pick_metric(text),
        "window_days": _pick_window(text), "pen_id": None, "denied": False, "pen_not_found": False,
    }
    # 与问诊侧 _resolve_pen 用同一条宽松规则（"N号" 子串匹配），否则同一句话
    # 在问诊能定位圈舍、在数据查询却定位不到——两套口径必然让用户困惑
    num = re.search(r"(\d{1,3})\s*号", text)
    hit = next((p for p in own if num and f"{num.group(1)}号" in p.pen_name), None)
    if hit is None:
        hit = next((p for p in own if len(p.pen_name) >= 2 and p.pen_name in text), None)
    if hit is not None:
        slots["pen_id"] = hit.pen_id
        return slots

    if num:  # 本人没匹配上 → 去全场找，找到别人的就是越权
        all_pens = (await db.execute(select(Pen).where(Pen.is_deleted.is_(False)))).scalars().all()
        other = next((p for p in all_pens if f"{num.group(1)}号" in p.pen_name), None) or next(
            (p for p in all_pens if len(p.pen_name) >= 2 and p.pen_name in text), None
        )
        if other is not None:
            slots["denied"] = True
            return slots
    if re.search(r"号圈|个圈|圈舍", text):
        slots["pen_not_found"] = True
    return slots


async def _pen_name(db: AsyncSession, pen_id: int) -> str:
    pen = await db.get(Pen, pen_id)
    return pen.pen_name if pen else "全场"


async def run_tool(db: AsyncSession, user: User, slots: dict[str, Any]) -> dict[str, Any]:
    tool = slots["tool"]
    if tool == "stock_summary":
        return await _stock(db, user, slots)
    if tool == "sensor_digest":
        return await _sensor(db, user, slots)
    if tool == "alert_digest":
        return await _alerts(db, user, slots)
    return await _reviews(db, user, slots)


async def _stock(db: AsyncSession, user: User, slots: dict[str, Any]) -> dict[str, Any]:
    pens = await _my_pens(db, user)
    if not pens:
        return {"ok": False, "reason": "empty"}
    if slots.get("pen_id"):
        pen = next((p for p in pens if p.pen_id == slots["pen_id"]), None)
        if pen is None:
            return {"ok": False, "reason": "empty"}
        return {
            "ok": True, "scope": "pen", "pen_name": pen.pen_name, "total": pen.male_count + pen.female_count,
            "male": pen.male_count, "female": pen.female_count, "stage": STAGE_LABEL.get(pen.batch_stage or "", pen.batch_stage),
        }
    total_male = sum(p.male_count for p in pens)
    total_female = sum(p.female_count for p in pens)
    # scope 必须如实标 all_farms：这里是**账号名下全部农场**的合计，而数字大屏按场选择器只算
    # 当前一个场。此前该分支返回 scope="farm"、话术写"您全场"，于是同一个"存栏"在两处
    # 显示两个数字且都自称全场口径（F12 FR-03 第 3 条验收项卡在这里）
    return {
        "ok": True, "scope": "all_farms", "total": total_male + total_female,
        "male": total_male, "female": total_female, "pen_count": len(pens),
        "farm_count": len({p.farm_id for p in pens}),
    }


async def _sensor(db: AsyncSession, user: User, slots: dict[str, Any]) -> dict[str, Any]:
    from app.models import Device, SensorReading

    if not slots.get("pen_id"):
        return {"ok": False, "reason": "need_pen"}
    pen = await db.get(Pen, slots["pen_id"])
    if pen is None:
        return {"ok": False, "reason": "need_pen"}
    since = int((datetime.now(timezone.utc) - timedelta(days=slots["window_days"])).timestamp() * 1000)
    rows = (
        await db.execute(
            select(SensorReading.ts, SensorReading.value)
            .where(SensorReading.pen_id == pen.pen_id, SensorReading.metric == slots["metric"], SensorReading.ts >= since)
            .order_by(SensorReading.ts)
        )
    ).all()
    if not rows:
        return {"ok": False, "reason": "no_sensor", "pen_name": pen.pen_name, "window_days": slots["window_days"]}
    # 阈值按**本圈物种**取（mock 版写死猪，切真必须跟着圈舍走）
    th = env_thresholds(pen.species_code).get(slots["metric"], {})
    values = [fe.to_float(v) for _, v in rows]
    peak = max(values)
    peak_ts = max(ts for ts, v in rows if fe.to_float(v) == peak)
    lo, hi = th.get("min"), th.get("max")
    exceeds = sum(1 for v in values if (hi is not None and v > hi) or (lo is not None and v < lo))
    meta = METRIC_META.get(slots["metric"], {"label": slots["metric"], "unit": ""})
    return {
        "ok": True, "pen_name": pen.pen_name, "metric": meta["label"], "unit": meta["unit"],
        "latest": values[-1], "avg": round(sum(values) / len(values), 1), "peak": peak,
        "peak_date": datetime.fromtimestamp(peak_ts / 1000, tz=timezone.utc).astimezone().date().isoformat(),
        "exceeds": exceeds, "sample_count": len(values), "window_days": slots["window_days"],
    }


async def _alerts(db: AsyncSession, user: User, slots: dict[str, Any]) -> dict[str, Any]:
    farm_ids = (await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id, Farm.is_deleted.is_(False)))).scalars().all()
    if not farm_ids:
        return {"ok": False, "reason": "empty"}
    since = datetime.now(timezone.utc) - timedelta(days=slots["window_days"])
    q = select(Alert, Pen.pen_name).join(Pen, Pen.pen_id == Alert.pen_id, isouter=True).where(
        Alert.farm_id.in_(farm_ids), Alert.create_time >= since
    )
    if slots.get("pen_id"):
        q = q.where(Alert.pen_id == slots["pen_id"])
    rows = (await db.execute(q.order_by(Alert.create_time.desc()))).all()
    if not rows:
        return {"ok": False, "reason": "no_alert", "window_days": slots["window_days"]}
    recent = [
        {
            "type": ALERT_TYPE_LABEL.get(a.alert_type, a.alert_type), "level": a.risk_level,
            "pen_name": pen_name or "全场",  # 查询已按本人 farm 过滤；无圈舍的外场告警显示"全场"
            "content": (a.alert_content or "")[:40],
            "date": a.create_time.astimezone().strftime("%m-%d %H:%M"),
        }
        for a, pen_name in rows[:3]
    ]
    return {
        "ok": True, "total": len(rows), "unhandled": sum(1 for a, _ in rows if a.status == "pending"),
        "window_days": slots["window_days"], "recent": recent,
    }


async def _reviews(db: AsyncSession, user: User, slots: dict[str, Any]) -> dict[str, Any]:
    farm_ids = (await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id, Farm.is_deleted.is_(False)))).scalars().all()
    if not farm_ids:
        return {"ok": False, "reason": "empty"}
    since = datetime.now(timezone.utc) - timedelta(days=slots["window_days"])
    rows = (
        await db.execute(
            select(VetReview).where(VetReview.farm_id.in_(farm_ids), VetReview.create_time >= since).order_by(VetReview.create_time.desc())
        )
    ).scalars().all()
    if not rows:
        return {"ok": False, "reason": "no_review", "window_days": slots["window_days"]}
    now = datetime.now(timezone.utc)
    by = {k: sum(1 for r in rows if r.review_status == k) for k in ("pending", "approved", "rejected", "timeout")}
    recent = []
    for r in rows[:3]:
        item = {
            "status": r.review_status, "status_label": REVIEW_STATUS_LABEL.get(r.review_status, r.review_status),
            "original_diagnosis": (r.original_diagnosis or "")[:40],
        }
        if r.review_status == "pending" and r.create_time:
            item["waiting_hours"] = round((now - r.create_time).total_seconds() / 3600 * 10) / 10
        recent.append(item)
    return {"ok": True, "total": len(rows), "by_status": by, "window_days": slots["window_days"], "recent": recent}


def build_answer(tool: str, res: dict[str, Any], slots: dict[str, Any]) -> tuple[str, str, str]:
    """返回 (正文, section 标题, 溯源出处)。数值全部来自 res，不掺任何生成内容。"""
    if not res.get("ok"):
        reason = res.get("reason")
        if reason == "empty":
            return NO_FARM_TEXT, "数据查询", "场内数据"
        if reason == "no_sensor":
            label = METRIC_META.get(slots["metric"], {}).get("label", "传感器")
            return (
                f"「{res.get('pen_name', '该圈舍')}」最近 {slots['window_days']} 天暂无{label}数据。"
                f"请确认已添加{label}设备并保持在线（可在【设备管理】检查）。",
                "数据查询", "sensor_readings",
            )
        if reason == "no_alert":
            return f"最近 {slots['window_days']} 天没有告警记录，圈舍环境正常。", "告警查询", "alert_log"
        if reason == "no_review":
            return f"最近 {slots['window_days']} 天没有进行中的高危病例复核。", "复核状态", "vet_review"
        return "没有查到相关数据。", "数据查询", "场内数据"

    if tool == "stock_summary":
        if res["scope"] == "pen":
            stage = f"，处于{res['stage']}阶段" if res.get("stage") else ""
            return (
                f"{res['pen_name']}当前存栏 {res['total']} 头（公 {res['male']} / 母 {res['female']}）{stage}",
                "存栏汇总", "存栏快照（pen 公母数，stock_change 流水驱动口径）",
            )
        return (
            f"您名下 {res['farm_count']} 个农场合计存栏 {res['total']} 头（公 {res['male']} / 母 {res['female']}），"
            f"分布在 {res['pen_count']} 个圈舍；数字大屏显示的是当前所选农场的数字，两者口径不同。",
            "存栏汇总", "存栏快照（pen 公母数，stock_change 流水驱动口径，全部农场合计）",
        )
    if tool == "sensor_digest":
        return (
            f"{res['pen_name']}最近 {res['window_days']} 天{res['metric']}：最新 {res['latest']}{res['unit']}"
            f" / 均值 {res['avg']}{res['unit']} / 峰值 {res['peak']}{res['unit']}（{res['peak_date']}），"
            f"超阈值 {res['exceeds']} 次（样本 {res['sample_count']} 条）。",
            "传感器摘要", f"传感器历史数据（近 {res['window_days']} 天实际读数，阈值取 F03 环境配置）",
        )
    if tool == "alert_digest":
        lines = "；".join(
            f"{x['date']} {x['pen_name']} {x['type']}告警（{x['level']}）：{x['content']}" for x in res["recent"]
        )
        return (
            f"最近 {res['window_days']} 天共 {res['total']} 条告警（未处理 {res['unhandled']} 条）。最近一条：{lines}。",
            "告警查询", "告警中心同源数据（alert_log）",
        )
    bs = res["by_status"]
    lines = "；".join(
        f"{x['status_label']}" + (f"（已等待 {x['waiting_hours']} 小时）" if x.get("waiting_hours") is not None else "")
        + f"：{x['original_diagnosis']}"
        for x in res["recent"]
    )
    return (
        f"最近 {res['window_days']} 天共 {res['total']} 条高危病例复核记录"
        f"（待复核 {bs['pending']} / 已确认 {bs['approved']} / 已驳回 {bs['rejected']} / 已超时降级 {bs['timeout']}）。"
        f"最近：{lines}。",
        "复核状态", "兽医复核记录（vet_review）",
    )
