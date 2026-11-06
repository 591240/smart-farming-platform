#!/usr/bin/env bash
# 云上牧场 PostgreSQL 备份 + **restore 自检**
#   chmod 755 deploy/sf-backup.sh
#   crontab -e →  30 3 * * * root /srv/smart-farming/deploy/sf-backup.sh >> /var/log/smart-farming/backup.log 2>&1
#
# 为什么每次都要真 restore 一遍：只 pg_dump 不验证，得到的是一个"看起来存在的文件"，
# 而备份失效最常见的原因（磁盘写满、格式不兼容、权限、只 dump 了半个库）全都长得跟
# 成功一样。这里每天把它倒进一个一次性库、数一遍表，失败就非零退出让 cron 报错。
#
# 注意：本文件若在 Windows 上编辑过，部署前先 `dos2unix sf-backup.sh`，否则 bash 会
# 因为 \r 报 "bad interpreter"。

set -euo pipefail

DB_CONTAINER=sf-postgres
DB_USER=smartfarm
DB_NAME=smart_farm
VERIFY_DB=sf_backup_verify
KEEP_DAYS=14
BACKUP_DIR=/var/backups/smart-farming

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d-%H%M)
FILE="$BACKUP_DIR/${DB_NAME}-${STAMP}.dump"

log() { echo "[$(date '+%F %T')] $*"; }

# -Fc（custom format）便于按需恢复单表；--no-owner 避免换机器后角色对不上
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc --no-owner > "$FILE"

if [ ! -s "$FILE" ] || [ "$(head -c 5 "$FILE")" != "PGDMP" ]; then
  log "[FAIL] 产物不是合法的 pg_dump custom format，已保留文件供排查：$FILE"
  exit 1
fi
log "dump 完成 $(du -h "$FILE" | cut -f1)  $FILE"

# —— restore 自检 ——
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -q -c "DROP DATABASE IF EXISTS $VERIFY_DB"
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -q -c "CREATE DATABASE $VERIFY_DB"
if ! docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$VERIFY_DB" --no-owner < "$FILE"; then
  log "[FAIL] pg_restore 报错，备份不可用：$FILE"
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -q -c "DROP DATABASE $VERIFY_DB" || true
  exit 1
fi

SRC_TABLES=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "select count(*) from pg_tables where schemaname='public'")
DST_TABLES=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$VERIFY_DB" -tAc \
  "select count(*) from pg_tables where schemaname='public'")
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -q -c "DROP DATABASE $VERIFY_DB"

if [ "$DST_TABLES" != "$SRC_TABLES" ]; then
  log "[FAIL] restore 后表数不一致：源 $SRC_TABLES / 备份 $DST_TABLES"
  exit 1
fi
log "restore 自检通过（$DST_TABLES 张表）"

# 轮转：只删匹配本脚本命名格式的旧文件，不碰目录里的其它内容
find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}-*.dump" -mtime +"$KEEP_DAYS" -delete
log "保留最近 $KEEP_DAYS 天，当前占用 $(du -sh "$BACKUP_DIR" | cut -f1)"

# 恢复命令（记在这里，出事时不用去找文档）：
#   docker exec -i sf-postgres psql -U smartfarm -d postgres -c "DROP DATABASE IF EXISTS smart_farm_restored"
#   docker exec -i sf-postgres psql -U smartfarm -d postgres -c "CREATE DATABASE smart_farm_restored"
#   docker exec -i sf-postgres pg_restore -U smartfarm -d smart_farm_restored --no-owner < 备份文件.dump
# 先恢复到 *_restored 而不是直接覆盖现库，确认数据对了再切。
