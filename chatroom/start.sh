#!/usr/bin/env bash
# 一鍵啟動：本機聊天室伺服器 + Cloudflare 臨時隧道（自動印出公網網址）
# 用法：./start.sh   （Windows 請改用 `npm start`，隧道另開終端跑 cloudflared）

set -euo pipefail
cd "$(dirname "$0")"

[ -d node_modules ] || npm install

STICKER_ADMIN_PASSWORD="${STICKER_ADMIN_PASSWORD:-0425}" node server.js &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
sleep 1

echo ""
echo "本機網址： http://localhost:3000"
echo ""

if command -v cloudflared >/dev/null 2>&1; then
  echo "啟動公網隧道（把印出的 https 網址 + 房號發給朋友）..."
  cloudflared tunnel --url http://localhost:3000
else
  echo "未安裝 cloudflared，目前僅本機可用。"
  echo "要跨裝置/讓中國朋友連，先安裝： brew install cloudflared"
  wait "$SERVER_PID"
fi
