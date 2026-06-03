#!/usr/bin/env bash
# 一鍵啟動：自動檢測/安裝環境 → 同時啟動「抖音弹幕 web_danmaku」+「聊天室 chatroom」
# 聊天室訊息會即時鏡像到 web_danmaku webUI 的「聊天室」視窗。
# 用法： ./start_all.sh        （Ctrl+C 同時結束兩個服務）

set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
CHATROOM="$ROOT/chatroom"
VENV="$ROOT/venv"
DANMAKU_PORT=8765
CHAT_PORT=3000

c_say(){ printf '\033[36m%s\033[0m\n' "$*"; }
c_ok(){ printf '\033[32m%s\033[0m\n' "$*"; }
c_warn(){ printf '\033[33m%s\033[0m\n' "$*"; }
c_err(){ printf '\033[31m%s\033[0m\n' "$*"; }

# ---------- [1/4] 基礎工具 ----------
c_say "==> [1/4] 檢測 Python / Node"
PY="$(command -v python3 || true)"
[ -z "$PY" ] && { c_err "缺 python3，請先安裝： brew install python3"; exit 1; }
echo "    python: $("$PY" --version 2>&1)"
command -v node >/dev/null 2>&1 || { c_err "缺 node，請先安裝 Node.js 18+： brew install node"; exit 1; }
echo "    node:   $(node --version)"

# ---------- [2/4] Python venv + 依賴 ----------
c_say "==> [2/4] Python 環境 (venv + requirements)"
# 原 repo 的 venv 是 Windows 版（無 bin/python），macOS 不可用 → 重建
if [ ! -x "$VENV/bin/python" ]; then
  c_warn "    venv 不存在或非本機版（原為 Windows venv），重建中…"
  rm -rf "$VENV"
  "$PY" -m venv "$VENV"
fi
VPY="$VENV/bin/python"
if "$VPY" -c "import websocket, betterproto, py_mini_racer, execjs, requests" 2>/dev/null; then
  c_ok "    ✅ Python 依賴已就緒"
else
  echo "    安裝 Python 依賴（首次較久，含下載 V8 引擎）…"
  "$VPY" -m pip install -q --upgrade pip
  if "$VPY" -m pip install -q -r "$ROOT/requirements.txt"; then
    c_ok "    ✅ Python 依賴安裝完成"
  else
    c_warn "    ⚠️ 部分依賴安裝失敗：抖音弹幕抓取可能不可用，但聊天室與 webUI 鏡像仍可運作"
  fi
fi

# ---------- [3/4] chatroom Node 依賴 ----------
c_say "==> [3/4] chatroom (Node) 依賴"
if [ -d "$CHATROOM/node_modules" ]; then
  c_ok "    ✅ 已就緒"
else
  echo "    npm install…"
  ( cd "$CHATROOM" && npm install --no-fund --no-audit ) && c_ok "    ✅ 完成" || { c_err "    npm install 失敗"; exit 1; }
fi

# ---------- [4/4] 啟動 ----------
c_say "==> [4/4] 啟動服務"
# 清掉殘留進程，避免埠占用（Address already in use）
lsof -tiTCP:$DANMAKU_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -tiTCP:$CHAT_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

PASSWORD="$(tr -d '[:space:]' < "$ROOT/ui_password.txt" 2>/dev/null || echo 0425)"
PIDS=()
cleanup(){ echo ""; c_say "關閉服務中…"; for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

# 抖音弹幕：關閉「無瀏覽器自動退出」與「自動開瀏覽器」，改由本腳本統一管理
( cd "$ROOT" && DY_NO_AUTOCLOSE=1 DY_NO_BROWSER=1 "$VPY" web_danmaku.py ) &
PIDS+=($!)

# 聊天室：指向 web_danmaku 做訊息鏡像（DANMAKU_URL 空則不鏡像）
( cd "$CHATROOM" && PORT=$CHAT_PORT DANMAKU_URL="http://127.0.0.1:$DANMAKU_PORT" DANMAKU_PASSWORD="$PASSWORD" node server.js ) &
PIDS+=($!)

sleep 3
echo ""
echo "============================================================"
c_ok  "  ✅ 已啟動（Ctrl+C 結束兩個服務）"
echo  "  聊天室 chatroom  →  http://localhost:$CHAT_PORT"
echo  "  抖音弹幕 webUI   →  http://127.0.0.1:$DANMAKU_PORT   (密碼: $PASSWORD)"
echo  ""
echo  "  在 webUI 點右上『開啟聊天室』，或在 webUI 新增視窗選『聊天室』，"
echo  "  即可即時看到 chatroom 房間訊息。"
echo "============================================================"
wait
