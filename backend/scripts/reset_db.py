"""演示库重置 — 清掉冒烟/探针写进 `smart_farm` 的业务数据，只保留参考种子。

为什么需要它：所有冒烟脚本（smoke_agent / smoke_feeding / smoke_backend）都打同一个
DATABASE_URL，跑一次就留一批「喂食冒烟场」这类垃圾。演示库必须随时能回到干净状态。

保留（参考数据，由启动 lifespan 幂等重灌）：
  medicine_lib（兽药库）、feed_nutrition_lib（营养标准）、feed_prices 内置价
清空（业务数据）：
  其余全部 public 表 + langgraph schema 的 checkpoint 四表

用法：
    python scripts/reset_db.py            # 只打印将要清空的内容（干跑，默认）
    python scripts/reset_db.py --yes      # 真删
    python scripts/reset_db.py --yes --db smart_farm_test

安全：默认干跑；--yes 才执行。删除在单事务内，中途失败整体回滚。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许从 scripts/ 直跑

import psycopg

from app.core.config import settings

KEEP_TABLES = {"medicine_lib", "feed_nutrition_lib"}  # 参考种子：不动，启动时按业务键幂等补齐
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_writes", "checkpoint_blobs", "checkpoint_migrations")


def conninfo(db: str) -> str:
    base = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    return base.rsplit("/", 1)[0] + "/" + db


def business_tables(conn) -> list[str]:
    rows = conn.execute(
        """
        select tablename from pg_tables
        where schemaname='public' and tablename <> 'alembic_version'
        order by tablename
        """
    ).fetchall()
    return [r[0] for r in rows if r[0] not in KEEP_TABLES]


def main() -> None:
    args = sys.argv[1:]
    apply = "--yes" in args
    db = args[args.index("--db") + 1] if "--db" in args else "smart_farm"

    with psycopg.connect(conninfo(db)) as conn:
        targets = business_tables(conn)
        print(f"\n目标库：{db}")
        print(f"将清空 {len(targets)} 张业务表：")
        for t in targets:
            n = conn.execute(f"select count(*) from public.{t}").fetchone()[0]
            print(f"  {t:24s} {n:>7} 行")
        print("保留（参考种子，启动时幂等补齐）：" + ", ".join(sorted(KEEP_TABLES)))
        ck = conn.execute(
            "select count(*) from information_schema.tables where table_schema='langgraph'"
        ).fetchone()[0]
        if ck:
            total = conn.execute("select count(*) from langgraph.checkpoints").fetchone()[0]
            print(f"LangGraph checkpoint：{total} 行（{db} 库内 schema langgraph）")

        if not apply:
            print("\n[干跑] 未做任何修改。确认无误后加 --yes 执行。")
            return

        if targets:
            conn.execute("TRUNCATE TABLE " + ", ".join(f"public.{t}" for t in targets) + " RESTART IDENTITY CASCADE")
        if ck:
            conn.execute("TRUNCATE TABLE " + ", ".join(f"langgraph.{t}" for t in CHECKPOINT_TABLES) + " RESTART IDENTITY CASCADE")
        conn.commit()
        print("\n[done] 已清空。下次启动后端会自动重灌兽药库与营养/价格种子；")
        print("       三天故事演示数据请再跑一次：python seed_story.py")


if __name__ == "__main__":
    main()
