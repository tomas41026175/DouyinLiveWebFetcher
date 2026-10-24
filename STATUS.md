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

- 2026-10-24：補上 `CHANGELOG.md`、`docs/adr/0001-architecture.md` 與「回滾」段落
- 2026-09-24：v1.3.0 — `config.json` 啟動設定、今日音浪總計、可關閉密碼驗證（#21、#22）

## 下一步

1. 決定 VPS 部署是否恢復；不恢復的話把 README 的 VPS 段落標為停用
2. 若要納入監控：接 Healthchecks（見 workbench 擴充契約）

## 已知問題

- README 仍以 VPS 部署為主要情境，與目前只跑本機不一致

## 回滾

本機（目前唯一的部署方式）：

1. 先停掉服務（`start_all.sh` 視窗按 Ctrl+C；Windows 關掉 `start_all.bat` 開的視窗）。
2. `git checkout <要回去的 commit>`（detached HEAD）。注意：`start_all.sh`／`start_all.bat` 啟動時會在「落後上游且可 fast-forward」時自動
   `git pull --ff-only`，若只是把 main `reset` 回舊版，下次啟動會被拉回新版；detached HEAD 沒有上游，不會觸發自動更新。
   要讓回滾長期有效，改用 `git revert <壞的 commit>` 推上 main。
3. 重新執行 `start_all.sh`（或 `start_all.bat`）。腳本只在缺 `chatroom/node_modules` 時才裝依賴；回到的版本 `package.json` 不同時，先 `cd chatroom && npm install`。
4. 設定回滾：`config.json` 在 git 內，`git checkout <commit> -- config.json` 即可。

VPS（暫停中）：依 `deploy/maintenance.md` 的更新流程，在 VPS 上 checkout 舊 commit 後 `systemctl restart chatroom danmaku`。待確認：VPS 恢復時再驗證這段。

## 決策紀錄

- 架構彙整：`docs/adr/0001-architecture.md`；變更紀錄：`CHANGELOG.md`
- `dev-log/`、`docs/`
