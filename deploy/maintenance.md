# 維運手冊（day-2 operations）

整份專案已部署到海外 VPS，24/7 運行。本文件是**日常維護**手冊（服務管理、更新、改設定、故障排查、備份、續費）。
首次部署步驟見 [cloud-deploy.md](./cloud-deploy.md)；整合架構見 [../docs/INTEGRATION.md](../docs/INTEGRATION.md)。

---

## 速查

| 我要… | 指令 |
|------|------|
| 看服務是否在跑 | `ssh root@167.179.84.87 "systemctl is-active chatroom danmaku"` |
| 重啟聊天室 | `ssh root@167.179.84.87 "systemctl restart chatroom"` |
| 重啟抖音彈幕 | `ssh root@167.179.84.87 "systemctl restart danmaku"` |
| 看彈幕即時 log | `ssh root@167.179.84.87 "journalctl -u danmaku -f"` |
| 看聊天室 log | `ssh root@167.179.84.87 "journalctl -u chatroom -n 50"` |
| 看線上版本 | `curl http://167.179.84.87/version` |
| 更新程式 | `git push origin main`（自動部署，約 20 秒） |
| 換抖音直播間 | 改 `/root/danmaku/.env` 的 `LIVE_ID` → 重啟 danmaku（見下） |
| 改貼圖管理密碼 | 開 `http://167.179.84.87/admin` → 登入 → 🔑 修改密碼 |
| 健康檢查 | `curl http://167.179.84.87/version` + `free -h` + `df -h` |

> SSH：本機 `~/.ssh/id_ed25519`（已停用 root 密碼登入，僅 key）。

---

## 系統架構（現況）

```
中國朋友瀏覽器
   |
   v
VPS 167.179.84.87  (Vultr 東京 / Ubuntu 24.04 / 1vCPU 2GB)  24/7
   |
   +--> chatroom.service   :80   (Node v18)   公網入口
   |       WorkingDir /root/chatroom
   |       +--> /              聊天室（房間碼進房）
   |       +--> /admin         貼圖管理（登入式）
   |       +--> 訂閱彈幕 SSE  <----------+
   |       +--> 房間訊息 POST ---------->|  同機 localhost 雙向鏡像
   |                                     |
   +--> danmaku.service     :8765 (127.0.0.1, Python 3.12)
           WorkingDir /root/danmaku
           +--> 抓抖音直播間（.env 的 LIVE_ID）
           +--> /stream SSE 彈幕 -------+
           +--> webUI（你自己看，需密碼 ui_password.txt）
```

| 元件 | 服務名 | Port | 路徑 | 對外 |
|------|--------|------|------|------|
| 聊天室 | `chatroom` | 80 | `/root/chatroom` | ✅ 公網 |
| 抖音彈幕 | `danmaku` | 8765 | `/root/danmaku` | ❌ 僅 127.0.0.1 |

---

## 服務管理

兩個服務都用 systemd 管理，`Restart=always`（掛了自動拉起）。

```bash
# 狀態
systemctl status chatroom        # 詳細狀態
systemctl is-active chatroom danmaku   # 只看 active/inactive

# 重啟 / 停 / 啟
systemctl restart chatroom       # 改完 chatroom 程式或設定後
systemctl restart danmaku        # 改完 LIVE_ID 或抖音斷線後
systemctl stop chatroom
systemctl start chatroom

# Log（systemd journal）
journalctl -u danmaku -f         # 即時跟（看抖音收訊息 / 斷線）
journalctl -u chatroom -n 100    # 最近 100 行
journalctl -u danmaku --since "10 min ago"
```

> `danmaku` 正常時 log 會每 10 秒印一行 `[诊断] 累计消息：…`（代表 WebSocket 連著、有在收）。

---

## 更新部署

### 自動（預設）

push 到 `main` → GitHub Actions 自動 rsync + 重啟，約 20 秒。

```bash
git push origin main
# 看部署進度：
gh run list --limit 1
gh run watch <run-id>
```

部署範圍（見 `.github/workflows/deploy.yml`）：
- `chatroom/`：rsync `--delete`，**排除** `node_modules`、`stickers`（保留 VPS 上的貼圖）
- `web_danmaku.py` + `liveMan.py` + 簽名 js + `protobuf/` 等：rsync（**保留** VPS 的 `.env`、`venv`、`ui_password.txt`）

