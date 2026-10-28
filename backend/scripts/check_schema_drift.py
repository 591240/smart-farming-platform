"""Schema 漂移检查 —— models.py 声明的列 vs 数据库里真实存在的列。

为什么需要它：`init_db()` 用 `create_all` 建表，而 SQLAlchemy 的 create_all **只补少的表，
不会给已存在的表加列**。所以给模型加一列而忘了同步 `_PENDING_COLUMNS`，代码在自己机器上跑得好
（新库是 create_all 建的、列齐全），一上老库就 500，而且炸在**读路径**上：
2026-10-15 实测 `KbDocument` 加了 5 个文件列没补 ALTER，`GET /kb/list` 直接
`column kb_document.file_name does not exist`。

这不是"上 Alembic 就好了"的替代方案（那是 C-17，需要单独专项），而是**在 Alembic 之前**
把同类事故从"线上读到时才发现"提前到"启动/CI 就报红"的 30 行检查。

用法：
    python scripts/check_schema_drift.py                  # 用当前 DATABASE_URL
    DATABASE_URL=...smart_farm python scripts/check_schema_drift.py
    python scripts/check_schema_drift.py --json           # 给 CI 消费
退出码：0 无漂移；1 有缺表/缺列。库里多出来的列只提示不判失败（回滚场景常见）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许从 backend/ 直接跑

from sqlalchemy import text  # noqa: F401,E402  （保留给 --json 之外的调试查询）

from app.core.db import SessionLocal, schema_drift  # noqa: E402


async def collect() -> dict:
    """委托给 `app.core.db.schema_drift()`——冒烟与启动自检共用同一份判定逻辑。"""
    return await schema_drift()


def report(res: dict) -> None:
    if res["ok"] and not res["extra_columns"]:
        print(f"schema 一致：{res['tables_checked']} 张表的列与 models.py 完全对齐")
        return
    for t in res["missing_tables"]:
        print(f"  × 缺表：{t}")
    for t, cols in res["missing_columns"].items():
        print(f"  × 缺列：{t} → {'、'.join(cols)}")
        print(f"      修：把这几条加进 app/core/db.py 的 _PENDING_COLUMNS（ADD COLUMN IF NOT EXISTS），或走 Alembic（C-17）")
    for t, cols in res["extra_columns"].items():
        print(f"  ! 库里多出未声明的列（不判失败）：{t} → {'、'.join(cols)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="输出 JSON（CI 消费）")
    args = parser.parse_args()
    res = asyncio.run(collect())
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        report(res)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
