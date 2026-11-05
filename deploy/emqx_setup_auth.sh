#!/usr/bin/env bash
# 一次性脚本：EMQX 建账号 + 收紧 ACL（认证器已在上一轮创建成功）
set -euo pipefail
DASH=http://127.0.0.1:18083
TOKEN=$(curl -s -X POST "$DASH/api/v5/login" -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"public"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
AUTHH="Authorization: Bearer $TOKEN"
echo "== 认证链: $(curl -s "$DASH/api/v5/authentication" -H "$AUTHH")"

PW_BRIDGE=$(openssl rand -base64 18 | tr '+/' 'Aa' | tr -d '\n')
PW_DEV=$(openssl rand -base64 18 | tr '+/' 'Aa' | tr -d '\n')
for u in sfbridge:$PW_BRIDGE farmdev:$PW_DEV; do
  uid=${u%%:*}; pw=${u#*:}
  CODE=$(curl -s -o /tmp/u_resp.json -w '%{http_code}' -X POST \
    "$DASH/api/v5/authentication/password_based:built_in_database/users" \
    -H "$AUTHH" -H 'Content-Type: application/json' \
    -d "{\"user_id\":\"$uid\",\"password\":\"$pw\",\"is_superuser\":false}")
  echo "== 建用户 $uid HTTP $CODE: $(cat /tmp/u_resp.json)"
done

cat > /tmp/acl_rules.erl <<'EOF'
%%%% 云上牧场 ACL（2026-11-05）%%%%
{allow, {username, "sfbridge"}, all, ["farm/+/pen/+/+/data"]}.
{allow, {username, "farmdev"}, publish, ["farm/+/pen/+/+/data"]}.
{deny, all}.
EOF
python3 -c 'import json; json.dump({"rules": open("/tmp/acl_rules.erl").read()}, open("/tmp/acl_file.json","w"))'
CODE=$(curl -s -o /tmp/acl_resp.json -w '%{http_code}' -X PUT "$DASH/api/v5/authorization/sources/file" \
  -H "$AUTHH" -H 'Content-Type: application/json' -d @/tmp/acl_file.json)
echo "== 更新 ACL(file 源) HTTP $CODE: $(head -c 300 /tmp/acl_resp.json)"

echo "== 验证：用户列表:"
curl -s "$DASH/api/v5/authentication/password_based:built_in_database/users" -H "$AUTHH" | head -c 300; echo

echo "== MQTT_CREDENTIALS_BEGIN"
echo "bridge  user=sfbridge  pass=$PW_BRIDGE"
echo "device  user=farmdev   pass=$PW_DEV"
echo "== MQTT_CREDENTIALS_END"
rm -f /tmp/u_resp.json /tmp/acl_resp.json /tmp/acl_file.json /tmp/acl_rules.erl
