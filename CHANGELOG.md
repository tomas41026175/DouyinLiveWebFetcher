# 變更紀錄

DouyinLiveWebFetcher（抖音彈幕抓取 + 多人聊天室整合）的變更紀錄，格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)。
版本號以根目錄 `VERSION` 為準；聊天室本身 v1.0–v1.2 的細節見 [docs/chatroom/CHANGELOG.md](docs/chatroom/CHANGELOG.md)。
沒有版本 tag，以下依 PR 合併日期整理；1.3.0 以前的 `VERSION` 對應待確認。

## 2026-10-23

### 文件
- 新增 `STATUS.md` 與 status-gate 閘門（#23）

## [1.3.0] — 2026-09-24

### 新增
- `config.json` 啟動設定（啟動項目、房號、密碼驗證開關）、今日音浪總計、可關閉密碼驗證（#21）

### 測試
- web_danmaku 單元測試：音浪統計、連擊去重、密碼驗證開關（#22）

## 2026-06-08

### 新增
- 恢復 `start_all.sh` 的 trycloudflare 隧道（#19）

### 修正
- 直播彈幕貼圖訊息自動捲動失效（#20）

## 2026-06-05

### 新增
- chatroom 整份上雲（中國可連）、貼圖 CRUD／壓縮、改暱稱、對話快取（#2）
- 放寬貼圖上傳（無副檔名以 MIME 判斷）、批量拖拉、admin 登入式（#3）
- 聊天室分類彈幕顯示貼圖 + 批量上傳提示（#4）
- admin 顯示版本號（git sha + 部署時間）（#7）；chatroom 版本號改用 package.json version（#8）
- chatroom 字體大小控制 A−／A+（#13）、即時流量與延遲查看（#16）
- web_danmaku webUI 多聊天室監看牆（#17）、資料來源切換（本機⇄VPS）+ :8765 對外（#18）

### 效能
- chatroom 靜態資源 gzip（#12）、靜態與貼圖快取（#14）、QR lib lazy load（#15）

### 修正
- 無副檔名選圖誤判、html no-cache、Actions 自動部署（#5）；自動部署 host 換行（#6）

### 文件／CI
- VPS 維運手冊 + 修正過時整合文件（#9）；純文件變更不觸發部署（#10、#11）

## 2026-06-04

### 新增
- chatroom 分享 QR、彈幕分類過濾、貼圖功能 + 啟動／更新腳本（#1）
