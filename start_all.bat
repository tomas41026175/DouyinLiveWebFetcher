@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 一鍵啟動（Windows）：檢測/安裝環境 → 啟動 web_danmaku(本機) + chatroom，
REM 聊天室透過 Cloudflare 取得公網網址分享給朋友（不走 localhost）。
REM 注意：本檔需為 CRLF 行尾（cmd.exe 要求）。

set "DANMAKU_PORT=8765"
set "CHAT_PORT=3000"
set "CF_LOG=.cf_tunnel.log"
set "CHAT_URL_FILE=chatroom_url.txt"

echo ==^> [1/5] 檢測 Python / Node / cloudflared
where python >nul 2>nul || (echo [錯誤] 找不到 python，請安裝 Python 3.x 並加入 PATH & pause & exit /b 1)
where node   >nul 2>nul || (echo [錯誤] 找不到 node，請安裝 Node.js 18+ & pause & exit /b 1)
where cloudflared >nul 2>nul
if errorlevel 1 (
  echo     未裝 cloudflared，嘗試以 winget 安裝...
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
  REM winget 裝完，當前 session PATH 不會即時更新，補上 WinGet Links 後再找
  set "PATH=!PATH!;!LOCALAPPDATA!\Microsoft\WinGet\Links"
  where cloudflared >nul 2>nul || (echo [提示] cloudflared 可能已安裝但本視窗 PATH 未更新，請關閉本視窗重新執行 start_all.bat & pause & exit /b 1)
)

echo ==^> [2/5] Python venv + 依賴
if not exist "venv\Scripts\python.exe" ( python -m venv venv )
set "VPY=venv\Scripts\python.exe"
"%VPY%" -c "import websocket, betterproto, py_mini_racer, execjs, requests" 2>nul
if errorlevel 1 (
  echo     安裝 Python 依賴（首次較久，含下載 V8 引擎）...
  "%VPY%" -m pip install -q --upgrade pip
  "%VPY%" -m pip install -q -r requirements.txt
  if errorlevel 1 echo     [警告] 部分 Python 依賴安裝失敗：抖音抓取可能不可用，聊天室仍可運作
)

echo ==^> [3/5] chatroom (Node) 依賴
if not exist "chatroom\node_modules" (
  pushd chatroom
  call npm install --no-fund --no-audit
  if errorlevel 1 ( echo [錯誤] npm install 失敗 & popd & pause & exit /b 1 )
  popd
)

echo ==^> [4/5] 啟動 web_danmaku + chatroom（本機）
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%DANMAKU_PORT% " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%CHAT_PORT% " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>nul
if exist "%CHAT_URL_FILE%" del "%CHAT_URL_FILE%"
if exist "%CF_LOG%" del "%CF_LOG%"

set "PW=0425"
if exist "ui_password.txt" set /p PW=<ui_password.txt
set "DY_NO_AUTOCLOSE=1"
set "DY_NO_BROWSER=1"
start "抖音弹幕 web_danmaku (勿關)" cmd /k ""%VPY%" web_danmaku.py"

set "PORT=%CHAT_PORT%"
set "DANMAKU_URL=http://127.0.0.1:%DANMAKU_PORT%"
set "DANMAKU_PASSWORD=%PW%"
start "聊天室 chatroom (勿關)" cmd /k "node chatroom\server.js"

echo ==^> [5/5] 建立 Cloudflare 隧道（聊天室公網網址）
REM 重定向整段用引號包成 cmd /k 的單一命令，讓 ^> 綁在 cloudflared 而非外層 cmd；用相對路徑避開引號地獄
start "Cloudflare 隧道 (勿關)" cmd /k "cloudflared tunnel --url http://localhost:%CHAT_PORT% > .cf_tunnel.log 2>&1"

echo     等待公網網址...
set "PUBLIC_URL="
for /l %%i in (1,1,30) do (
  if not defined PUBLIC_URL (
    for /f "usebackq delims=" %%u in (`powershell -NoProfile -Command "$t=Get-Content -Raw -ErrorAction SilentlyContinue '%CF_LOG%'; if($t -match 'https://[a-z0-9-]+\.trycloudflare\.com'){$matches[0]}"`) do set "PUBLIC_URL=%%u"
    if not defined PUBLIC_URL timeout /t 1 >nul
  )
)
if defined PUBLIC_URL (>"%CHAT_URL_FILE%" echo|set /p="!PUBLIC_URL!")

echo.
echo ============================================================
if defined PUBLIC_URL (
  echo   聊天室（分享給朋友這個網址，中國可連^)：
  echo        !PUBLIC_URL!
) else (
  echo   [警告] 未取得 Cloudflare 網址，看 %CF_LOG%；本機自測 http://localhost:%CHAT_PORT%
)
echo   抖音弹幕 webUI（本機自己看^): http://127.0.0.1:%DANMAKU_PORT%  ^(密碼 %PW%^)
echo.
echo   關閉彈出的三個視窗即可停止對應服務
echo ============================================================
pause
endlocal
