"""F09 视觉巡查（07-协议第四节 + 03-API 7.3）— 事件入库管道、列表、演示注入。

不模拟视频流（F09 决策 4）：本域只有**识别事件**，预览卡与时间线都读这里；
RTSP→mediamtx 那条链是硬件侧的事（docs/08 C-01 还缺 WebRTC 8189/udp 映射）。

与 mock 逐条对齐的关键口径：

- **只存事实不存结论**：表里没有 level 列，`level`/`event_label` 在读侧由
  `event_type + confidence` 现算（`confidence_level`），改分级规则不用回刷历史；
- **单通道分级告警**：death≥0.5 → high，≥0.7 → mid，其余 low；`<0.5` 在入库前就丢弃
  （不入库不告警，只计数）；合并键 `vision:{pen}:{type}:{level}` 带 level，
  所以 mid→high 升级能重新出告警；
- **停用设备仍入库**（事实数据要留），只跳过告警判定；
- **death 不自动扣存栏**（F09 决策 3）：high 告警引导人工核实后去"调整存栏"登记；
- msg_id 去重 TTL 2h 与合并窗口都在 `core/statestore`（接了 Redis 跨进程共享，没配退回进程内存）。

`vision_drop_stats` 是观测值、无 HTTP 路由（与 mock 一致），需要看就加只读端点，
不要为了看板好看把它落库。
"""

from __future__ import annotations

import math
import random
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.farm import resolve_pen
from app.api.rules import MERGE_WINDOW_MS
from app.core import statestore
from app.core.db import get_db
from app.core.response import E_FORBIDDEN, E_NOT_FOUND, E_VALIDATION, ApiError, ok
from app.core.security import get_current_user, require_farmer
from app.models import CameraAiResult, Device, Farm, Pen, User
from app.services.alerts import create_alert, in_merge_window, now_ms

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])

VISION_EVENT_TYPES = ("huddle", "water", "cough", "lethargy", "death")
VISION_EVENT_META: dict[str, dict[str, str]] = {
    "huddle": {"label": "扎堆", "impact": "密度过高或应激信号", "advice": "建议通风降温，排查应激源并适当分群"},
    "water": {"label": "圈舍积水", "impact": "圈舍潮湿，湿度与蹄病风险上升", "advice": "建议检修排水，及时清理积水并更换垫料"},
    "cough": {"label": "咳嗽", "impact": "呼吸道疾病前兆信号", "advice": "建议尽早 AI 问诊排查（问诊跳转链接 V1.1）"},
    "lethargy": {"label": "精神萎靡", "impact": "精神状态异常，健康风险提示", "advice": "建议加强巡查，必要时 AI 问诊排查"},
    "death": {
        "label": "死亡牲畜（疑似）",
        "impact": "发现疑似死亡个体",
        "advice": "请立即人工核实，确认后到「场舍管理 → 调整存栏」登记死淘（系统不自动扣减存栏）",
    },
}
VISION_DISCLAIMER = "视觉识别结果仅作为提示，不作为确诊依据"

EVENT_TOPIC_RE = re.compile(r"^farm/(\d+)/pen/(\d+)/([A-Za-z0-9-]+)/event$")
DEDUP_TTL_MS = 2 * 60 * 60 * 1000

# msg_id 去重在 core/statestore（键 vdedup:{msg_id}，TTL 2h），跨进程共享
_drop_stats = {
    "totalIngested": 0, "dropped": 0, "lowConfidence": 0,
    "dupMsgId": 0, "badDevice": 0, "badPayload": 0, "badEventType": 0,
}


def drop_stats() -> dict[str, int]:
    return dict(_drop_stats)


def reset_vision_state() -> None:
    """测试用：清空丢弃计数。去重与合并窗口已上移到状态层，按 key 前缀清理，不在这里做。"""
    for k in _drop_stats:
        _drop_stats[k] = 0


def confidence_level(event_type: str, confidence: float) -> str:
    """F09 决策 1 + V1.2 边界：death≥0.5→high；≥0.7→mid；否则 low（<0.5 早已丢弃）。"""
    if event_type == "death" and confidence >= 0.5:
        return "high"
    if confidence >= 0.7:
        return "mid"
    return "low"


def _count_drop(reason: str) -> None:
    _drop_stats["dropped"] += 1
    bucket = {
        "low_confidence": "lowConfidence", "dup_msg_id": "dupMsgId", "bad_device": "badDevice",
        "bad_payload": "badPayload", "bad_event_type": "badEventType",
    }.get(reason)
    if bucket:
        _drop_stats[bucket] += 1


