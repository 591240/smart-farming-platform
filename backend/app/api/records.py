"""F04 台账查询与导出（03-API 10.1/10.2）。

append-only：不提供 PUT/DELETE（TC-R04 兜底 40401 由路由不存在天然满足）。
导出契约：{ filename, mime, content }，CSV + UTF-8 BOM（Excel/WPS 直接打开不乱码）。
"""

import csv
import io
from datetime import datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.response import E_FORBIDDEN, E_VALIDATION, ApiError, ok
from app.core.security import get_current_user, require_farmer
from app.models import Farm, LedgerRecord, Pen, User, VetReview

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user), Depends(require_farmer)])


async def _farm_ids(db: AsyncSession, user: User) -> list[int]:
    rows = await db.execute(select(Farm.farm_id).where(Farm.user_id == user.user_id))
    return list(rows.scalars())


async def _scoped_farm_ids(db: AsyncSession, user: User, farm_id: int | None) -> list[int]:
    """租户全部农场，或按「养殖场」筛选参数收敛到单场（F04 FR-01）。

    逻辑删除的场不剔除：删场保留台账是档案口径（02-数据库），筛选只影响列表范围。
    越权农场直接 40302——此前后端签名里没有 farm_id，FastAPI 静默丢弃该参数，
    筛选框点了没反应、导出恒含全部场。
    """
    ids = await _farm_ids(db, user)
    if farm_id is None:
        return ids
    if farm_id not in ids:
        raise ApiError(E_FORBIDDEN, "无权限访问该养殖场的台账")
    return [farm_id]


def _csv_escape(value: str) -> str:
    if any(ch in value for ch in (",", '"', "\n", "\r")):
        return '"' + value.replace('"', '""') + '"'
    return value


def _parse_date(raw: str, field: str) -> datetime:
    """筛选日期解析：非法输入给 42201，而不是让 ValueError 冒成 50001。"""
    try:
        return datetime.fromisoformat(raw)
    except ValueError as e:
        raise ApiError(E_VALIDATION, f"{field} 不是合法日期（YYYY-MM-DD）", {"field": field}) from e


RECORD_TYPE_LABEL = {
    "stock_change": "存栏变动",
    "sensor": "传感器告警",
    "device_offline": "设备离线",
    "climate": "气象告警",
    "vision": "视觉告警",
    "disease": "疫病告警",
    "medicine": "用药",
    "feed": "饲喂",
    "vet_review": "兽医复核",
    "lifecycle": "生命周期",
    "slaughter": "出栏",
    "individual": "个体档案",
}

# record_ext 动态列的中文列名（F04 决策 1 / 03-API 10.2）。
# 前端表头消费 {field,label}，导出表头用 label；未登记的键按首次出现顺序追加并原样展示。
EXT_FIELD_LABELS = {
    "male_delta": "公变动",
    "female_delta": "母变动",
    "tag_no": "耳标号",
    "individual_id": "个体编号",
    "event_type": "事件类型",
    "event_date": "事件日期",
    "due_date": "预产期",
    "live_count": "活仔数",
    "dead_count": "死胎数",
    "parity": "胎次",
    "handle_action": "处理动作",
    "med_name": "用药名称",
    "usage_date": "用药日期",
    "head_count": "出栏头数",
    "male_count": "公畜数",
    "female_count": "母畜数",
    "feed_type": "饲喂类型",
    "planned_kg": "计划投料量",
    "as_planned": "按计划执行",
    "cost_per_head_day": "头日均成本",
    "formula_id": "配方编号",
    "created_male": "新增公畜",
    "created_female": "新增母畜",
    "withdrawal_days": "休药期天数",
    "demo": "演示数据",
    # 出栏/强制出栏留痕（record_ext 实际写入的键，浏览器实测漏了四个裸英文列头）
    "force_note": "强制出栏说明",
    "destination": "去向",
    "avg_weight_kg": "均重(kg)",
    "withdrawal_check": "休药期校验",
    "head_avg_weight_kg": "均重(kg)",
    # AI 问诊/用药与兽医复核留痕（03-API 9.1/10.1：这些键真会进动态列，未登记就在台账上裸显英文）
    "disease_name": "疑似病症",
    "risk_level": "风险等级",
    "diagnosis_mode": "诊断方式",
    "llm_model": "生成模型",
    "vision_signs": "图片征象",
    "intent": "识别意图",
    "source": "依据来源",
    "session_id": "AI 会话",
    "thread_id": "工作流线程",
    "med_suggest": "建议用药",
    "max_withdrawal_days": "最长休药期(天)",
    "withdrawal_period": "休药期(天)",
    "withdrawal_until": "休药期至",
    "review_id": "复核编号",
    "review_action": "复核动作",
    "review_opinion": "复核意见",
    "review_cost_hours": "复核耗时(小时)",
    "original_diagnosis": "AI 初诊",
    "revised_diagnosis": "修正诊断",
    "revised_med": "修正用药",
    "vet_user_id": "复核兽医",
}


def _collect_ext_columns(rows: list[LedgerRecord]) -> list[dict[str, str]]:
    """动态扩展列：先按 EXT_FIELD_LABELS 定义序，再追加未定义键的首次出现序（mock 同兜底规则）。"""
    seen: set[str] = set()
    appeared: list[str] = []
    for r in rows:
        for key in (r.record_ext or {}):
            if key not in seen:
                seen.add(key)
                appeared.append(key)
    ordered = [k for k in EXT_FIELD_LABELS if k in seen] + [k for k in appeared if k not in EXT_FIELD_LABELS]
    return [{"field": k, "label": EXT_FIELD_LABELS.get(k, k)} for k in ordered]


