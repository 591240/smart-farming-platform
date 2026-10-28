"""ORM 模型 — 02-数据库设计（切真主链路：账户/场舍存栏/流水/台账/个体耳标/繁殖/用药
/设备告警/会话复核/喂食中心/气象缓存）。

快照纪律：pens.male_count/female_count 只由 stock_change 流水驱动（02-数据库口径表）。
台账纪律：ledger_records append-only，不提供修改/删除（TC-R04）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(16), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    nickname: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="farmer")  # farmer/vet/admin
    main_species_code: Mapped[str | None] = mapped_column(String(32), default=None)
    is_enable: Mapped[bool] = mapped_column(Boolean, default=True)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    phone: Mapped[str] = mapped_column(String(16), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    lock_until: Mapped[int] = mapped_column(BigInteger, default=0)  # epoch ms


class Farm(Base):
    __tablename__ = "farms"

    farm_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), index=True)
    farm_name: Mapped[str] = mapped_column(String(64))
    province: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(32), default="")
    county: Mapped[str] = mapped_column(String(32), default="")
    adcode: Mapped[str] = mapped_column(String(16), default="")
    address: Mapped[str] = mapped_column(String(128), default="")
    contact_person: Mapped[str] = mapped_column(String(32), default="")
    phone: Mapped[str] = mapped_column(String(16), default="")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pens: Mapped[list[Pen]] = relationship(back_populates="farm")


class Pen(Base):
    __tablename__ = "pens"

    pen_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.farm_id"), index=True)
    pen_name: Mapped[str] = mapped_column(String(64))
    species_code: Mapped[str] = mapped_column(String(32), default="pig")
    variety_code: Mapped[str | None] = mapped_column(String(32), default=None)
    male_count: Mapped[int] = mapped_column(Integer, default=0)
    female_count: Mapped[int] = mapped_column(Integer, default=0)
    batch_stage: Mapped[str | None] = mapped_column(String(16), default=None)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    farm: Mapped[Farm] = relationship(back_populates="pens")


class StockChange(Base):
    __tablename__ = "stock_changes"

    change_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    change_type: Mapped[str] = mapped_column(String(16))  # birth/death/sale/purchase/manual/transfer_out/transfer_in
    male_delta: Mapped[int] = mapped_column(Integer)
    female_delta: Mapped[int] = mapped_column(Integer)
    related_change_id: Mapped[int | None] = mapped_column(Integer, default=None)  # 转群两笔互指
    remark: Mapped[str] = mapped_column(String(128), default="")
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LedgerRecord(Base):
    """台账（F04）：append-only，不提供修改/删除。record_ext 为 JSONB 扩展列。"""

    __tablename__ = "ledger_records"

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.farm_id"), index=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    record_type: Mapped[str] = mapped_column(String(32), index=True)  # stock_change/individual/lifecycle/…
    summary: Mapped[str] = mapped_column(Text)
    stock_change_id: Mapped[int] = mapped_column(Integer, default=0)  # F04 FR-03 溯源外键
    alert_id: Mapped[int] = mapped_column(Integer, default=0)
    record_ext: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    lifecycle_event_id: Mapped[int] = mapped_column(Integer, default=0)  # F04 FR-03 溯源外键
    medicine_usage_id: Mapped[int] = mapped_column(Integer, default=0)
    vet_review_id: Mapped[int] = mapped_column(Integer, default=0)  # F10 溯源外键（02-数据库 3.6 口径）
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AnimalIndividual(Base):
    """F14 个体耳标档案。强关联对账：在栏个体数 == 圈舍存栏快照。"""

    __tablename__ = "animal_individuals"
    __table_args__ = (
        # 场内唯一（含全部状态个体——耳标是终身身份）；纠错删档（is_deleted）不占号
        Index(
            "uq_individual_farm_tag",
            "farm_id",
            "ear_tag_no",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("idx_individual_pen", "pen_id", "status"),
    )

    individual_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.farm_id"), index=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    ear_tag_no: Mapped[str] = mapped_column(String(32))
    gender: Mapped[str] = mapped_column(String(8))  # male/female
    variety_code: Mapped[str | None] = mapped_column(String(32), default=None)
    birth_date: Mapped[date | None] = mapped_column(Date, default=None)
    source: Mapped[str] = mapped_column(String(32), default="人工建档")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/sold/dead/removed
    exit_note: Mapped[str] = mapped_column(String(128), default="")
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    remark: Mapped[str] = mapped_column(String(128), default="")
    traits: Mapped[str] = mapped_column(String(128), default="")   # F17：外貌/毛色特征
    lineage: Mapped[str] = mapped_column(String(128), default="")  # F17：血缘备注（父系/母系）
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IndividualEvent(Base):
    """F17 个体档案事件（一畜一档时间线；U-LITS 事件寄存器模型）。

    流水联动事件（register/rename/transfer/exit）只增不删；
    手动事件（weight/immunization/treatment/note）可纠错删除。
    """

    __tablename__ = "individual_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    individual_id: Mapped[int] = mapped_column(ForeignKey("animal_individuals.individual_id"), index=True)
    event_type: Mapped[str] = mapped_column(String(16))  # register/rename/transfer/exit/weight/immunization/treatment/note
    event_date: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ===== F03 设备 / 告警 / 传感器 =====

class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.farm_id"), index=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    device_type: Mapped[str] = mapped_column(String(16))  # sensor/camera
    device_name: Mapped[str] = mapped_column(String(64))
    device_sn: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(16), default="sim")  # sim/real（主 PRD 3.3.4）
    # C-24 设备侧凭证：存 sha256 hex，明文只在创建/轮换时返回一次。
    # 空串 = 该设备未签发设备密钥，只能走农户 JWT（存量设备天然处于此状态）。
    device_secret_hash: Mapped[str] = mapped_column(String(64), default="")
    mqtt_topic: Mapped[str] = mapped_column(String(128))
    rtsp_url: Mapped[str] = mapped_column(String(256), default="")
    online_status: Mapped[str] = mapped_column(String(16), default="offline")  # online/offline
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    is_enable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    install_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"
    # C-04：告警三枚举补 CHECK（此前只有 vet_reviews 有）。
    # 库内加列/加约束走 core/db.py 的幂等 DDL（Alembic 到位前同 C-21 纪律）；
    # 用 NOT VALID 是为了不因历史脏行把整个服务启动卡住，新写入照常受约束。
    __table_args__ = (
        CheckConstraint(
            "alert_type IN ('sensor','climate','vision','disease','device_offline')",
            name="chk_alert_type",
        ),
        CheckConstraint(
            "risk_level IN ('low','mid','high')",
            name="chk_alert_risk_level",
        ),
        CheckConstraint(
            "status IN ('pending','acknowledged','resolved','false_alarm')",
            name="chk_alert_status",
        ),
    )

    alert_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.farm_id"), index=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    alert_type: Mapped[str] = mapped_column(String(16))  # sensor/climate/vision/disease/device_offline
    risk_level: Mapped[str] = mapped_column(String(8))  # low/mid/high
    alert_content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/acknowledged/resolved/false_alarm
    handled_note: Mapped[str] = mapped_column(String(128), default="")
    handled_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SensorReading(Base):
    """传感器历史（07 协议 data 上行落库；查询侧抽稀返回）。"""

    __tablename__ = "sensor_readings"
    __table_args__ = (Index("idx_reading_pen_metric_ts", "pen_id", "metric", "ts"),)

    reading_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pen_id: Mapped[int] = mapped_column(Integer)
    metric: Mapped[str] = mapped_column(String(16))  # temperature/humidity/nh3/co2
    ts: Mapped[int] = mapped_column(BigInteger)  # epoch ms（以设备 ts 为准）
    value: Mapped[float] = mapped_column()


# ===== F07 繁殖 / 用药 / 出栏 =====

class MedicineLib(Base):
    __tablename__ = "medicine_lib"

    med_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    med_code: Mapped[str] = mapped_column(String(32), unique=True)
    med_name: Mapped[str] = mapped_column(String(64))
    species_code: Mapped[str] = mapped_column(String(16))
    disease_name: Mapped[str] = mapped_column(String(32))
    usage: Mapped[str] = mapped_column(String(256), default="")
    withdrawal_period: Mapped[int] = mapped_column(Integer, default=0)  # 天
    is_forbidden: Mapped[bool] = mapped_column(Boolean, default=False)
    contraindication: Mapped[str] = mapped_column(String(128), default="")
    risk_level: Mapped[str | None] = mapped_column(String(8), default=None)


class MedicineUsage(Base):
    __tablename__ = "medicine_usages"

    usage_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    med_id: Mapped[int] = mapped_column(Integer)
    usage_date: Mapped[date] = mapped_column(Date)
    dosage: Mapped[str] = mapped_column(String(64), default="")
    withdrawal_until: Mapped[date] = mapped_column(Date)  # = usage_date + withdrawal_period（服务端计算，口径 455）
    operator_note: Mapped[str] = mapped_column(String(128), default="")
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BreedingFemale(Base):
    __tablename__ = "breeding_females"
    __table_args__ = (
        Index("uq_female_pen_tag", "pen_id", "tag_no", unique=True, postgresql_where=text("is_deleted = false")),
    )

    female_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    tag_no: Mapped[str] = mapped_column(String(32))
    variety_code: Mapped[str | None] = mapped_column(String(32), default=None)
    parity: Mapped[int] = mapped_column(Integer, default=0)
    breed_status: Mapped[str] = mapped_column(String(16), default="open")  # open/bred/pregnant/nursing/culled
    breed_date: Mapped[date | None] = mapped_column(Date, default=None)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)  # = breed_date + gestation_days
    remark: Mapped[str] = mapped_column(String(128), default="")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LifecycleEvent(Base):
    __tablename__ = "lifecycle_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    female_id: Mapped[int | None] = mapped_column(Integer, default=None)  # 个体事件挂母畜
    pen_id: Mapped[int | None] = mapped_column(Integer, default=None)  # 批次事件挂圈
    event_type: Mapped[str] = mapped_column(String(16), index=True)
    event_date: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reminder(Base):
    __tablename__ = "reminders"

    reminder_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.farm_id"), index=True)
    pen_id: Mapped[int | None] = mapped_column(Integer, default=None)
    female_id: Mapped[int | None] = mapped_column(Integer, default=None)
    reminder_type: Mapped[str] = mapped_column(String(16))  # due_date/weaning/vaccine
    remind_date: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/done/expired
    handled_note: Mapped[str] = mapped_column(String(128), default="")
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ===== F05 AI 问诊会话 / F10 HITL 兽医复核 =====

class AgentSession(Base):
    """问诊会话（02-数据库 agent_session）。

    pending_clarify：圈舍澄清待补位的槽位记忆（F05 FR-03）——上一轮问了"哪个圈"，
    本轮短回复（如"3号圈"）与原始描述合并重诊，而不是当全新消息判 unknown。
    """

    __tablename__ = "agent_sessions"
    __table_args__ = (Index("idx_session_user", "user_id", "update_time"),)

    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), index=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    intent: Mapped[str] = mapped_column(String(16), default="consult")
    pending_clarify: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # 会话软删；台账不受影响
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentMessage(Base):
    """问诊消息（02-数据库 agent_message）。meta 承载 risk_level/sections/clarify/image。"""

    __tablename__ = "agent_messages"
    __table_args__ = (Index("idx_message_session", "session_id", "message_id"),)

    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.session_id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user/assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VetReview(Base):
    """兽医复核单（02-数据库 vet_review + F05 FR-09 放宽）。

    与原 DDL 的两处偏离（F05 PRD V1.1 已记录，02-数据库回写待办）：
    - alert_id 放宽可空（0=无告警）：问诊高危病例没有 alert_log 可挂，改用 session_id/message_id 关联；
    - 新增 thread_id：LangGraph checkpoint 的 thread 定位键，兽医复核后 Command(resume) 靠它找回
      挂起的工作流。thread_id 按"轮"而非按会话（同一会话第二轮不得复用未恢复的 checkpoint）。
    """

    __tablename__ = "vet_reviews"
    __table_args__ = (
        Index("idx_vreview_pending", "review_status", "create_time"),
        CheckConstraint(
            "review_status IN ('pending','approved','rejected','timeout')", name="chk_review_status"
        ),
    )

    review_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, default=0)  # 0=问诊高危病例（无告警）
    session_id: Mapped[int] = mapped_column(Integer, default=0)
    message_id: Mapped[int] = mapped_column(Integer, default=0)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.farm_id"), index=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    original_diagnosis: Mapped[str] = mapped_column(Text, default="")
    risk_level: Mapped[str] = mapped_column(String(8), default="high")  # low/mid/high
    review_status: Mapped[str] = mapped_column(String(16), default="pending")
    vet_user_id: Mapped[int | None] = mapped_column(Integer, default=None)  # 复核人（JWT 注入，不可抵赖）
    review_opinion: Mapped[str] = mapped_column(Text, default="")
    revised_diagnosis: Mapped[str] = mapped_column(Text, default="")
    revised_med: Mapped[str] = mapped_column(Text, default="")
    review_cost_hours: Mapped[float | None] = mapped_column(default=None)  # 复核耗时（十节审计留痕）
    review_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ===== F06 喂食中心（配方大脑 / 饲喂执行 / 数据反馈闭环）=====
# 数据源：app/agents/feed_seed.json（由 scripts/export_feed_seed.ts 从前端 feed-data.ts 机械导出）

class FeedNutritionLib(Base):
    """营养标准库（02-数据库 3.4 feed_nutrition_lib）。

    `stage=None` + `sex_type=female` 的组合用于母猪：由 sow_phase（gestation/lactation）
    按在栏母畜实际状态选档，而不是按圈舍 batch_stage（母猪圈不参与批次阶段配方匹配，PRD V1.7）。
    """

    __tablename__ = "feed_nutrition_lib"

    feed_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    species_code: Mapped[str] = mapped_column(String(32), index=True)
    variety_code: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str | None] = mapped_column(String(32), default=None)
    sex_type: Mapped[str] = mapped_column(String(8), default="all")  # all/female
    sow_phase: Mapped[str | None] = mapped_column(String(16), default=None)  # gestation/lactation
    min_weight: Mapped[float | None] = mapped_column(Numeric(10, 2), default=None)
    max_weight: Mapped[float | None] = mapped_column(Numeric(10, 2), default=None)
    nutrition: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # 日采食量/顿数/指标区间
    formula: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # [{name, ratio_pct}]
    weather_adjust: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    source: Mapped[str] = mapped_column(Text, default="")  # 溯源文案（配方 basis 直接引用）


class FeedPrice(Base):
    """原料价格（02-数据库 3.5）。历史追加式：改价不覆盖旧行，取 (name, price_date) 最新。

    C-18 租户模型（2026-10-16 定稿）：`user_id IS NULL` = 全局参考价（内置种子 + 行情采集写入，
    人人共享）；`user_id=<农户>` = 该农户自己的**改价覆盖**，对他就压过全局。
    mock 时代每人一份 localStorage，天然按浏览器隔离——加这一列是把切真时丢掉的
    "各改各的"语义补回来，同时保住"全局参考价不用每人录一遍"。
    """

    __tablename__ = "feed_prices"
    __table_args__ = (Index("idx_price_name_date", "ingredient_name", "price_date"),)

    price_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), default=None)  # NULL=全局参考价
    ingredient_name: Mapped[str] = mapped_column(String(64))
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    price_date: Mapped[date] = mapped_column(Date)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    seed_caliber: Mapped[str] = mapped_column(String(16), default="")  # 内置价标定日（刷新判据）
    price_source: Mapped[str] = mapped_column(String(24), default="manual")  # manual / llm_search / builtin
    source_site: Mapped[str] = mapped_column(String(64), default="")  # 行情来源站点名
    confidence: Mapped[str] = mapped_column(String(12), default="")  # high(≥2 条互证) / medium / ""
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedFormula(Base):
    """日粮配方（02-数据库 3.4）。候选态 is_active=false，activate 置真；
    同圈唯一生效由部分唯一索引保证（等价 mock 的 activate 序列）。"""

    __tablename__ = "feed_formulas"
    __table_args__ = (
        Index("uq_formula_active_per_pen", "pen_id", unique=True, postgresql_where=text("is_active = true")),
        Index("idx_formula_pen", "pen_id", "create_time"),
    )

    formula_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pen_id: Mapped[int | None] = mapped_column(ForeignKey("pens.pen_id"), default=None)
    species_code: Mapped[str] = mapped_column(String(32), default="pig")
    variety_code: Mapped[str | None] = mapped_column(String(32), default=None)
    stage: Mapped[str | None] = mapped_column(String(32), default=None)
    sex_type: Mapped[str] = mapped_column(String(8), default="all")
    label: Mapped[str] = mapped_column(String(64), default="基础配方")
    ingredients: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    feeds_per_day: Mapped[int] = mapped_column(Integer, default=2)
    cost_per_head_day: Mapped[float | None] = mapped_column(Numeric(10, 2), default=None)
    nutrition_check: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    basis: Mapped[str] = mapped_column(Text, default="")
    disclaimer: Mapped[str] = mapped_column(String(64), default="AI 生成，仅供参考")
    source: Mapped[str] = mapped_column(String(16), default="ai")  # ai/manual
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    activate_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeedingRecord(Base):
    """饲喂登记（02-数据库 feeding_record + V1.4 计划快照）。

    planned_kg 在登记那一刻快照写入，后续改配方/改存栏都不回写历史——保证日历上
    "计划 vs 实际"的历史对照稳定（F06 PRD 决策：计划快照）。
    同 (pen, date, feed_type) 重复登记为覆盖更新（mock 口径）。
    """

    __tablename__ = "feeding_records"
    __table_args__ = (
        Index("idx_feeding_record_pen_date", "pen_id", "feed_date"),
        UniqueConstraint("pen_id", "feed_date", "feed_type", name="uq_feeding_pen_date_type"),
    )

    feed_record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    feed_date: Mapped[date] = mapped_column(Date)
    feed_type: Mapped[str] = mapped_column(String(16))  # concentrate/roughage/silage/other
    amount_kg: Mapped[float] = mapped_column(Numeric(10, 2))
    planned_kg: Mapped[float | None] = mapped_column(Numeric(10, 2), default=None)
    as_planned: Mapped[bool] = mapped_column(Boolean, default=False)
    remark: Mapped[str] = mapped_column(String(128), default="")
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WeightRecord(Base):
    """称重记录（批次平均体重口径：抽几头取平均，录一个数）。同 (pen, date) 覆盖。"""

    __tablename__ = "weight_records"
    __table_args__ = (
        Index("idx_weight_record_pen_date", "pen_id", "weigh_date"),
        UniqueConstraint("pen_id", "weigh_date", name="uq_weight_pen_date"),
    )

    weight_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    weigh_date: Mapped[date] = mapped_column(Date)
    avg_weight_kg: Mapped[float] = mapped_column(Numeric(10, 2))
    sample_count: Mapped[int | None] = mapped_column(Integer, default=None)
    daily_gain_kg: Mapped[float | None] = mapped_column(Numeric(8, 4), default=None)  # 与上次自动算
    remark: Mapped[str] = mapped_column(String(128), default="")
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ===== F08 气象（缓存落地存储）=====

class WeatherCache(Base):
    """气象数据缓存（01-架构 5.3 `weather:{adcode}` 的持久化形态）。

    用 PostgreSQL 而非 Redis：缓存需要跨进程重启存活、且天然按 adcode 共享给同区养殖户，
    这两条关系型表给得更稳（Redis 只承接 core/statestore 那类短生命周期状态）。
    表主键即 adcode，TTL 6h 由 fetched_at 判定。
    """

    __tablename__ = "weather_cache"

    adcode: Mapped[str] = mapped_column(String(16), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {forecast, warnings, fetched_at}
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    source: Mapped[str] = mapped_column(String(16), default="live")  # live/sim


# ===== F16 知识库文档（02-数据库 3.7 kb_document + mock 检索扩展字段）=====

class KbDocument(Base):
    """知识库整理稿（F16）：admin 上传，启用即进问诊检索池（与 kb_seed 内置条目同池计分）。

    DDL 字段对齐 02-数据库 3.7；retrieval_keywords/risk_level/med_names/feed_advice/
    content_md/is_enable 为检索与演示扩展字段（前端 mock 同款口径）——B5 切 Milvus 时
    retrieval_keywords 供切片加权，其余进 chunk metadata，本表即文档管理面。
    纪律：不提供物理删除（停用退出检索池，可恢复）；上传必填 source + organizer（附录 E 留痕）。
    """

    __tablename__ = "kb_document"

    doc_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_name: Mapped[str] = mapped_column(String(128))
    doc_type: Mapped[str] = mapped_column(String(16))  # disease/nutrition
    species_code: Mapped[str] = mapped_column(String(32), default="")  # 空=公共法规文档（全物种适用）
    retrieval_keywords: Mapped[list[str]] = mapped_column(JSONB, default=list)  # 症状/主题关键词 ≥2 个
    risk_level: Mapped[str] = mapped_column(String(8), default="mid")  # 仅 disease 生效（nutrition 不抬风险）
    med_names: Mapped[list[str]] = mapped_column(JSONB, default=list)  # 仅 disease；上传时过兽药库非违禁校验
    feed_advice: Mapped[str] = mapped_column(Text, default="")  # 饲养/饲喂建议（nutrition 进饲料建议补充段）
    content_md: Mapped[str] = mapped_column(Text, default="")  # markdown 正文存档 ≤20000
    source: Mapped[str] = mapped_column(String(256), default="")  # 出处（书名/规范名，必填留痕）
    organizer: Mapped[str] = mapped_column(String(64), default="")  # 整理人（必填留痕）
    chunk_count: Mapped[int] = mapped_column(Integer, default=1)  # mock 切片数（正文每 300 字一片，≥1）
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)  # 索引重建时间
    is_enable: Mapped[bool] = mapped_column(Boolean, default=True)  # 停用即退出检索池（不删档可恢复）
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    # 2026-10-15 知识库书架改版：支持上传各类原始文件（pdf/office/图片/音视频等），
    # 文件落盘 uploads/kb/，库里存元数据；storage_path 为空 = 纯文本整理稿（无附件）
    file_name: Mapped[str | None] = mapped_column(String(256), default=None)  # 原始文件名
    file_ext: Mapped[str | None] = mapped_column(String(16), default=None)  # 小写扩展名（不含点）
    file_size: Mapped[int | None] = mapped_column(Integer, default=None)  # 字节
    storage_path: Mapped[str | None] = mapped_column(String(512), default=None)  # 相对 backend/ 的存储路径
    uploader_id: Mapped[int | None] = mapped_column(Integer, default=None)  # 上传者（删除/停用权限判定）
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CameraAiResult(Base):
    """视觉识别事件（F09 / 07-协议第四节）。

    只存事实，不存结论：`level` 与 `event_label` 都在读侧由 `vision_json.event_type`
    + `confidence` 现算（与 mock 同口径），改分级规则不用回刷历史数据。
    `vision_json` 全量透传落库（含 bbox/extra），bbox 按 V1.0 约定不对外返回。
    """

    __tablename__ = "camera_ai_results"
    __table_args__ = (Index("idx_vision_pen_time", "pen_id", "collect_time"),)

    result_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.device_id"), index=True)
    pen_id: Mapped[int] = mapped_column(ForeignKey("pens.pen_id"), index=True)
    collect_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 事件采集时刻（消息 ts）
    vision_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SceneAnalysis(Base):
    """F15 拍照建 3D 场景的识别结果（2026-10-16 落库）。

    只存**派生配置**，不存照片本体：照片是 data URL（数百 KB/张）且属现场影像，
    复现场景需要的只是那份参数化配置。此前结果只活在 `useState` 里，刷新即消失，
    而界面话术说的是"已按照片生成场景"——刷新后那句就成了假话。
    """

    __tablename__ = "scene_analyses"
    __table_args__ = (Index("idx_scene_user_time", "user_id", "create_time"),)

    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # 参数化 3D 配置
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # Float 而非 Numeric：Decimal 进 JSON 会序列化失败
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64), default="")
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
