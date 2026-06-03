# 小型群聊室（2–10 人）— 部署規格 (DEPLOY)

> 版本：v1.0 ｜ 日期：2026-06-03 ｜ 重點：讓中國大陸使用者可穩定連線

---

## 0. 先講結論

**部署到中國大陸的真正門檻不是技術，是 ICP 備案。**

| 路線 | 備案 | 成本 | 適合 |
|------|------|------|------|
| **0. Cloudflare 臨時隧道** | 免 | **0** | **私人 / 短期（一週級）← 你的情況** |
| A. 大陸節點 + 域名 | 必須備案（1–2 週） | 中 | 正式長期上線 |
| B. 香港/海外節點 + 域名 | 免備案 | 低 | 快速上線 / 過渡 |
| C. 大陸節點 + 純 IP | 灰色，易被阻斷 | 低 | 不建議 |

> 批判性提醒：很多「教學」只講 Nginx + Node.js，**跳過備案這關**。大陸伺服器若用域名走 80/443 而未備案，會被直接阻斷——技術全對也連不上。
> 但若只是**私人、短期使用**，根本不必碰伺服器 / 域名 / 備案 / 憑證——直接用方案 0。

---

## 方案 0：Cloudflare 臨時隧道（私人 / 一週用，推薦你的情況）

本機跑伺服器，Cloudflare 給你一個臨時 https 公網網址，對方瀏覽器打開即用。**免伺服器、免域名、免備案、免憑證、免登入、零成本。**

```
你的 Mac (npm start, localhost:3000)
     |
     |  cloudflared 反向隧道
     v
Cloudflare 海外邊緣 (自帶 https + wss)
     |
     +--> https://xxxx.trycloudflare.com   (把這網址 + 房號給對方)
              |
              +--> 對方瀏覽器（中國可連，走 Cloudflare 邊緣，免備案）
```

### 步驟（Mac）
```bash
# 1. 裝 cloudflared（一次性）
brew install cloudflared

# 2. 起聊天室
cd chatroom && npm install && npm start      # localhost:3000

# 3. 另開一個終端，開臨時隧道
cloudflared tunnel --url http://localhost:3000
# 會印出一個網址，例如：https://random-words-1234.trycloudflare.com
```

把那個 `https://...trycloudflare.com` 網址連同房號傳給對方即可。前端已內建 ws/wss 自動切換，https 網址會自動走 wss，無需改任何程式碼。

### 注意 / 取捨
- **Mac 要保持開機 + 兩個指令掛著**（npm start 與 cloudflared）。私人約時間聊很 OK。
- **臨時網址重啟會變**：每次重跑 cloudflared 換一組網址。一週內別關就行；要固定網址得申請 Cloudflare 帳號用 named tunnel（多一步，非必要）。
- **連通性**：trycloudflare 走 Cloudflare 海外邊緣，中國一般可連，偶有波動。若某時段不穩，退回方案 B（阿里雲香港免費試用）。
- 用完直接關掉兩個終端，**零殘留、零費用**。

> 為什麼不直接上阿里雲？對「一週、私人、幾個人用」，買 ECS + 域名 + 實名認證 + Nginx + certbot 是過度投資。方案 0 五分鐘搞定。阿里雲方案留給「之後想長期跑」時用（見 README）。

---

## 1. 部署架構

```
中國使用者 (瀏覽器)
     |
     |  https:// + wss://  (443)
     v
+------------------------------------------+
|  雲伺服器 (阿里雲 ECS / 騰訊雲 CVM /        |
|            香港節點)                       |
|                                          |
|   Nginx (443, TLS 終結 + 反向代理)         |
|     |                                    |
|     +--> 靜態檔 public/  (HTTP)           |
|     |                                    |
|     +--> /ws  --(Upgrade)--> Node.js     |
|                               :3000      |
|                                          |
|   Node.js (server.js, PM2 常駐)          |
+------------------------------------------+
     |
     +--> 域名 (已備案 / 香港免備案)
     +--> TLS 憑證 (Let's Encrypt 或雲廠商免費證書)
```

部署決策樹：
```
要在中國大陸用？
  |
  +--> 是, 且要正式長期
  |       |
  |       +--> 大陸 ECS + 域名 + ICP 備案 (路線 A)
  |
  +--> 是, 但要快/過渡/沒法備案
  |       |
  |       +--> 香港節點 + 域名 + 免備案 (路線 B)
  |
  +--> 只是內測/自己用
          |
          +--> 任意雲 + 域名 + HTTPS (備案視節點而定)
```

