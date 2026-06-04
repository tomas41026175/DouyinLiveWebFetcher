@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM 檢查並更新到最新版（只檢查＆更新，不啟動服務）。
REM 注意：本檔需為 CRLF 行尾（cmd.exe 要求）。

set "VERSION=unknown"
if exist "VERSION" set /p VERSION=<VERSION
echo ============================================================
echo   整合聊天室 更新檢查    目前版本 v%VERSION%
echo ============================================================

where git >nul 2>nul || (echo [錯誤] 找不到 git，請先安裝 Git for Windows ^& pause ^& exit /b 1)

git rev-parse --git-dir >nul 2>nul
if errorlevel 1 (
  echo [錯誤] 這個資料夾不是 git 倉庫，無法自動更新。
  echo        請重新用 git clone 取得專案。
  pause
  exit /b 1
)

echo 檢查遠端更新...
git fetch --quiet 2>nul
if errorlevel 1 (
  echo [警告] 無法連線遠端（git fetch 失敗），請檢查網路後再試。
  pause
  exit /b 1
)

set "LOCAL=" & set "REMOTE=" & set "BASE="
for /f %%h in ('git rev-parse @ 2^>nul') do set "LOCAL=%%h"
for /f %%h in ('git rev-parse "@{u}" 2^>nul') do set "REMOTE=%%h"
for /f %%h in ('git merge-base @ "@{u}" 2^>nul') do set "BASE=%%h"

if not defined REMOTE (
  echo [警告] 找不到遠端追蹤分支，無法比對更新。
  pause
  exit /b 1
)

if "!LOCAL!"=="!REMOTE!" (
  echo.
  echo   [OK] 已是最新版，無需更新。
  echo.
  pause
  exit /b 0
)

if not "!LOCAL!"=="!BASE!" (
  echo.
  echo   [警告] 本地有未推送的提交或與遠端分歧，無法用快進更新。
  echo          如要強制同步遠端，請手動處理（先 git stash 再 git pull）。
  echo.
  pause
  exit /b 1
)

echo 發現新版本，更新中 ^(git pull^)...
git pull --ff-only --quiet 2>nul
if errorlevel 1 (
  echo.
  echo   [警告] 更新失敗（本地可能有改動）。可手動執行： git stash ^&^& git pull
  echo.
  pause
  exit /b 1
)

set "NEWVER=unknown"
if exist "VERSION" set /p NEWVER=<VERSION
echo.
echo   [OK] 已更新： v%VERSION%  -^>  v!NEWVER!
echo        重新執行 start_all.bat 即可使用新版。
echo.
pause
endlocal
