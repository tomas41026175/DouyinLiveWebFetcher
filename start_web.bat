@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  Launch Douyin danmaku Web UI
REM  Put this inside C:\dy\DouyinLiveWebFetcher and double-click.
REM ============================================================

cd /d "%~dp0"

if not exist "liveMan.py" (
    echo [ERROR] liveMan.py not found here.
    echo         Put start_web.bat and web_danmaku.py inside the
    echo         DouyinLiveWebFetcher folder, then run again.
    pause
    exit /b 1
)

if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat

python web_danmaku.py

echo.
pause
endlocal
