@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  DouyinLiveWebFetcher - Quick start
REM ============================================================

cd /d "%~dp0"

REM ---- locate the repo folder (where liveMan.py lives) ----
if exist "liveMan.py" goto found
if exist "DouyinLiveWebFetcher\liveMan.py" (
    cd /d "%~dp0DouyinLiveWebFetcher"
    goto found
)
echo [ERROR] Cannot find liveMan.py
echo         Put start.bat ^(and .env^) inside the DouyinLiveWebFetcher
echo         folder, then run again.
echo.
pause
exit /b 1

:found

REM ---- make sure .env exists ----
if exist ".env" goto readenv
> ".env" echo # DouyinLiveWebFetcher config
>> ".env" echo # Room id = digits after live.douyin.com/
>> ".env" echo LIVE_ID=

:readenv
REM ---- read LIVE_ID from .env ----
set "LIVE_ID="
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="LIVE_ID" set "LIVE_ID=%%B"
)
REM strip leading spaces
for /f "tokens=* delims= " %%i in ("!LIVE_ID!") do set "LIVE_ID=%%i"

if not "!LIVE_ID!"=="" goto run

REM ---- ask if empty, then save back ----
echo.
set /p "LIVE_ID=Enter Douyin room id (digits after live.douyin.com/): "
if "!LIVE_ID!"=="" (
    echo [ERROR] No room id provided. Exiting.
    pause
    exit /b 1
)
> ".env" echo # DouyinLiveWebFetcher config
>> ".env" echo # Room id = digits after live.douyin.com/
>> ".env" echo LIVE_ID=!LIVE_ID!

:run
REM ---- activate venv if present ----
if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat

echo.
echo ============================================================
echo   Connecting to room: !LIVE_ID!
echo   (Press Ctrl+C to stop)
echo ============================================================
echo.

python -c "from liveMan import DouyinLiveWebFetcher; DouyinLiveWebFetcher('!LIVE_ID!').start()"

echo.
echo ============================================================
echo   Stopped. (window stays open)
echo ============================================================
pause
endlocal
