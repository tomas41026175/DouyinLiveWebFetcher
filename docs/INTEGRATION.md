# chatroom × 抖音彈幕 整合說明

把聊天室 `chatroom/` 整合進本抖音彈幕專案，**雙向互通**。
生產環境整份部署在海外 VPS（24/7、中國可直連）；本機可用 `start_all.sh` 開發測試。

- 首次部署：[../deploy/cloud-deploy.md](../deploy/cloud-deploy.md)
- 日常維運（重啟 / 更新 / 改設定 / 故障排查 / 備份）：[../deploy/maintenance.md](../deploy/maintenance.md)

## 架構（雙向資料流）

```
                抖音指定直播間（VPS /root/danmaku/.env 的 LIVE_ID）
                      │ 簽名 + protobuf
                      ▼
        ┌────────────────────────────────────┐
        │  web_danmaku.py  (VPS 127.0.0.1:8765) │
        │  · webUI 看彈幕 + 「聊天室」視窗        │◄── chatroom 房間訊息 POST /chat/ingest
        │  · 只綁 localhost，不對公網開放         │
        └────────────────────────────────────┘
                      │ /stream SSE（彈幕）
                      ▼
        ┌────────────────────────────────────┐
        │  chatroom  (VPS :80 公網)             │
        │  · 訂閱彈幕注入房間上方彈幕流           │
        │  · 房間訊息 / 貼圖鏡像回 webUI          │
        │  · /admin 貼圖管理                     │
        └────────────────────────────────────┘
                      │
                      ▼
        中國朋友直連 http://167.179.84.87/?room=123456
```

- **chatroom 對外（:80）**：朋友連 VPS IP 直接進房，中國可連（自有 IP 不被 GFW 污染）。
- **web_danmaku 留 localhost（:8765）**：彈幕主控台，只有同機 chatroom 能連，不對公網開放。
- **雙向鏡像走同機 localhost**：彈幕 → 聊天室（SSE）；房間訊息 / 貼圖 → webUI（POST `/chat/ingest`）。

> 為什麼上雲：原本用 trycloudflare 隧道分享，但隧道域名在中國被 GFW 污染（NXDOMAIN）。改用自有 VPS IP 後中國可直連，且本機不必常開。

## 本機開發測試

| 平台 | 指令 |
|------|------|
| **mac / Linux** | `./start_all.sh` |
| **Windows** | 雙擊 `start_all.bat` |

腳本會：檢測並安裝環境（Python venv + 依賴、Node 依賴）→ 啟動兩服務（**僅 localhost，不對外穿透**）→ 自動開兩個瀏覽器分頁。

```
抖音弹幕 webUI ：http://127.0.0.1:8765       (密碼見 ui_password.txt)
聊天室         ：http://localhost:3000/?room=123456
```

> 本機模式純供開發測試；要對外分享請用 VPS（生產）。

## 注意事項

- 首次本機啟動會建立 venv 並 `pip install`（含下載 V8 引擎，較久）。原 repo 的 `venv/` 是 Windows 版，mac 上腳本會自動重建。
- 依賴（`py_mini_racer` / `betterproto` 等）在 Python 3.12（VPS）/ 3.14（本機 Apple Silicon）皆實測可裝。
- 結束本機服務：mac `Ctrl+C`；Windows 關閉彈出視窗。
- 不進 git 的執行期 / 敏感檔：`venv/`、`node_modules/`、`ui_password.txt`、`.env`、`stats/`、`rooms.json`、`stickers/`。

> 獨立聊天室（不含抖音）見 `chatroom/`，可單獨 `cd chatroom && npm start`。
