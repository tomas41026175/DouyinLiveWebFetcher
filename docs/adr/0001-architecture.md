# 0001 — 目前架構：web_danmaku + chatroom 雙服務、本機啟動腳本自動更新、VPS 部署暫停

- 狀態：accepted
- 日期：2026-10-24（整理既有決策；細節以連結的原文件為準）
- 廢棄原因：—

## 背景

本 repo 以上游抖音網頁版彈幕抓取（簽名 + protobuf）為基礎，整合多人聊天室 `chatroom/`，讓朋友在聊天室看到直播彈幕並雙向互通
（`README.MD`、`docs/INTEGRATION.md`）。2026-06 曾整份部署到海外 VPS；目前 VPS 暫停，只在本機手動啟動（`STATUS.md`）。

這份 ADR 只彙整已存在的決策，不新增決策。

## 決策

1. **兩個服務、同機 localhost 雙向鏡像**：`web_danmaku.py`（:8765，彈幕主控台）把彈幕經 `/stream` SSE 給 chatroom；
   chatroom（本機 :3000）把房間訊息與貼圖 POST `/chat/ingest` 回 webUI（`docs/INTEGRATION.md` 架構）。
2. **分享方式**：本機用 trycloudflare 隧道取得公網網址（`start_all.sh`，#19 恢復）；VPS 用自有 IP，因為隧道域名曾被 GFW 污染
   （`docs/INTEGRATION.md`「為什麼上雲」）。
3. **VPS 部署暫停**：`deploy.yml` 的 push 自動部署已註解，VPS 手冊保留在 `deploy/cloud-deploy.md`、`deploy/maintenance.md`（`STATUS.md`）。
4. **啟動設定集中在 `config.json`**：啟動項目、房號、密碼驗證開關（v1.3.0，#21；`README.MD`「啟動設定」）。
5. **啟動腳本自帶更新**：`start_all.sh`／`start_all.bat` 啟動時若落後上游且可 fast-forward 就 `git pull --ff-only` 後重啟；`update.bat` 手動更新。
6. **聊天室設計**：功能取捨見 `docs/specs/2026-06-04-chatroom-features-design.md`、`docs/chatroom/SPEC.md`。

## 替代方案

| 方案 | 不選的原因 |
|---|---|
| 只用 trycloudflare 隧道分享 | 隧道域名在中國被 GFW 污染（NXDOMAIN），因此 2026-06 改 VPS（`docs/INTEGRATION.md`） |
| 維持 VPS 24/7 | 待確認：暫停的原因沒有文件記錄（`STATUS.md` 下一步 1：決定是否恢復） |

## 後果

- 沒有排程或常駐程式，不需要外部心跳；工作檯 registry 標記 Healthchecks「不適用」。若恢復 VPS 常駐，要重新評估監控（`STATUS.md` 下一步 2）。
- `README.MD` 仍以 VPS 為主要情境，與現況不一致（`STATUS.md` 已知問題）。
- 啟動自動更新代表 main 上的變更下次啟動就生效；回滾要注意（見 `STATUS.md`「回滾」）。