### 手動 fallback（Actions 壞掉時，從本機）

```bash
# chatroom
rsync -az --delete --exclude node_modules --exclude stickers \
  chatroom/ root@167.179.84.87:/root/chatroom/
ssh root@167.179.84.87 "cd /root/chatroom && npm install --omit=dev && systemctl restart chatroom"

# web_danmaku
rsync -az web_danmaku.py liveMan.py a_bogus.js sign.js sign_v0.js webmssdk.js \
  ac_signature.py gift_names.json requirements.txt protobuf \
  root@167.179.84.87:/root/danmaku/
ssh root@167.179.84.87 "systemctl restart danmaku"
```

---

## 日常維運操作

### 換抖音直播間（LIVE_ID）

`LIVE_ID` = 抖音直播網址 `live.douyin.com/` 後面那串數字。**只存在 VPS 的 `/root/danmaku/.env`（不進 git）**。

```bash
ssh root@167.179.84.87
nano /root/danmaku/.env          # 改 LIVE_ID=新的房間數字
systemctl restart danmaku
journalctl -u danmaku -f         # 確認重新連上（看到 [诊断] 累计消息 即成功）
```

> 目前 `LIVE_ID=923052122608`。換房間只動這裡，不用改程式、不用重新部署。

### 改貼圖管理密碼（/admin）

**建議走 UI**：`http://167.179.84.87/admin` → 登入 → 「🔑 修改管理密碼」。
密碼持久化在 `/root/.sticker_admin_pw`，重啟仍生效。

忘記密碼時直接重設檔案：
```bash
ssh root@167.179.84.87 'printf "新密碼" > /root/.sticker_admin_pw && systemctl restart chatroom'
```

### 改 webUI 密碼（你自己看彈幕用）

webUI 密碼要**同步改兩處**（否則 chatroom 抓不到彈幕）：
```bash
ssh root@167.179.84.87
# 1. web_danmaku 自己的登入密碼
printf "新密碼" > /root/danmaku/ui_password.txt
# 2. chatroom 連 danmaku 時帶的密碼（unit 的 DANMAKU_PASSWORD）
systemctl edit chatroom          # 加 [Service] Environment=DANMAKU_PASSWORD=新密碼
systemctl daemon-reload
systemctl restart danmaku chatroom
```

### 看版本

```bash
curl http://167.179.84.87/version
# {"version":"1.0.0","startedAt":"..."}  ← version 來自 chatroom/package.json
```
或開 `/admin`，標題旁顯示 `v1.0.0 · 部署 時間`。
要標新版本：改 `chatroom/package.json` 的 `version` → push（自動部署生效）。

### 貼圖管理

貼圖存在 VPS `/root/chatroom/stickers/`（**不進 git**、部署時 rsync 會保留）。
日常用 `/admin` 上傳/刪除即可（自動壓縮、支援批量）。

---

## 健康檢查 / 監控

```bash
ssh root@167.179.84.87 '
  systemctl is-active chatroom danmaku   # 兩個都要 active
  curl -s localhost:80/version           # chatroom 有回應
  curl -s -o /dev/null -w "%{http_code}" localhost:8765   # danmaku 活著
  free -h | grep Mem                     # 記憶體（mini_racer 吃 V8，盯這個）
  df -h /                                # 磁碟
  journalctl -u danmaku -n 3 --no-pager  # 抖音有在收訊息？
'
```

**正常基準**（2GB 機）：記憶體用量約 450MB、磁碟約 20%、danmaku log 每 10 秒一行診斷。

---

## 故障排查

| 症狀 | 診斷 | 解法 |
|------|------|------|
| 中國朋友連不上 | `curl http://167.179.84.87/version` 本地能通？ | 通→朋友網路/瀏覽器問題；不通→看 chatroom 服務 |
| 聊天室打不開 | `systemctl status chatroom` | 掛了：`journalctl -u chatroom -n 50` 看錯誤 → `systemctl restart chatroom` |
| 彈幕沒進聊天室 | `journalctl -u danmaku -f` 有無 `[诊断] 累计消息` | 沒有→見下方「抖音抓不到」 |
| 抖音抓不到 | log 有 `连接成功`？直播是否正在開？ | ①直播沒開→等開播 ②`LIVE_ID` 錯→改 .env ③簽名失效→更新 repo 簽名 js 重部署 |
| Actions 部署失敗 | `gh run view <id> --log-failed` | 多半是 `VPS_HOST`/`VPS_SSH_KEY` secret（檢查無換行）；可改用手動 fallback |
| 記憶體不足 / OOM | `free -h`、`journalctl -k | grep -i oom` | 重啟 danmaku 釋放；長期→升 VPS 規格（mini_racer V8 吃記憶體，故選 2GB） |
| 貼圖上傳失敗 | `/admin` 是否已登入？檔案 ≤5MB？ | 重新登入；超大圖前端會壓縮，仍失敗看 chatroom log |

