@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  Finish installing dependencies (bypass broken pip cache)
REM  Run this inside C:\dy\DouyinLiveWebFetcher
REM ============================================================

cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] venv not found here. Put this file inside
    echo         C:\dy\DouyinLiveWebFetcher and run again.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo.
echo [1/2] Purging pip cache (ignore errors if any)...
python -m pip cache purge 2>nul

echo.
echo [2/2] Installing dependencies without cache...
if exist "requirements.txt" (
    python -m pip install --no-cache-dir -r requirements.txt
) else (
    python -m pip install --no-cache-dir requests==2.31.0 betterproto==2.0.0b6 websocket-client==1.7.0 PyExecJS==1.5.1 mini_racer==0.12.4
)

echo.
echo ============================================================
echo   Verifying mini_racer...
echo ============================================================
python -c "from py_mini_racer import MiniRacer; MiniRacer().eval('1+1'); print('mini_racer OK')"

echo.
echo  If you see "mini_racer OK" above, you are ready.
echo  Now double-click start.bat in this folder.
echo ============================================================
echo.
pause
endlocal
