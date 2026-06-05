# 雲端部署（整份上雲）

整份專案（web_danmaku 抖音彈幕 + chatroom 聊天室）部署到海外 VPS，24/7 運行、本機免開、中國朋友直連 VPS IP。

> 為什麼上雲：trycloudflare 隧道域名在中國被 GFW 污染（NXDOMAIN）；自有 VPS IP 不在黑名單，且本機不必常開。

## VPS 規格

- 國際商（**非**阿里雲/騰訊雲，那些要實名）：Vultr 東京 / 搬瓦工 等
- 1 vCPU / **2GB**（web_danmaku 的 V8 引擎 mini_racer 吃記憶體，1GB 會 OOM）
- Ubuntu 24.04 LTS

## 架構

```
VPS (東京) 24/7
  web_danmaku.py :8765（本機綁 127.0.0.1）  抓抖音彈幕
       │ SSE（同機 localhost）
       ▼
  chatroom :80  公網 → 中國朋友直連 http://VPS_IP/?room=123456
       · 雙向鏡像（同機 localhost 互通）
       · 貼圖 CRUD（/admin）
```

## 部署步驟

### 1. 系統依賴
```bash
apt-get update
apt-get install -y nodejs npm python3.12-venv python3-pip
```

### 2. chatroom（聊天室，Node）
```bash
# 上傳 chatroom/ 到 /root/chatroom，然後：
cd /root/chatroom && npm install --omit=dev
```
systemd `/etc/systemd/system/chatroom.service`：
```ini
[Unit]
After=network.target danmaku.service
[Service]
WorkingDirectory=/root/chatroom
Environment=PORT=80
Environment=STICKER_ADMIN_PASSWORD=<貼圖管理密碼初始值>
Environment=STICKER_PW_FILE=/root/.sticker_admin_pw
Environment=DANMAKU_URL=http://127.0.0.1:8765
Environment=DANMAKU_PASSWORD=<= web_danmaku 的 ui_password.txt 值>
ExecStart=/usr/bin/node /root/chatroom/server.js
Restart=always
[Install]
WantedBy=multi-user.target
```

### 3. web_danmaku（抖音彈幕，Python）
```bash
# 上傳 web_danmaku.py + liveMan.py + 簽名 js(a_bogus/sign/sign_v0/webmssdk) +
#   ac_signature.py + protobuf/ + .env(LIVE_ID) + gift_names.json + ui_password.txt + requirements.txt
cd /root/danmaku && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```
systemd `/etc/systemd/system/danmaku.service`：
```ini
[Unit]
After=network.target
[Service]
WorkingDirectory=/root/danmaku
Environment=DY_NO_AUTOCLOSE=1
Environment=DY_NO_BROWSER=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/root/danmaku/venv/bin/python web_danmaku.py
Restart=always
[Install]
WantedBy=multi-user.target
```

### 4. 啟用
```bash
systemctl daemon-reload
systemctl enable --now danmaku chatroom
```

## 踩坑（重要）

1. **`mini_racer`(PyPI) 的 import 名是 `py_mini_racer`**（不是 mini_racer）；py3.12 用 0.14.x。
2. **web_danmaku.py 依賴 `liveMan.py`**（簽名核心 `from py_mini_racer import MiniRacer`）— 打包別漏。
3. **抖音可從日本 VPS IP 抓**（實測 WebSocket 連上、未被風控）。
4. Ubuntu 24.04 預設無 `python3.12-venv`，要先 apt 裝。
5. systemd 寫 service 檔務必確保完整 flush（用 sftp 要 close），否則 Environment 沒生效。
6. PyExecJS(`execjs`) 需要 node runtime（chatroom 也裝了 node，共用）。

## 密碼

- root：已關密碼登入改用 SSH key；隨機密碼存 `/root/.rootpw`（或 Vultr 後台 reset）
- 貼圖管理：`/root/.sticker_admin_pw`（= chatroom 的 STICKER_ADMIN_PASSWORD）
- web_danmaku webUI：`ui_password.txt`（= chatroom 的 DANMAKU_PASSWORD）