### 抖音斷線自動恢復

`danmaku.service` 是 `Restart=always`，程式崩潰會自動拉起。但「WebSocket 被抖音斷開但程式沒崩」時不會自動重連——觀察 log 不再印診斷就手動 `systemctl restart danmaku`。

---

## 備份與恢復

### 要備份的（都不在 git）

| 內容 | 路徑 |
|------|------|
| 貼圖 | `/root/chatroom/stickers/` |
| 貼圖管理密碼 | `/root/.sticker_admin_pw` |
| webUI 密碼 | `/root/danmaku/ui_password.txt` |
| 抖音房間設定 | `/root/danmaku/.env`（LIVE_ID） |

### 備份指令（拉到本機）

```bash
mkdir -p ~/vps-backup
scp -r root@167.179.84.87:/root/chatroom/stickers ~/vps-backup/
scp root@167.179.84.87:/root/.sticker_admin_pw ~/vps-backup/
scp root@167.179.84.87:/root/danmaku/ui_password.txt ~/vps-backup/
scp root@167.179.84.87:/root/danmaku/.env ~/vps-backup/
```

### 換新 VPS 重建

1. 照 [cloud-deploy.md](./cloud-deploy.md) 裝系統依賴 + 兩個 systemd 服務
2. push main 觸發 Actions 部署程式（或手動 fallback），**記得在 repo 改 `VPS_HOST` secret 為新 IP**
3. 把上面備份的貼圖 / 密碼檔 / .env 放回原路徑
4. `systemctl restart danmaku chatroom`

---

## 安全

- **SSH**：已停用 root 密碼登入，僅 key（本機 `~/.ssh/id_ed25519`）。root 隨機密碼存 `/root/.rootpw`（或 Vultr 後台 reset）。
- **密碼檔權限**：`chmod 600 /root/.sticker_admin_pw /root/danmaku/ui_password.txt`
- **不進 git 的敏感檔**：`.env`、`ui_password.txt`、`.sticker_admin_pw`、`node_modules/`、`venv/`、`stickers/`
- **GitHub secrets**：`VPS_HOST`、`VPS_SSH_KEY`（部署用 deploy key，與本機 key 可不同）
- danmaku 只綁 `127.0.0.1`，不對公網開放（只有同機 chatroom 能連）。

---

## 成本與續費

- Vultr 東京 vc2-1c-2gb：約 **$10 / 月**（未開 Auto Backups）
- 續費：Vultr 後台自動扣款；餘額不足會暫停 → 留意帳戶餘額 / 信用卡有效期
- 規格夠用：記憶體常駐約 450MB（含 5.3GB swap 緩衝），磁碟 20%。流量小（純文字 + 小貼圖）。
- 若同時開多房間或加重抓取 → 再評估升 4GB。

---

## 附錄：路徑與環境變數對照

| 環境變數 | 服務 | 值 / 來源 |
|----------|------|-----------|
| `PORT=80` | chatroom | 公網埠 |
| `STICKER_ADMIN_PASSWORD` | chatroom | 初始值，之後以 `STICKER_PW_FILE` 為準 |
| `STICKER_PW_FILE=/root/.sticker_admin_pw` | chatroom | 貼圖密碼持久化 |
| `DANMAKU_URL=http://127.0.0.1:8765` | chatroom | 連 danmaku |
| `DANMAKU_PASSWORD` | chatroom | = danmaku 的 `ui_password.txt` |
| `LIVE_ID` | danmaku | `.env`，抖音房間數字 |
| `DY_NO_AUTOCLOSE=1` | danmaku | 不自動關閉（伺服器常駐必要） |
| `DY_NO_BROWSER=1` | danmaku | 不開瀏覽器 |
| `PYTHONUNBUFFERED=1` | danmaku | log 即時 flush |