async def _review_statuses(db: AsyncSession, rows: list[LedgerRecord]) -> dict[int, str]:
    """台账 → 关联复核单状态。

    F10 决策 2：「未经兽医复核」是**读侧派生**——复核超时不回写原 disease 台账（append-only），
    也不新增记录，只在展示与导出时按 vet_review_id 关联出的 timeout 状态叠加标注。
    """
    ids = sorted({r.vet_review_id for r in rows if r.vet_review_id})
    if not ids:
        return {}
    found = await db.execute(select(VetReview.review_id, VetReview.review_status).where(VetReview.review_id.in_(ids)))
    return {rid: status for rid, status in found.all()}


@router.get("/record/list")
async def list_records(
    record_type: str = Query(default=""),
    farm_id: int | None = Query(default=None),
    pen_id: int | None = Query(default=None),
    start_date: str = Query(default="", alias="from"),
    end_date: str = Query(default="", alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    farm_ids = await _scoped_farm_ids(db, user, farm_id)
    q = select(LedgerRecord).where(LedgerRecord.farm_id.in_(farm_ids))
    if record_type:
        q = q.where(LedgerRecord.record_type == record_type)
    if pen_id is not None:
        q = q.where(LedgerRecord.pen_id == pen_id)
    if start_date:
        q = q.where(LedgerRecord.create_time >= _parse_date(start_date, "from"))
    if end_date:
        # 含端日：to 日期整天
        q = q.where(LedgerRecord.create_time < _parse_date(end_date, "to") + timedelta(days=1))
    q = q.order_by(LedgerRecord.create_time.desc(), LedgerRecord.record_id.desc())

    from sqlalchemy import func as fn

    total = (await db.execute(select(fn.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    # 只查本页出现的圈舍名：此前是 select(Pen) 全表，把**所有租户**的圈舍读进内存做字典查表
    pen_ids = {r.pen_id for r in rows}
    pens = (
        {p.pen_id: p.pen_name for p in (await db.execute(select(Pen).where(Pen.pen_id.in_(pen_ids)))).scalars()}
        if pen_ids
        else {}
    )

    ext_columns = _collect_ext_columns(list(rows))

    statuses = await _review_statuses(db, list(rows))
    items = [
        {
            "record_id": r.record_id,
            "record_type": r.record_type,
            "summary": r.summary,
            "create_time": r.create_time.isoformat(),
            "pen_name": pens.get(r.pen_id, ""),
            "record_ext": r.record_ext or {},
            **(
                {"vet_review_id": r.vet_review_id, "vet_review_status": statuses[r.vet_review_id]}
                if r.vet_review_id and r.vet_review_id in statuses
                else {}
            ),
        }
        for r in rows
    ]
    return ok({"total": total, "page": page, "page_size": page_size, "ext_columns": ext_columns, "items": items})


@router.get("/record/export")
async def export_records(
    record_type: str = Query(default=""),
    farm_id: int | None = Query(default=None),
    pen_id: int | None = Query(default=None),
    start_date: str = Query(default="", alias="from"),
    end_date: str = Query(default="", alias="to"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    farm_ids = await _scoped_farm_ids(db, user, farm_id)
    q = select(LedgerRecord).where(LedgerRecord.farm_id.in_(farm_ids))
    if record_type:
        q = q.where(LedgerRecord.record_type == record_type)
    if pen_id is not None:
        q = q.where(LedgerRecord.pen_id == pen_id)
    if start_date:
        q = q.where(LedgerRecord.create_time >= datetime.fromisoformat(start_date))
    if end_date:
        q = q.where(LedgerRecord.create_time < datetime.fromisoformat(end_date) + timedelta(days=1))
    q = q.order_by(LedgerRecord.create_time.desc(), LedgerRecord.record_id.desc())
    rows = (await db.execute(q.limit(5000))).scalars().all()
    pens = {p.pen_id: p.pen_name for p in (await db.execute(select(Pen))).scalars()}

    ext_columns = _collect_ext_columns(list(rows))

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM
    # 通用列顺序按 PRD 3.6 与 mock 导出对齐：时间/圈舍/类型/摘要 + 动态扩展列
    header = ["时间", "圈舍", "类型", "摘要", *[c["label"] for c in ext_columns]]
    buf.write(",".join(_csv_escape(h) for h in header) + "\r\n")
    statuses = await _review_statuses(db, list(rows))
    for r in rows:
        ext = r.record_ext or {}
        summary = r.summary
        if r.vet_review_id and statuses.get(r.vet_review_id) == "timeout":
            summary = f"{summary}（未经兽医复核）"  # F10 决策 2 读侧派生
        cells = [
            r.create_time.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            pens.get(r.pen_id, ""),
            RECORD_TYPE_LABEL.get(r.record_type, r.record_type),
            summary,
            *[str(ext.get(c["field"], "")) for c in ext_columns],
        ]
        buf.write(",".join(_csv_escape(c) for c in cells) + "\r\n")

    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    return ok({"filename": f"ledger-{now}.csv", "mime": "text/csv", "content": buf.getvalue()})