async def _msg_id_seen(msg_id: str) -> bool:
    """QoS1 重发幂等：2h 内同 msg_id 判重复（状态层原子占位，等价 mqtt:dedup:{msg_id}）。"""
    return not await statestore.claim(f"vdedup:{msg_id}", DEDUP_TTL_MS)


async def ingest_vision_event(db: AsyncSession, raw: dict[str, Any]) -> dict[str, Any]:
    """视觉事件消费链（07-协议第六节同款管道哲学）：schema → 去重 → 回表 → 枚举 →
    低置信丢弃 → 入库 → 分级告警。返回 `{ok, dropped?, reason?, result?}`，
    丢弃是**引擎观测而非 HTTP 错误**（EX-V1~V5），调用方按原样透给前端。
    """
    _drop_stats["totalIngested"] += 1
    msg_id = raw.get("msg_id")
    device_sn = raw.get("device_id")
    ts = raw.get("ts")
    event_type = raw.get("event_type")
    confidence = raw.get("confidence")

    def dropped(reason: str) -> dict[str, Any]:
        _count_drop(reason)
        return {"ok": False, "dropped": True, "reason": reason}

    if not isinstance(msg_id, str) or not msg_id:
        return dropped("bad_payload")
    if not isinstance(device_sn, str) or not device_sn:
        return dropped("bad_payload")
    try:
        ts_ms = float(ts)
    except (TypeError, ValueError):
        return dropped("bad_payload")
    if not math.isfinite(ts_ms) or ts_ms <= 0:
        return dropped("bad_payload")
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        return dropped("bad_payload")
    if not math.isfinite(conf) or conf < 0 or conf > 1:
        return dropped("bad_payload")
    if not isinstance(event_type, str) or not event_type:
        return dropped("bad_payload")

    if await _msg_id_seen(msg_id):
        return dropped("dup_msg_id")

    device = (
        await db.execute(select(Device).where(Device.device_sn == device_sn, Device.is_deleted == False))  # noqa: E712
    ).scalars().first()
    if device is None or device.device_type != "camera":
        return dropped("bad_device")
    m = EVENT_TOPIC_RE.match(str(raw.get("topic") or ""))
    if not m or int(m[1]) != device.farm_id or int(m[2]) != device.pen_id:
        return dropped("bad_payload")

    if event_type not in VISION_EVENT_TYPES:
        return dropped("bad_event_type")
    if conf < 0.5:
        return dropped("low_confidence")

    pen = await db.get(Pen, device.pen_id)
    if pen is None:
        return dropped("bad_device")

    row = CameraAiResult(
        device_id=device.device_id,
        pen_id=pen.pen_id,
        collect_time=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
        vision_json=dict(raw),
    )
    db.add(row)
    await db.flush()

    level = confidence_level(event_type, conf)
    meta = VISION_EVENT_META[event_type]
    sim_tag = "（模拟）" if (raw.get("extra") or {}).get("sim") is True else ""
    conf_pct = round(conf * 100)  # 展示与比较都先取整（断言纪律：别让 0.86999 显示成 87%）
    if event_type == "death":
        content = f"{pen.pen_name} 视觉识别{sim_tag}：{meta['label']}（置信度 {conf_pct}%）——{meta['advice']}"
    else:
        content = f"{pen.pen_name} 视觉识别{sim_tag}：{meta['label']}（置信度 {conf_pct}%）——{meta['impact']}，{meta['advice']}"

    alert_id: int | None = None
    if device.is_enable and not await in_merge_window(f"vision:{pen.pen_id}:{event_type}:{level}", MERGE_WINDOW_MS):
        alert = await create_alert(db, pen, "vision", level, content)
        alert_id = alert.alert_id
    await db.commit()
    return {"ok": True, "result": {"result_id": row.result_id, "level": level, "alert_id": alert_id}}


def _iso(v: datetime) -> str:
    return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if v.tzinfo else v.isoformat()


