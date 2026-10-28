"""F03 设备接入与环境告警闭环（03-API 7/8 节；ingest 引擎与 mock engineIngest 同规则）。

管道：msg_id 去重(2h TTL) → topic 归属校验 → metrics 白名单 → 落 sensor_readings +
心跳/在线状态 → 阈值判定（防抖连续 3 次、1h 合并窗口、双档分级）→ 告警落库落台账。
停用设备：数据照常入库、心跳照常刷新，仅跳过告警判定（F03 PRD FR-02）。
"""

import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func as fn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.farm import resolve_pen
from app.api.rules import (
    DEDUP_TTL_MS,
    DEBOUNCE_COUNT,
    DEBOUNCE_TTL_MS,
    HISTORY_MAX_POINTS,
    HISTORY_SCAN_CAP,
    METRIC_META,
    OFFLINE_AFTER_MS,
    VALID_METRICS,
    env_thresholds,
)
from app.core import statestore
from app.core.db import get_db
from app.core.ratelimit import ingest_rate_limit
from app.core.response import E_FORBIDDEN, E_INVALID_TOKEN, E_NOT_FOUND, E_VALIDATION, ApiError, ok
from app.core.security import (
    get_current_user,
    issue_device_secret,
    optional_current_user,
    require_farmer,
    verify_device_secret,
)
from app.models import Alert, Device, Farm, LedgerRecord, Pen, SensorReading, User
from app.services.alerts import create_alert, in_merge_window

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])

# C-24：设备上行单独一条 router——它不能挂农户 JWT 的 router 级依赖（ESP32 拿不到农户 token），
# 改为路由内部自行做"JWT 或 设备密钥"双轨鉴权，且两条轨都必须证明设备归属。
ingest_router = APIRouter(prefix="/api")

# 展示口径：告警时间在前端/导出按东八区自然日呈现，服务端日期筛选必须用同一时区，
# 否则「今日新增」会把昨天 08:00 之后的告警算进今天（大屏客户端 localDateKey 同口径）。
CST = timezone(timedelta(hours=8))

TOPIC_PATTERN = re.compile(r"^farm/(\d+)/pen/(\d+)/([A-Za-z0-9-]+)/data$")
SN_PATTERN = re.compile(r"^[A-Za-z0-9-]{2,64}$")

# 引擎态（去重 dedup:{sn}:{msg_id} / 防抖 deb:{sn}:{metric} / 心跳 hb:{sn}）落在 core/statestore：
# 配了 Redis 就全进程共享，没配退回进程内存。回收交给键的 TTL，不再有「超 2000 条淘汰最旧」
# 那种只属于进程内实现的护栏。


def _now_ms() -> int:
    return int(time.time() * 1000)


async def resolve_device(db: AsyncSession, user: User, device_id: int) -> tuple[Device, Farm, Pen]:
    device = await db.get(Device, device_id)
    if device is None or device.is_deleted:
        raise ApiError(E_NOT_FOUND, "该设备不存在或已删除")
    farm = await db.get(Farm, device.farm_id)
    if farm is None or farm.is_deleted or farm.user_id != user.user_id:
        raise ApiError(E_FORBIDDEN, "无权限访问该设备")
    pen = await db.get(Pen, device.pen_id)
    if pen is None:
        raise ApiError(E_NOT_FOUND, "该设备绑定的圈舍不存在")
    return device, farm, pen


# ---------- FR-01 设备注册 / 列表 / 编辑 / 删除 ----------

class DeviceBody(BaseModel):
    device_name: str = Field(default="")
    device_type: str = Field(default="")
    device_sn: str = Field(default="")
    pen_id: int
    rtsp_url: str = Field(default="")


