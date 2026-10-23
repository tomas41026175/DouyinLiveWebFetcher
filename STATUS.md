# STATUS — DouyinLiveWebFetcher

> 抖音直播彈幕抓取 + 多人聊天室（雙向鏡像、貼圖、今日音浪）。

| 欄位 | 值 |
|---|---|
| 狀態 | active（VPS 部署暫停，只跑本機） |
| 部署位置 | 本機（`start_all.sh` / `start_all.bat`）；VPS 已停用，`deploy.yml` 的 push 自動部署已註解 |
| 部署版本 | `VERSION` 1.3.0 |
| 最後驗證可用 | 待確認 |
| 資料位置 | `config.json`（啟動設定）、`chatroom/` 貼圖（入 git） |
| 監控 | 無 |
| 相關 repo | `workbench`（總覽） |

## 目前進度

- 2026-09-24：v1.3.0 — `config.json` 啟動設定、今日音浪總計、可關閉密碼驗證（#21、#22）

## 下一步

1. 決定 VPS 部署是否恢復；不恢復的話把 README 的 VPS 段落標為停用
2. 若要納入監控：接 Healthchecks（見 workbench 擴充契約）

## 已知問題

- README 仍以 VPS 部署為主要情境，與目前只跑本機不一致

## 決策紀錄

- `dev-log/`、`docs/`