def _day_bound(value: str, end: bool = False) -> datetime | None:
    """日期串（yyyy-MM-dd）按"含当日"展开；带时间的串原样解析（03-API 7.2 同款口径）。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            d = date.fromisoformat(text)
            base = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
            return base + timedelta(days=1) - timedelta(milliseconds=1) if end else base
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@router.get("/vision/events")
async def list_vision_events(
    pen_id: int | None = Query(default=None),
    from_: str = Query(default="", alias="from"),
    to: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    farm_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """事件时间线（FR-03）。缺省 pen_id = 本人名下全部圈；bbox 不返回（V1.0 不展示）。"""
    if pen_id is not None:
        pen, _farm = await resolve_pen(db, user, pen_id)  # 越权 40302 / 不存在 40401（EX-V6）
        pen_ids = [pen.pen_id]
    else:
        farm_ids = list(
            (
                await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id, Farm.is_deleted == False))  # noqa: E712
            ).scalars()
        )
        if farm_id is not None:
            if farm_id not in farm_ids:
                raise ApiError(E_FORBIDDEN, "无权限访问该场区的视觉事件")
            farm_ids = [farm_id]
        pen_ids = list(
            (
                await db.execute(select(Pen.pen_id).where(Pen.farm_id.in_(farm_ids or [0]), Pen.is_deleted == False))  # noqa: E712
            ).scalars()
        )

    q: Select = select(CameraAiResult, Pen, Device).join(Pen, Pen.pen_id == CameraAiResult.pen_id, isouter=True).join(
        Device, Device.device_id == CameraAiResult.device_id, isouter=True
    ).where(CameraAiResult.pen_id.in_(pen_ids or [0]))
    lo, hi = _day_bound(from_), _day_bound(to, end=True)
    if lo:
        q = q.where(CameraAiResult.collect_time >= lo)
    if hi:
        q = q.where(CameraAiResult.collect_time <= hi)
    q = q.order_by(CameraAiResult.collect_time.desc(), CameraAiResult.result_id.desc())
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).all()

    items = []
    for row, pen, device in rows:
        payload = row.vision_json or {}
        event_type = str(payload.get("event_type") or "")
        conf = float(payload.get("confidence") or 0)
        items.append({
            "result_id": row.result_id,
            "event_type": event_type,
            "event_label": VISION_EVENT_META.get(event_type, {}).get("label") or event_type,
            "confidence": conf,
            "level": confidence_level(event_type, conf),
            "collect_time": _iso(row.collect_time),
            "pen_name": pen.pen_name if pen else "",
            "device_sn": device.device_sn if device else "",
        })
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


class VisionSimulateBody(BaseModel):
    device_id: int
    event_type: str = Field(default="")
    confidence: float = Field(default=0.8)
    anomaly: str = Field(default="none")  # none | low_confidence | duplicate | bad_payload


@router.post("/vision/simulate")
async def vision_simulate(
    body: VisionSimulateBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """演示注入（FR-04）：走 FR-01 同一条校验链，**不是旁路直写**。

    anomaly 演练三种丢弃路径（低置信 EX-V1 / 同 msg_id 重发 EX-V2 / 缺字段 EX-V3），
    让"引擎会丢数据"这件事在演示里可见，而不是只在文档里存在。
    """
    device = await db.get(Device, body.device_id)
    if device is None or device.is_deleted:
        raise ApiError(E_NOT_FOUND, "设备不存在")
    farm_owner = (await db.execute(select(Farm.user_id).where(Farm.farm_id == device.farm_id))).scalars().first()
    if farm_owner != user.user_id:
        raise ApiError(E_FORBIDDEN, "仅可向本人名下设备注入事件")
    if device.device_type != "camera":
        raise ApiError(E_VALIDATION, "仅摄像头设备支持视觉事件注入", {"field": "device_id"})

    msg_id = f"{random.Random().getrandbits(32):08x}"
    conf = float(body.confidence)

    def payload() -> dict[str, Any]:
        data: dict[str, Any] = {
            "msg_id": msg_id,
            "device_id": device.device_sn,
            "topic": f"farm/{device.farm_id}/pen/{device.pen_id}/{device.device_sn}/event",
            "ts": now_ms(),
            "confidence": min(conf, 0.45) if body.anomaly == "low_confidence" else conf,
            "bbox": [120, 88, 460, 380],
            "extra": {"sim": True, "animal_count_est": 6},
        }
        if body.anomaly != "bad_payload":
            data["event_type"] = body.event_type
        return data

    first = await ingest_vision_event(db, payload())
    second: dict[str, Any] | None = None
    if body.anomaly == "duplicate":
        second = await ingest_vision_event(db, payload())

    result: dict[str, Any] = dict(first)
    if second is not None:
        result["dedup"] = {"msg_id_replay_dropped": second.get("dropped") is True and second.get("reason") == "dup_msg_id"}
    if first.get("ok"):
        level = (first.get("result") or {}).get("level")
        has_alert = bool((first.get("result") or {}).get("alert_id"))
        result["message"] = f"已入库并生成 {level} 级提示" + ("（含告警）" if has_alert else "（1h 合并窗口内同因告警不重复）")
    elif body.anomaly == "low_confidence":
        result["message"] = "低置信演练：confidence <0.5 已丢弃（不入库不告警，丢弃计数 +1）"
    elif body.anomaly == "bad_payload":
        result["message"] = "bad_payload 演练：缺字段已跳过记日志（消费者未崩溃）"
    elif first.get("dropped"):
        result["message"] = "无效的事件类型" if first.get("reason") == "bad_event_type" else "注入被引擎丢弃"
    return ok(result)