@router.post("/device")
async def create_device(body: DeviceBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    device_name = body.device_name.strip()
    if not device_name:
        raise ApiError(E_VALIDATION, "请填写设备名称", {"field": "device_name"})
    if body.device_type not in ("sensor", "camera"):
        raise ApiError(E_VALIDATION, "无效的设备类型", {"field": "device_type"})
    sn = body.device_sn.strip()
    if not SN_PATTERN.fullmatch(sn):
        raise ApiError(E_VALIDATION, "设备编号需为 2~64 位字母/数字/连字符", {"field": "device_sn"})
    exists = (await db.execute(select(Device).where(Device.device_sn == sn))).scalar_one_or_none()
    if exists:
        raise ApiError(E_VALIDATION, "该设备编号已被使用", {"field": "device_sn"})  # EX-C9 全局唯一
    pen, _ = await resolve_pen(db, user, body.pen_id)
    rtsp = body.rtsp_url.strip()
    if body.device_type == "camera" and not rtsp:
        raise ApiError(E_VALIDATION, "请填写摄像头 RTSP 地址", {"field": "rtsp_url"})

    secret_raw, secret_hash = issue_device_secret()
    device = Device(
        farm_id=pen.farm_id, pen_id=pen.pen_id, device_type=body.device_type, device_name=device_name,
        # 2026-10-23：浏览器造数引擎下线后，平台里不再有"模拟设备"这种实体——
        # 新建的一律是待接入的真实设备（无数据时显示离线，等硬件上报）。
        device_sn=sn, source="real", device_secret_hash=secret_hash,
        mqtt_topic=f"farm/{pen.farm_id}/pen/{pen.pen_id}/{sn}/data",
        rtsp_url=rtsp, online_status="offline", last_heartbeat=None, is_enable=True,
    )
    db.add(device)
    await db.commit()
    # device_secret 只在这里回传一次（库里只有 sha256）：贴到设备上即可走
    # `POST /api/device/ingest` + `X-Device-Key`，无需农户 JWT；丢了就轮换。
    return ok({
        "device_id": device.device_id, "mqtt_topic": device.mqtt_topic, "rtsp_url": rtsp or None,
        "device_secret": secret_raw,
    })


@router.post("/device/{device_id}/rotate-secret")
async def rotate_device_secret(
    device_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """重签发设备密钥（旧值立即失效）。同样只回传一次明文。"""
    device, _farm, _pen = await resolve_device(db, user, device_id)
    secret_raw, secret_hash = issue_device_secret()
    device.device_secret_hash = secret_hash
    await db.commit()
    return ok({"device_id": device.device_id, "device_secret": secret_raw})


@router.get("/device/list")
async def list_devices(farm_id: int | None = Query(default=None), user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = await db.execute(
        select(Device, Pen)
        .join(Farm, Farm.farm_id == Device.farm_id)
        .join(Pen, Pen.pen_id == Device.pen_id, isouter=True)
        .where(Device.is_deleted == False, Farm.user_id == user.user_id, Farm.is_deleted == False)  # noqa: E712
        .order_by(Device.device_id)
    )
    items = []
    for d, pen in rows.all():
        if farm_id and d.farm_id != farm_id:
            continue
        items.append({
            "device_id": d.device_id,
            "device_name": d.device_name,
            "device_type": d.device_type,
            "device_sn": d.device_sn,
            "source": d.source,
            "farm_id": d.farm_id,
            "pen_id": d.pen_id,
            "pen_name": pen.pen_name if pen else "",
            "mqtt_topic": d.mqtt_topic,
            "rtsp_url": d.rtsp_url,
            "online_status": d.online_status,
            "last_heartbeat": d.last_heartbeat.isoformat() if d.last_heartbeat else None,
            "is_enable": d.is_enable,
            "install_time": d.install_time.isoformat(),
        })
    return ok({"items": items})


class DeviceUpdateBody(BaseModel):
    device_name: str | None = None
    is_enable: bool | None = None
    rtsp_url: str | None = None


@router.put("/device/{device_id}")
async def update_device(device_id: int, body: DeviceUpdateBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    device, _, _ = await resolve_device(db, user, device_id)
    if body.device_name is not None:
        if not body.device_name.strip():
            raise ApiError(E_VALIDATION, "请填写设备名称", {"field": "device_name"})
        device.device_name = body.device_name.strip()
    if body.is_enable is not None:
        device.is_enable = body.is_enable
    if body.rtsp_url is not None:
        device.rtsp_url = body.rtsp_url.strip()
    await db.commit()
    return ok({"device_id": device.device_id, "updated": True})


@router.delete("/device/{device_id}")
async def delete_device(device_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    device, _, _ = await resolve_device(db, user, device_id)
    device.is_deleted = True  # 历史采集数据与告警保留（主 PRD 3.3.1）
    await statestore.drop(f"hb:{device.device_sn}")
    await db.commit()
    return ok({"device_id": device.device_id, "deleted": True})


# ---------- FR-03/04 数据上行引擎（07 协议 data 帧）----------

class IngestBody(BaseModel):
    msg_id: str = Field(default="")
    device_id: str = Field(default="")
    topic: str = Field(default="")
    ts: float | None = None
    metrics: dict[str, float] | None = None


async def offline_scan(db: AsyncSession, farm_ids: list[int] | None = None) -> None:
    """FR-05 离线判定扫描（等价 mq/consumer.py 心跳扫描任务）。

    `farm_ids=None` 只允许系统维护任务使用（全量扫描）；任何由农户 HTTP 请求触发的
    扫描必须传自己的农场列表——否则一次 `/alert/list` 就会跨租户改设备状态并生成别人的告警。
    """
    now = _now_ms()
    q = select(Device, Pen).join(Pen, Pen.pen_id == Device.pen_id).where(
        Device.is_deleted == False,  # noqa: E712
        Device.is_enable == True,  # noqa: E712
        Device.device_type == "sensor",
        Device.online_status == "online",
    )
    if farm_ids is not None:
        q = q.where(Device.farm_id.in_(farm_ids or [0]))
    rows = await db.execute(q)
    for device, pen in rows.all():
        # 心跳优先取状态层（跨进程实时值），未命中/已过期再回退库里的 last_heartbeat：
        # 两者由同一次上报写入同一个时间戳，所以回退是无损的
        last = await statestore.last_stamp(f"hb:{device.device_sn}") or (
            int(device.last_heartbeat.timestamp() * 1000) if device.last_heartbeat else 0
        )
        if now - last > OFFLINE_AFTER_MS:
            device.online_status = "offline"
            if not await in_merge_window(f"device_offline:{pen.pen_id}"):
                await create_alert(db, pen, "device_offline", "mid", f"{pen.pen_name} 设备「{device.device_name}」超过 {OFFLINE_AFTER_MS // 1000} 秒无心跳，环境数据已断更")


async def ingest_payload(
    db: AsyncSession,
    body: IngestBody,
    *,
    user: User | None = None,
    device_key: str = "",
    trusted: bool = False,
) -> dict[str, Any]:
    """设备上行核心（07 协议）。**HTTP 与 MQTT 共用这一条校验链**（C-24）。

    三条入口，证明的是同一件事——"这条读数确实属于这台设备"：

    - `X-Device-Key`：建设备/轮换时返回的一次性明文，库里只存 sha256。
      真实硬件（ESP32/STM32）拿不到农户 JWT，这条轨就是为它开的。
    - `Authorization: Bearer <农户 JWT>`：浏览器/网关代报，且必须是该设备所在农场的主人。
    - `trusted=True`：MQTT 桥接（app/services/mqtt_bridge.py）专用。连接已由 EMQX 认证，
      但 topic 与设备归属的一致性**仍然逐条校验**，所以伪造到别人 topic 的消息只会
      得到 topic-mismatch，写不进别人圈舍。该参数只在服务内部传，路由层不接受。

    三条都不满足 → 40101。不存在匿名上报。
    """
    # ① msg_id 空值先拒；真正的去重登记放在鉴权之后（见 ③b）——匿名请求带随机 msg_id
    #    不得污染进程内去重表，否则既是无界内存增长，又会吞掉后续同 msg_id 的真实上报
    if not body.msg_id:
        return {"code": 0, "message": "ok", "data": {"ok": False, "reason": "bad-payload"}}

    # ② topic 归属校验（EX-C5）
    m = TOPIC_PATTERN.fullmatch(body.topic or "")
    if not m:
        return {"code": 0, "message": "ok", "data": {"ok": False, "reason": "topic-mismatch"}}
    device = (await db.execute(select(Device).where(Device.device_sn == body.device_id, Device.is_deleted == False))).scalar_one_or_none()  # noqa: E712
    if device is None:
        return {"code": 0, "message": "ok", "data": {"ok": False, "reason": "unknown-device"}}
    if int(m.group(1)) != device.farm_id or int(m.group(2)) != device.pen_id:
        return {"code": 0, "message": "ok", "data": {"ok": False, "reason": "topic-mismatch"}}

    # ②b 身份与归属（C-24 三轨，都必须证明"这条读数属于这台设备"）
    farm = await db.get(Farm, device.farm_id)
    if trusted:
        pass  # MQTT 桥接：连接已认证，topic 归属已在 ② 校验
    elif user is not None:
        if farm is None or farm.user_id != user.user_id:
            return {"code": E_FORBIDDEN, "message": "无权限向该设备上报数据", "data": None}
    else:
        if not verify_device_secret(device_key, device.device_secret_hash):
            return {"code": E_INVALID_TOKEN, "message": "设备凭证缺失或无效", "data": None}

    # ③ metrics 白名单 + 数值合法（EX-C4）
    ts = int(body.ts) if body.ts is not None else 0
    if not body.metrics:
        return {"code": 0, "message": "ok", "data": {"ok": False, "reason": "bad-payload"}}
    clean: list[tuple[str, float]] = []
    for k, v in body.metrics.items():
        if k not in VALID_METRICS or not isinstance(v, (int, float)) or v < 0:
            return {"code": 0, "message": "ok", "data": {"ok": False, "reason": "bad-payload"}}
        clean.append((k, round(float(v), 2)))

    pen = await db.get(Pen, device.pen_id)
    if pen is None:
        return {"code": 0, "message": "ok", "data": {"ok": False, "reason": "unknown-device"}}

    # ③b msg_id 去重（EX-C2：QoS1 重发 / 补报重放）：身份与载荷都验证完才登记，
    #     避免一条本该被拒的请求把 msg_id 占掉、让设备的正确重试被误判为 duplicate。
    #     键按设备隔离——msg_id 由设备自行生成，不同设备撞号不应互相吞消息。
    now = _now_ms()
    # SET NX PX 把「查重 + 登记」并成一个原子动作：并发重发只有一条能占到，且 TTL 到点自动
    # 释放——进程内实现那套「超 2000 条淘汰最旧」的护栏不再需要（键由 Redis 回收）
    if not await statestore.claim(f"dedup:{device.device_sn}:{body.msg_id}", DEDUP_TTL_MS):
        return {"code": 0, "message": "ok", "data": {"ok": False, "reason": "duplicate"}}

    # ④ 写历史 + 心跳/在线状态（离线设备恢复上报即回 online，FR-05）
    # C-09 分工定稿：历史行按**消息 ts**入库（补报要还原采集时刻），心跳按**到达时间**记。
    # 此前心跳也取消息 ts，于是 `seed_story.py` 灌 Day1（2 天前）的历史读数时，设备被离线
    # 扫描秒判 offline 并多出一条 device_offline 告警——"补报"和"掉线"被同一根时间轴混在一起。
    for metric, value in clean:
        db.add(SensorReading(pen_id=pen.pen_id, metric=metric, ts=ts, value=value))
    # TTL 给两倍离线阈值：过期后 offline_scan 自动回退库里的 last_heartbeat，不会误判离线
    await statestore.stamp(f"hb:{device.device_sn}", now, OFFLINE_AFTER_MS * 2)
    device.last_heartbeat = datetime.fromtimestamp(now / 1000, tz=timezone.utc)
    device.online_status = "online"

    # ⑤ 阈值判定：防抖连续 3 次 + 1h 合并窗口 + 双档分级（FR-04）
    thresholds = env_thresholds(pen.species_code)
    for metric, value in clean:
        if not device.is_enable:
            break  # 停用设备：仅入库与心跳，跳过告警判定（F03 PRD FR-02）
        th = thresholds.get(metric)
        if not th:
            continue
        meta = METRIC_META[metric]
        breach_critical = (th.get("criticalMax") is not None and value > th["criticalMax"]) or (th.get("criticalMin") is not None and value < th["criticalMin"])
        breach_warn = value > th.get("max", float("inf")) or value < th.get("min", float("-inf"))
        d_key = f"deb:{device.device_sn}:{metric}"
        if not breach_warn and not breach_critical:
            await statestore.reset_count(d_key, DEBOUNCE_TTL_MS)  # 未超标清零（TC-D02）
            continue
        # 每次超标都把计数 TTL 续上：静默满 DEBOUNCE_TTL 自动归零，与旧实现「读时判过期」同口径
        count = await statestore.bump(d_key, DEBOUNCE_TTL_MS)
        if count < DEBOUNCE_COUNT:
            continue
        if await in_merge_window(f"sensor:{pen.pen_id}"):
            continue  # EX-C7/TC-D07：合并窗口内不再产生
        await statestore.reset_count(d_key, DEBOUNCE_TTL_MS)
        level = "high" if breach_critical else "mid"  # warn=mid / critical=high（TC-D10）
        if breach_critical:
            bound = th["criticalMax"] if (th.get("criticalMax") is not None and value > th["criticalMax"]) else th.get("criticalMin")
            direction = "上限" if (th.get("criticalMax") is not None and value > th["criticalMax"]) else "下限"
        else:
            bound = th["max"] if value > th.get("max", float("-inf")) else th.get("min")
            direction = "上限" if value > th.get("max", float("-inf")) else "下限"
        await create_alert(db, pen, "sensor", level, f"{pen.pen_name} {meta['label']} {value}{meta['unit']}，超过{'严重' if level == 'high' else '预警'}{direction} {bound}{meta['unit']}（连续 3 次超标）")

    await offline_scan(db, [device.farm_id])
    await db.commit()
    return {"code": 0, "message": "ok", "data": {"ok": True}}


@ingest_router.post("/device/ingest")
async def ingest(
    request: Request,
    body: IngestBody,
    _rl: None = Depends(ingest_rate_limit),  # 排在鉴权依赖之前：先挡量，再花查库的代价
    user: User | None = Depends(optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """HTTP 上行入口：真实硬件带 `X-Device-Key`，网关/浏览器带农户 JWT。"""
    return await ingest_payload(
        db, body, user=user, device_key=request.headers.get("x-device-key", "").strip()
    )


# ---------- FR-06 告警列表 / 处理 ----------

@router.get("/alert/list")
async def list_alerts(
    status: str | None = Query(default=None),
    type: str | None = Query(default=None),  # 03-API 8.1 文档参数名即 type（曾误加 alias="alert_type" → 前端类型筛选静默失效）
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    farm_id: int | None = Query(default=None),
    date_from: str = Query(default="", alias="from"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    farm_ids = list((await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id, Farm.is_deleted == False))).scalars().all())  # noqa: E712
    if farm_id is not None:
        if farm_id not in farm_ids:
            raise ApiError(E_FORBIDDEN, "无权限访问该场区的告警")
        farm_ids = [farm_id]
    await offline_scan(db, farm_ids)
    await db.commit()
    q = select(Alert, Pen).join(Pen, Pen.pen_id == Alert.pen_id, isouter=True).where(
        Alert.farm_id.in_(farm_ids or [0]),
    )
    if status:
        q = q.where(Alert.status == status)
    if type:
        q = q.where(Alert.alert_type == type)
    if date_from:
        # 大屏「今日新增」口径：服务端按自然日计数（此前前端只在前 8 条里筛，属抽样）
        try:
            since = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=CST)
        except ValueError:
            raise ApiError(E_VALIDATION, "日期格式需为 yyyy-MM-dd", {"field": "from"}) from None
        q = q.where(Alert.create_time >= since)
    q = q.order_by(Alert.create_time.desc(), Alert.alert_id.desc())
    total = (await db.execute(select(fn.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).all()
    items = [
        {
            "alert_id": a.alert_id,
            "alert_type": a.alert_type,
            "risk_level": a.risk_level,
            "alert_content": a.alert_content,
            "status": a.status,
            "handled_note": a.handled_note,
            "handled_time": a.handled_time.isoformat() if a.handled_time else None,
            "create_time": a.create_time.isoformat(),
            "pen_name": pen.pen_name if pen else "",
        }
        for a, pen in rows
    ]
    return ok({"total": total, "page": page, "page_size": page_size, "items": items})


class AlertActionBody(BaseModel):
    action: str = Field(default="")
    note: str = Field(default="")


@router.post("/alert/{alert_id}/handle")
async def alert_action(alert_id: int, body: AlertActionBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    alert = await db.get(Alert, alert_id)
    if alert is None:
        raise ApiError(E_NOT_FOUND, "该告警不存在")
    farm = await db.get(Farm, alert.farm_id)
    if farm is None or farm.user_id != user.user_id:
        raise ApiError(E_FORBIDDEN, "无权限访问该告警")
    pen = await db.get(Pen, alert.pen_id)
    pen_name = pen.pen_name if pen else ""
    now = datetime.now(timezone.utc)
    if body.action == "acknowledge":
        if alert.status != "pending":
            raise ApiError(E_VALIDATION, "该告警当前状态不可置已读")
        alert.status = "acknowledged"
        await db.commit()
        return ok({"alert_id": alert_id, "status": "acknowledged"})
    if body.action == "resolve":
        if alert.status == "resolved":
            raise ApiError(E_VALIDATION, "该告警已处理")
        if alert.status == "false_alarm":
            raise ApiError(E_VALIDATION, "该告警已判为误报，不可再标记已处理")
        if not body.note.strip():
            raise ApiError(E_VALIDATION, "处理备注必填（审计要求）", {"field": "note"})
        alert.status = "resolved"
        alert.handled_note = body.note.strip()
        alert.handled_time = now
        db.add(LedgerRecord(
            farm_id=alert.farm_id, pen_id=alert.pen_id, record_type=alert.alert_type,
            summary=f"{pen_name} 告警处理：{alert.alert_content} → 已处理（{body.note.strip()}）",
            stock_change_id=0, alert_id=alert.alert_id, record_ext={"handle_action": "resolve"},
        ))
        await db.commit()
        return ok({"alert_id": alert_id, "status": "resolved"})
    if body.action == "false_alarm":
        if alert.status in ("resolved", "false_alarm"):
            raise ApiError(E_VALIDATION, "该告警已处理，不可改判误报" if alert.status == "resolved" else "该告警已判为误报")
        # 与 resolve 同口径：误报也必须有理由并落台账，否则"这条为什么没人处理"事后无从追溯
        if not body.note.strip():
            raise ApiError(E_VALIDATION, "误报理由必填（审计要求）", {"field": "note"})
        alert.status = "false_alarm"
        alert.handled_note = body.note.strip()
        alert.handled_time = now
        db.add(LedgerRecord(
            farm_id=alert.farm_id, pen_id=alert.pen_id, record_type=alert.alert_type,
            summary=f"{pen_name} 告警判定误报：{alert.alert_content}（{body.note.strip()}）",
            stock_change_id=0, alert_id=alert.alert_id, record_ext={"handle_action": "false_alarm"},
        ))
        await db.commit()
        return ok({"alert_id": alert_id, "status": "false_alarm"})
    raise ApiError(E_VALIDATION, "无效的处理动作", {"field": "action"})


# ---------- FR-07 传感器历史（抽稀返回）----------

@router.get("/sensor/history")
async def sensor_history(
    pen_id: int = Query(...),
    metric: str = Query(default="temperature"),
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if metric not in METRIC_META:
        raise ApiError(E_VALIDATION, "无效的指标", {"field": "metric"})
    await resolve_pen(db, user, pen_id)
    from_ts = int(datetime.fromisoformat(start).timestamp() * 1000) if start else 0
    to_ts = int(datetime.fromisoformat(end).timestamp() * 1000) + (86_399_000 if end and len(end) == 10 else 0) if end else 1 << 62
    # 扫描上限：此前把区间内**全部**行载入 Python 再抽稀，选一年就是几十万行内存。
    # 现在按 ts 倒序取最近 HISTORY_SCAN_CAP 条、再正序抽稀——内存有界；超上限时曲线只覆盖
    # 最近这一段，并用 truncated 如实告诉前端，而不是悄悄画一条"看起来是全年"的线。
    rows = list(
        (
            await db.execute(
                select(SensorReading)
                .where(SensorReading.pen_id == pen_id, SensorReading.metric == metric, SensorReading.ts >= from_ts, SensorReading.ts <= to_ts)
                .order_by(SensorReading.ts.desc())
                .limit(HISTORY_SCAN_CAP)
            )
        ).scalars().all()
    )
    rows.reverse()
    stride = max(1, len(rows) // HISTORY_MAX_POINTS)
    points = [{"time": datetime.fromtimestamp(r.ts / 1000, tz=timezone.utc).isoformat(), "value": r.value} for i, r in enumerate(rows) if i % stride == 0]
    return ok({"metric": metric, "points": points, "truncated": len(rows) >= HISTORY_SCAN_CAP})
