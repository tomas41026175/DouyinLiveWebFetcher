#!/usr/bin/env bash
# 一鍵啟動（mac/Linux）：檢測/安裝環境 → 啟動「抖音弹幕 web_danmaku(本機)」+「聊天室 chatroom」
# 聊天室透過 Cloudflare 隧道取得「公網網址」分享給朋友（trycloudflare，GFW 未封時中國可連）。
# 另有 VPS 雲端部署（web_danmaku + chatroom + systemd 常駐），見 deploy/cloud-deploy.md。
# 用法： ./start_all.sh        （Ctrl+C 結束所有服務）

set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
CHATROOM="$ROOT/chatroom"
VENV="$ROOT/venv"
DANMAKU_PORT=8765
CHAT_PORT=3000
CHAT_URL_FILE="$ROOT/chatroom_url.txt"
CF_LOG="$ROOT/.cf_tunnel.log"
ROOM=123456   # 啟動後聊天室自動進入的固定房號

c_say(){ printf '\033[36m%s\033[0m\n' "$*"; }
c_ok(){ printf '\033[32m%s\033[0m\n' "$*"; }
c_warn(){ printf '\033[33m%s\033[0m\n' "$*"; }
c_err(){ printf '\033[31m%s\033[0m\n' "$*"; }

# ---------- 版本 + 自動更新 ----------
VERSION="$(cat "$ROOT/VERSION" 2>/dev/null || echo unknown)"
c_say "整合聊天室 v$VERSION"
if [ -z "${DY_SELF_UPDATED:-}" ] && git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "    檢查更新…"
  git -C "$ROOT" fetch --quiet 2>/dev/null || true
  LOCAL="$(git -C "$ROOT" rev-parse @ 2>/dev/null || true)"
  REMOTE="$(git -C "$ROOT" rev-parse '@{u}' 2>/dev/null || true)"
  BASE="$(git -C "$ROOT" merge-base @ '@{u}' 2>/dev/null || true)"
  if [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ] && [ "$LOCAL" = "$BASE" ]; then
    c_warn "    發現新版本，自動更新中 (git pull)…"
    if git -C "$ROOT" pull --ff-only --quiet 2>/dev/null; then
      c_ok "    ✅ 已更新到最新版，以新版重新啟動"
      export DY_SELF_UPDATED=1
      exec bash "$ROOT/start_all.sh" "$@"   # 絕對路徑 + bash 重跑，不依賴 +x / cwd / PATH
    else
      c_warn "    ⚠ 自動更新失敗（本地可能有改動），改用當前版本繼續。可手動： git stash && git pull"
    fi
  elif [ -n "$REMOTE" ]; then
    c_ok "    已是最新版"
  fi
fi

# ---------- [1/5] 基礎工具 ----------
c_say "==> [1/5] 檢測 Python / Node / cloudflared"
PY="$(command -v python3 || true)"
[ -z "$PY" ] && { c_err "缺 python3：brew install python3"; exit 1; }
echo "    python: $("$PY" --version 2>&1)"
command -v node >/dev/null 2>&1 || { c_err "缺 node：brew install node"; exit 1; }
echo "    node:   $(node --version)"
if ! command -v cloudflared >/dev/null 2>&1; then
  c_warn "    未裝 cloudflared，嘗試安裝…"
  if command -v brew >/dev/null 2>&1; then
    brew install cloudflared || { c_err "    cloudflared 安裝失敗，請手動安裝後重試"; exit 1; }
  else
    c_err "    找不到 brew，請手動安裝 cloudflared（https://github.com/cloudflare/cloudflared）後重試"; exit 1
  fi
fi
echo "    cloudflared: $(cloudflared --version 2>&1 | head -1)"

# ---------- [2/5] Python venv + 依賴 ----------
c_say "==> [2/5] Python 環境 (venv + requirements)"
if [ ! -x "$VENV/bin/python" ]; then
  c_warn "    venv 不存在或非本機版（原為 Windows venv），重建中…"
  rm -rf "$VENV"; "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"
if "$VPY" -c "import websocket, betterproto, py_mini_racer, execjs, requests" 2>/dev/null; then
  c_ok "    ✅ Python 依賴已就緒"
else
  echo "    安裝 Python 依賴（首次較久，含下載 V8 引擎）…"
  "$VPY" -m pip install -q --upgrade pip
  if "$VPY" -m pip install -q -r "$ROOT/requirements.txt"; then c_ok "    ✅ 完成"
  else c_warn "    ⚠️ 部分依賴安裝失敗：抖音抓取可能不可用，但聊天室仍可運作"; fi
