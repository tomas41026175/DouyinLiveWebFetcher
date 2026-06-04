# 悄悄話 · 小型群聊室

房間碼連線、WebSocket 中轉，**中國大陸可用**。每房 2–10 人（建房時可設），進房前可填暱稱。支援固定房號、房主鎖房、深色模式。Web UI，後端 Node.js 跑在 Mac / Windows / Linux 皆可。

- 規格：[`SPEC.md`](./SPEC.md)
- 部署規格（含三條路線）：[`DEPLOY.md`](./DEPLOY.md)

---

## 本機跑起來（Mac / Windows 指令相同）

```bash
npm install
npm start          # 開 http://localhost:3000
```

或一鍵啟動（含 Cloudflare 公網隧道，Mac/Linux）：

```bash
./start.sh         # 自動起 server + 隧道，印出可分享的 https 網址
```

自測：開一個普通視窗建房 + 一或多個無痕視窗輸碼加入。建房前可在「進階設定」調房間人數、切深色模式。

> 版本演進見 [CHANGELOG.md](./CHANGELOG.md)。

跑測試：
```bash
PORT=3999 node server.js &   # 另開終端
PORT=3999 node test.mjs      # 9 項中轉邏輯測試
```

---

## 部署到中國大陸（你的路線：香港節點 · 免備案）

> 你目前什麼都沒有，以下是從零開始的採購 + 部署清單。
> 選香港節點的原因：**免 ICP 備案，當天可上**；代價是延遲 +30~80ms（聊天可接受）。

### Step 0 — 採購清單（約半天搞定）

| 項目 | 去哪買 | 規格 | 約略費用 |
|------|--------|------|----------|
| 香港 VPS | **阿里雲免費試用（個人版）→ 選香港地域** | 最高 4 核 8G，3 個月，¥300 額度 | **0（試用期）** |
| 域名 | 阿里雲 / Cloudflare / Namecheap | 任意 `.com` / `.xyz` | 約 ¥50/年 |
| TLS 憑證 | Let's Encrypt（certbot） | 免費 | 0 |

#### 阿里雲免費方案（你要的）
- **個人版免費試用**：完成個人實名認證，¥300 額度、有效 **3 個月**，提供 8 種規格（最高 4 核 8G），**涵蓋 7 個地域含香港**。
- 香港地域每月 **200GB** 流量配額（內地地域只有 20GB），聊天室用量極小，綽綽有餘。
- 學生：學信網認證可領 ¥300 無門檻券（用於輕量伺服器 / ECS 入門規格）。

> ⚠️ **免費試用三個坑**（踩到會扣費）：
> 1. **3 個月到期後會自動轉收費**，到期前要記得退訂或評估續費。
> 2. 不要**變更實例類型 / 掛資料盤 / 把計費改成固定帶寬**——這些動作脫離免費範圍。
> 3. 香港節點免備案，但**域名仍要錢**（約 ¥50/年）且要解析到 IP；wss:// 憑證用 Let's Encrypt 免費。
>
> 長期免費替代：若 3 個月後不想付費，**Oracle Cloud Always Free**（東京/大阪/新加坡節點，永久免費 VM）也免備案、更適合長期白嫖，代價是申請門檻較高、偶爾缺貨。

### Step 1 — 伺服器環境

```bash
# SSH 登入後
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs nginx
sudo npm install -g pm2

# 上傳專案（或 git clone），進專案目錄
npm install --production
pm2 start server.js --name chatroom
pm2 save && pm2 startup     # 開機自啟（照終端提示再跑一行）
```

### Step 2 — 域名解析

到域名商後台，加一筆 A 紀錄：`your-domain.com` → 伺服器公網 IP。

### Step 3 — Nginx 反向代理（WebSocket 關鍵）

`/etc/nginx/sites-available/chatroom`：
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;

        # --- WebSocket 必備三行，少一行 wss 連不上 ---
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host       $host;
        # ------------------------------------------------

        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 600s;   # 長連線拉長逾時，避免被切
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/chatroom /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Step 4 — HTTPS（certbot 一鍵）

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com   # 自動改 Nginx 設定 + 申請憑證 + 90 天自動續期
```

完成後開 `https://your-domain.com`，前端會自動用 `wss://` 連線（程式碼依協議自動切換，無需改）。

### Step 5 — 防火牆 / 安全組

```bash
sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable
# 雲廠商後台安全組同步只開 80/443；切勿對外開 3000（Node 只聽 127.0.0.1）
```

---

## 上線檢查清單

```
[ ] 域名 A 紀錄已指向伺服器 IP
[ ] https:// 開啟無憑證警告
[ ] Nginx WebSocket Upgrade 三行已設定
[ ] proxy_read_timeout 已拉長
[ ] PM2 常駐 + 開機自啟（pm2 list 看得到 chatroom online）
[ ] 3000 埠未對外（只開 80/443）
[ ] 手機 4G/5G 實測（不只 WiFi）
[ ] 兩台不同網路裝置實測房號配對
```

---

## 專案結構

```
chatroom/
├── server.js          HTTP 靜態服務 + WebSocket 中轉 + 心跳清殭屍連線
├── package.json
├── test.mjs           9 項中轉邏輯測試
├── public/
│   └── index.html     單檔聊天界面（暖色紙感 · 系統字體 · ws/wss 自動切換 · 自動重連）
├── SPEC.md
├── DEPLOY.md
└── README.md
```

## 已知限制（v1）

- 無訊息持久化：伺服器重啟 / 重整頁面，歷史清空
- 訊息經伺服器明文中轉（隱私要求高需上 v2 端到端加密）
- 房間人數 2–10（建房時設定）；無暱稱驗證（同名會自動補 (2)）
