# chatroom × 抖音彈幕 整合說明

把聊天室 `chatroom/` 整合進本抖音彈幕專案，**雙向互通**、一鍵啟動、跨平台（mac / Windows）。

## 一鍵啟動

| 平台 | 指令 |
|------|------|
| **mac / Linux** | `./start_all.sh` |
| **Windows** | 雙擊 `start_all.bat`（或在 cmd 執行） |

腳本會自動：檢測並安裝環境（Python venv + 依賴、Node 依賴、cloudflared）→ 啟動兩個服務 → 建立 Cloudflare 隧道 → **印出聊天室公網網址**。

啟動後得到：

```
聊天室（分享給朋友，中國可連）：https://xxxx.trycloudflare.com
抖音弹幕 webUI（本機自己看）   ：http://127.0.0.1:8765   (密碼見 ui_password.txt)
```

## 架構（雙向資料流）

```
                  抖音指定直播間（.env 的 LIVE_ID）
                        │ 簽名 + protobuf
                        ▼
          ┌──────────────────────────────┐
          │  web_danmaku.py  (本機 :8765)  │
          │  · webUI 看彈幕 + 「聊天室」視窗 │◄── chatroom 房間訊息 POST /chat/ingest
          │  · 「開啟聊天室」鈕→公網網址      │
          └──────────────────────────────┘
                        │ /stream SSE（彈幕）
                        ▼
          ┌──────────────────────────────┐
          │  chatroom  (本機 :3000)         │
          │  · 訂閱彈幕注入房間              │
          │  · 上彈幕 / 下對話 / 可拖拉占比   │
          └──────────────────────────────┘
                        │ cloudflared 隧道
                        ▼
                 公網 https 網址（分享給朋友）
```

- **聊天室統一走 Cloudflare 公網**（不用 localhost），方便分享、中國可連
- **web_danmaku 留本機**（你自己的彈幕主控台）

## 注意事項

- 首次啟動會建立 macOS/Windows venv 並 `pip install`（含下載 V8 引擎，較久）。原 repo 的 `venv/` 是 Windows 版，mac 上腳本會自動重建。
- 依賴（`mini_racer` / `betterproto` 等）在 Python 3.14 + Apple Silicon 已實測可裝。
- `cloudflared` 未裝時腳本會嘗試安裝（mac: `brew`，Windows: `winget`）。Windows 首次裝完可能需**關閉視窗重新執行**讓 PATH 生效。
- 結束服務：mac `Ctrl+C`；Windows 關閉彈出的三個視窗。
- 不會推上 git 的執行期 / 敏感檔：`venv/`、`node_modules/`、`ui_password.txt`、`.env`、`stats/`、`rooms.json`、`chatroom_url.txt`。

> 獨立聊天室（不含抖音）見 `chatroom/`，可單獨 `cd chatroom && npm start` 或用 `chatroom/start.sh`。