---

## 2. 路線 A：大陸節點正式上線（推薦長期）

### 2.1 前置（卡關點，先做）
1. 買域名（阿里雲 / 騰訊雲）
2. **ICP 備案**：
   - 個人或企業實名，需大陸身分證 / 營業執照
   - 走雲廠商備案系統，約 **7–20 個工作天**
   - 備案綁定該雲帳號下的伺服器，換雲商要重新備案
3. 買 ECS / CVM（最低 1 核 2G 即可，聊天室很輕）

### 2.2 伺服器環境
```bash
# Node.js LTS (用 nvm 或廠商鏡像)
node -v   # >= 18
npm install --production
npm install -g pm2

# 常駐
pm2 start server.js --name chatroom
pm2 save && pm2 startup   # 開機自啟
```

### 2.3 Nginx 反向代理（WebSocket 關鍵設定）
```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;

        # --- WebSocket 必備三行，少一行 wss 連不上 ---
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host       $host;
        # ------------------------------------------------

        proxy_set_header X-Real-IP        $remote_addr;
        proxy_set_header X-Forwarded-For  $proxy_add_x_forwarded_for;
        proxy_read_timeout 600s;   # WebSocket 長連線，拉長逾時避免被切
    }
}

# 80 強制轉 443
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}
```

### 2.4 HTTPS 憑證
- 雲廠商免費 DV 證書（阿里雲 / 騰訊雲都有，1 年期）
- 或 Let's Encrypt（`certbot`，90 天自動續期）
- **wss:// 必須有有效憑證**，自簽會被瀏覽器擋

### 2.5 安全群組 / 防火牆
- 開放 443（與 80 轉址）
- **不要對外開 3000**（Node.js 只聽 127.0.0.1，由 Nginx 代理）

---

## 3. 路線 B：香港 / 海外節點（免備案、快速上線）

與路線 A 相同，差異只在：
- 選香港 / 新加坡節點 → **跳過 ICP 備案**，當天可上
- 延遲略高（大陸→香港約 30–80ms，聊天可接受）
- 風險：海外節點偶有抖動 / 被干擾，穩定度不如大陸節點

> 過渡策略：先用路線 B 上線驗證需求 → 同時送 ICP 備案 → 備案下來再遷大陸節點（路線 A）。

---

## 4. 前端連線位址（部署後要改）

`index.html` 裡的 WebSocket 位址不能寫死 `localhost`，要依當前頁面協議自動切換：
```js
const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
const ws = new WebSocket(`${proto}//${location.host}`);
```
這樣本機 (`ws://localhost`) 與線上 (`wss://your-domain`) 同一份程式碼都能跑。

---

## 5. 上線檢查清單

```
[ ] 域名已解析到伺服器 IP
[ ] (大陸節點) ICP 備案已通過
[ ] HTTPS 憑證有效, 瀏覽器無警告
[ ] Nginx WebSocket Upgrade 三行已設定
[ ] proxy_read_timeout 已拉長 (避免長連線被切)
[ ] Node.js 由 PM2 常駐, 開機自啟
[ ] 3000 埠未對外暴露 (只聽 127.0.0.1)
[ ] 前端 ws/wss 依協議自動切換
[ ] 手機 4G/5G 實測連線 (不只 WiFi)
[ ] 兩台不同網路裝置實測房間碼配對
```

---

## 6. 運維與風險

| 項目 | 處理 |
|------|------|
| 進程崩潰 | PM2 自動重啟 + `pm2 logs chatroom` 看錯誤 |
| 伺服器重啟丟房間 | v1 無持久化, 接受; 需要的話房間狀態移 Redis |
| 長連線被中間裝置切斷 | client 端加心跳 ping/pong + 自動重連 |
| 記憶體膨脹 | 斷線即清房 + 閒置 timeout 回收空房 |
| 備案被抽查 | 確保備案資訊與實際服務內容一致 |

---

## 7. 需你拍板（影響部署路線）

1. **節點選大陸還是香港？**（決定要不要備案、上線時程）
2. **有沒有已備案的域名？**（沒有的話路線 A 要先排 1–2 週）
3. **正式長期 vs 過渡測試？**（決定 A / B / 先 B 後 A）
4. **要不要加心跳重連？**（中國網路環境長連線易被切，建議要）