fi

# ---------- [3/5] chatroom Node 依賴 ----------
c_say "==> [3/5] chatroom (Node) 依賴"
if [ -d "$CHATROOM/node_modules" ]; then c_ok "    ✅ 已就緒"
else echo "    npm install…"; ( cd "$CHATROOM" && npm install --no-fund --no-audit ) && c_ok "    ✅ 完成" || { c_err "    npm install 失敗"; exit 1; }; fi

# ---------- [4/5] 啟動本機服務 ----------
c_say "==> [4/5] 啟動 web_danmaku + chatroom（本機）"
lsof -tiTCP:$DANMAKU_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -tiTCP:$CHAT_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1
rm -f "$CF_LOG" "$CHAT_URL_FILE"

PASSWORD="$(tr -d '[:space:]' < "$ROOT/ui_password.txt" 2>/dev/null || echo 0425)"
PIDS=()
cleanup(){ echo ""; c_say "關閉所有服務中…"; rm -f "$CF_LOG" "$CHAT_URL_FILE"; for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

( cd "$ROOT" && DY_NO_AUTOCLOSE=1 DY_NO_BROWSER=1 "$VPY" web_danmaku.py >/dev/null 2>&1 ) &
PIDS+=($!)
( cd "$CHATROOM" && PORT=$CHAT_PORT DANMAKU_URL="http://127.0.0.1:$DANMAKU_PORT" DANMAKU_PASSWORD="$PASSWORD" STICKER_ADMIN_PASSWORD="$PASSWORD" STICKER_GIT_POLL=1 node server.js >/dev/null 2>&1 ) &
PIDS+=($!)
sleep 3

# ---------- [5/5] Cloudflare 隧道（聊天室公網網址，分享給朋友）----------
c_say "==> [5/5] 建立 Cloudflare 隧道（聊天室公網網址）"
cloudflared tunnel --url "http://localhost:$CHAT_PORT" >"$CF_LOG" 2>&1 &
PIDS+=($!)
PUBLIC_URL=""
for _ in $(seq 1 30); do
  PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1)"
  [ -n "$PUBLIC_URL" ] && break
  sleep 1
done
[ -n "$PUBLIC_URL" ] && printf '%s' "$PUBLIC_URL" > "$CHAT_URL_FILE"

echo ""
echo "============================================================"
if [ -n "$PUBLIC_URL" ]; then
  c_ok "  ✅ 聊天室（分享給朋友這個網址，中國可連）："
  echo "       $PUBLIC_URL"
else
  c_warn "  ⚠️ 未取得 Cloudflare 網址（看 $CF_LOG）；本機自測： http://localhost:$CHAT_PORT"
fi
echo  "  抖音弹幕 webUI（本機自己看）: http://127.0.0.1:$DANMAKU_PORT  (密碼 $PASSWORD)"
echo  ""
echo  "  Ctrl+C 結束所有服務（含隧道）"
echo "============================================================"

# ---------- 自動開啟瀏覽器分頁：抖音 webUI + 聊天室（直接進房號 $ROOM）----------
# 聊天室優先用「公網網址」（讓分享 / QR 編碼的是可對外網址）；取不到才退本機 localhost
WEBUI_OPEN_URL="http://127.0.0.1:$DANMAKU_PORT/"
if [ -n "$PUBLIC_URL" ]; then
  CHAT_OPEN_URL="$PUBLIC_URL/?room=$ROOM"
else
  CHAT_OPEN_URL="http://localhost:$CHAT_PORT/?room=$ROOM"
fi

c_say "==> 自動開啟瀏覽器分頁（直接進房號 $ROOM）"
echo "    抖音 webUI : $WEBUI_OPEN_URL"
echo "    聊天室     : $CHAT_OPEN_URL"

# 開分頁：成功印 ✅，失敗印 ⚠️ + 手動網址（不再把錯誤吞掉，方便排查）
open_tab(){
  if open "$1" 2>/dev/null; then
    c_ok "    ✅ 已開：$2"
  else
    c_warn "    ⚠️ 未能自動開「$2」，請手動貼到瀏覽器：$1"
  fi
}
open_tab "$WEBUI_OPEN_URL" "抖音 webUI"
sleep 1   # 兩分頁間隔，避免部分瀏覽器忽略連續開啟而漏開聊天室
open_tab "$CHAT_OPEN_URL" "聊天室（房號 $ROOM）"

wait
