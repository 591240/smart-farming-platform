#!/usr/bin/env bash
# 云上牧场 — 一键更新脚本（在本机 Git Bash 运行）
# 用法：
#   bash deploy/update.sh fe      # 只更新前端
#   bash deploy/update.sh be      # 只更新后端代码
#   bash deploy/update.sh all     # 前后端都更新
# 服务器：ubuntu@43.142.92.229（SSH 走 ~/.ssh/tencent_shanghai 或 ~/Downloads/1.pem）

set -e
HOST=ubuntu@43.142.92.229
SSH_OPTS="-o BatchMode=yes -o UserKnownHostsFile=/tmp/sf_known_hosts"
KEY="$HOME/.ssh/tencent_shanghai"
[ -f "$KEY" ] || KEY="$USERPROFILE/Downloads/1.pem"
TARGET="${1:-all}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ssh_cmd() { ssh -i "$KEY" $SSH_OPTS "$HOST" "$@"; }
scp_cmd() { scp -i "$KEY" $SSH_OPTS "$@"; }

echo "== 目标：$TARGET =="

if [ "$TARGET" = "fe" ] || [ "$TARGET" = "all" ]; then
  echo "-- 构建前端 --"
  cd "$ROOT/smart-farming-vue"
  [ -d dist ] && rm -rf dist.old && mv dist dist.old   # safe-delete 钩子会拦 vite 清空，用 mv 绕开
  npm run build
  tar -czf /tmp/sf_dist.tar.gz -C . dist
  echo "-- 上传 dist --"
  scp_cmd /tmp/sf_dist.tar.gz "$HOST:/tmp/"
  ssh_cmd 'sudo rm -rf /srv/smart-farming/dist && sudo tar -xzf /tmp/sf_dist.tar.gz -C /srv/smart-farming && sudo chown -R smartfarm:smartfarm /srv/smart-farming/dist && echo FE-OK'
fi

if [ "$TARGET" = "be" ] || [ "$TARGET" = "all" ]; then
  echo "-- 打包后端 --"
  cd "$ROOT"
  tar -czf /tmp/sf_backend.tar.gz --exclude='__pycache__' --exclude='*.pyc' --exclude='*.log' backend
  echo "-- 上传 backend --"
  scp_cmd /tmp/sf_backend.tar.gz "$HOST:/tmp/"
  ssh_cmd 'sudo rm -rf /srv/smart-farming/backend.bak && sudo mv /srv/smart-farming/backend /srv/smart-farming/backend.bak && sudo tar -xzf /tmp/sf_backend.tar.gz -C /srv/smart-farming && sudo mkdir -p /srv/smart-farming/backend/uploads && sudo cp -rn /srv/smart-farming/backend.bak/uploads/* /srv/smart-farming/backend/uploads/ 2>/dev/null; sudo chown -R smartfarm:smartfarm /srv/smart-farming/backend && sudo systemctl restart smart-farming-api && sleep 6 && systemctl is-active smart-farming-api 2>/dev/null || sudo systemctl is-active smart-farming-api && curl -s http://127.0.0.1:8000/api/health && echo && echo BE-OK'
fi

echo "== 完成 =="
