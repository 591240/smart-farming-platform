"""数据库装配 — async SQLAlchemy + asyncpg（docker-compose sf-postgres）。

建表策略：**单一事实源 = models.py + 本文件的幂等 DDL**。

2026-10-23 移除了并存的 Alembic 配置（已归档到 `_archive/alembic-retired-20261023/`）：
它的 baseline 只有 27 张表（缺 `scene_analyses`），而启动路径根本不走它——两套建表机制
互相掩盖，`alembic upgrade head` 建出来的库比 `create_all` 少一张表。演示期只保留
`create_all + _PENDING_COLUMNS`；上生产前按 `docs/08` 的登记重建迁移链（届时以
models.py 为准重新 autogenerate，而不是复用那份已经漂移的 baseline）。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


# 已存在表的新增列（create_all 只建缺失的表，不会 ALTER 已有表）
_PENDING_COLUMNS = (
    "ALTER TABLE ledger_records ADD COLUMN IF NOT EXISTS vet_review_id INTEGER NOT NULL DEFAULT 0",
    # D1 行情溯源列：价格从哪来、几条互证，前端要能如实标注而非假装实时
    "ALTER TABLE feed_prices ADD COLUMN IF NOT EXISTS price_source VARCHAR(24) NOT NULL DEFAULT 'manual'",
    "ALTER TABLE feed_prices ADD COLUMN IF NOT EXISTS source_site VARCHAR(64) NOT NULL DEFAULT ''",
    "ALTER TABLE feed_prices ADD COLUMN IF NOT EXISTS confidence VARCHAR(12) NOT NULL DEFAULT ''",
    "UPDATE feed_prices SET price_source='builtin' WHERE is_builtin=TRUE AND price_source='manual'",
    # C-18：原料价租户维度（NULL=全局参考价，非空=该农户的改价覆盖）。存量行全部留 NULL，
    # 即"现有价格仍是共享参考价"，不改动任何历史数据
    "ALTER TABLE feed_prices ADD COLUMN IF NOT EXISTS user_id INTEGER",
    "CREATE INDEX IF NOT EXISTS idx_price_user_name ON feed_prices (user_id, ingredient_name)",
    # C-24：设备上行凭证（存量设备为空 = 只允许农户 JWT 上报，需显式轮换才启用设备密钥）
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS device_secret_hash VARCHAR(64) NOT NULL DEFAULT ''",
    # F10：兽医复核单的 checkpoint 定位键。models.py 已声明，老库缺列 → 读复核列表直接 500
    "ALTER TABLE vet_reviews ADD COLUMN IF NOT EXISTS thread_id VARCHAR(64)",
    "CREATE INDEX IF NOT EXISTS ix_vet_reviews_thread_id ON vet_reviews (thread_id)",
    # F16 知识库附件列（模型已加、create_all 不会 ALTER 已有表 → 老库 SELECT 直接 500）
    "ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS file_name VARCHAR(256)",
    "ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS file_ext VARCHAR(16)",
    "ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS file_size INTEGER",
    "ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS storage_path VARCHAR(512)",
    "ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS uploader_id INTEGER",
    # C-04：告警枚举 CHECK（NOT VALID 不追检历史行，先管住新写入）
    "ALTER TABLE alerts DROP CONSTRAINT IF EXISTS chk_alert_type",
    "ALTER TABLE alerts ADD CONSTRAINT chk_alert_type CHECK (alert_type IN ('sensor','climate','vision','disease','device_offline')) NOT VALID",
    "ALTER TABLE alerts DROP CONSTRAINT IF EXISTS chk_alert_risk_level",
    "ALTER TABLE alerts ADD CONSTRAINT chk_alert_risk_level CHECK (risk_level IN ('low','mid','high')) NOT VALID",
    "ALTER TABLE alerts DROP CONSTRAINT IF EXISTS chk_alert_status",
    "ALTER TABLE alerts ADD CONSTRAINT chk_alert_status CHECK (status IN ('pending','acknowledged','resolved','false_alarm')) NOT VALID",
    # 上线检查：告警/台账的分页热路径是 `farm_id IN(...) [+ status] ORDER BY create_time DESC`，
    # 而两表都只有单列索引 → 生产量下等于按场取全行再排序。复合索引让过滤与排序都走索引。
    "CREATE INDEX IF NOT EXISTS idx_alerts_farm_status_time ON alerts (farm_id, status, create_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_farm_time ON ledger_records (farm_id, create_time DESC)",
)


async def init_db() -> None:
    from app import models  # noqa: F401  确保模型注册到 Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in _PENDING_COLUMNS:
            await conn.execute(text(ddl))


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def schema_drift() -> dict:
    """比对 models.py 声明的列与库里真实存在的列（缺列=读路径随时 500，见 docs/08 C-21）。

    `create_all` 只补少的**表**，不会给已存在的表加列；加列必须同步 `_PENDING_COLUMNS`，
    否则本地新建库一切正常、老库一读就炸，而且炸在读接口上（比写接口更难发现）。
    """
    from sqlalchemy import text

    from app import models  # noqa: F401  确保全部表注册进 metadata

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                text("select table_name, column_name from information_schema.columns where table_schema='public'")
            )
        ).all()
    actual: dict[str, set[str]] = {}
    for table, column in rows:
        actual.setdefault(table, set()).add(column)

    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    extra_columns: dict[str, list[str]] = {}
    for table, model in Base.metadata.tables.items():
        declared = {c.name for c in model.columns}
        if table not in actual:
            missing_tables.append(table)
            continue
        if gap := sorted(declared - actual[table]):
            missing_columns[table] = gap
        if surplus := sorted(actual[table] - declared):
            extra_columns[table] = surplus
    return {
        "tables_checked": len(Base.metadata.tables),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "extra_columns": extra_columns,
        "ok": not missing_tables and not missing_columns,
    }
