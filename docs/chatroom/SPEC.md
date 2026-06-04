# 小型群聊室（2–10 人）— 規格書 (SPEC)

> 版本：v1.2 ｜ 日期：2026-06-03 ｜ 狀態：draft
> v1.2 變更：房間級可自訂容量（2–10）、房主鎖房、深色模式、進階設定面板
> v1.1 變更：容量 2 → 5、加入暱稱與成員名單、`join` 改為 join-or-create（`enter`）

---

## 1. 目標與範圍

打造一個**在中國可正常使用**的小型群聊室（每房 2–10 人，建房時可設），透過 **6 位房間碼** 連線即可開聊，進房前可填暱稱。

| 項目 | 內容 |
|------|------|
| 核心場景 | A 建房（可設人數）取得房間碼 → 分享給朋友 → 各自填暱稱輸碼進同一房 → 即時群聊 |
| 平台 | Web UI（瀏覽器打開即用），後端 Node.js 可跑在 Mac / Windows / Linux |
| 關鍵約束 | 不依賴任何被牆服務（Google / Firebase / 境外字體 CDN / Google STUN） |
| 連線模式 | WebSocket 中轉（非純 WebRTC，避開國內 NAT 穿透失敗問題） |

### 非目標 (Out of Scope)
- 大型群聊（每房上限 10 人）
- 帳號系統 / 登入註冊（暱稱僅前端輸入，無驗證）
- 訊息持久化（v1 不存歷史，重整即清空）
- 端到端加密（列為 v2 擴充）

---

## 2. 為什麼這樣選（關鍵決策）

| 決策 | 原因 | 被否決的替代方案 |
|------|------|------------------|
| WebSocket 中轉 | 國內 STUN/TURN 多被牆，P2P NAT 穿透成功率低且不穩 | 純 WebRTC P2P |
| 系統字體棧 | 不依賴 Google Fonts，國內秒開、零外部請求 | 引入 Web Font CDN |
| 6 位房間碼配對 | 無需帳號、分享門檻低、口頭/連結皆可傳遞 | 帳號 + 好友系統 |
| 暖色紙感 UI | 避免微信綠克隆感，移動端優先 | 仿微信配色 |

> 批判性檢查：WebSocket 中轉的代價是**訊息會經過伺服器**（明文可見）。若隱私要求高，必須上 v2 的端到端加密；v1 預設信任自架伺服器。

---

## 3. 系統架構

```
使用者 A (瀏覽器)                          使用者 B (瀏覽器)
     |                                          |
     |  WebSocket (wss://)                       |  WebSocket (wss://)
     v                                          v
+--------------------------------------------------------------+
|                    Node.js 伺服器 (server.js)                 |
|                                                              |
|   HTTP 靜態服務 ----> public/index.html (聊天界面)            |
|                                                              |
|   WebSocket 中轉層                                            |
|     |                                                        |
|     +--> rooms: Map<roomCode, [clientA, clientB]>           |
|     |                                                        |
|     +--> 配對 / 訊息轉發 / typing / 斷線通知 / 滿員拒絕       |
+--------------------------------------------------------------+

部署（國內上線）
     |
     +--> 阿里雲 / 騰訊雲伺服器
              |
              +--> Nginx (反向代理 + WebSocket upgrade + HTTPS)
                       |
                       +--> Node.js (localhost:3000)
```

### 連線時序

```
A: 建房                          Server                   B/C…: 加入(最多 5)
 |                                 |                              |
 |-- create {name} ------------->  |                              |
 |  <-- room_created {code,self,members}                          |
 |                                 |                              |
 |  (分享 code 給朋友)             |                              |
 |                                 |  <-- enter {code,name} ------|
 |                                 |  -- joined {self,members} -> |
 |  <-- peer_joined {name,members}                                |
 |                                 |                              |
 |-- message {text} ----------->   |                              |
 |              -- message {name,text,ts} --> 房內所有其他人        |
 |                                 |   (不回送發送者本人，無回聲)  |
 |                                 |                              |
 |-- typing {isTyping} -------->   |  -- typing {name,isTyping} ->|
 |                                 |                              |
 |  (某人斷線)                     |                              |
 |              -- peer_left {name,members} --> 房內其他人          |
```

---

## 4. WebSocket 訊息協議

所有訊息為 JSON：`{ "type": "...", ...payload }`

### Client → Server

| type | payload | 說明 |
|------|---------|------|
| `create` | `{ name, capacity }` | 建隨機房（capacity 2–10），伺服器產生 6 位碼 |
| `enter` | `{ code, name, capacity }` | 進入房號：不存在則自動建立（含 capacity，支援固定房號），存在有空位且未鎖則加入 |
| `set_lock` | `{ locked }` | 僅房主可用：鎖定 / 解除房間 |
| `message` | `{ text }` | 發送訊息 |
| `typing` | `{ isTyping }` | 輸入中狀態 |

### Server → Client

> 房間事件統一帶 `capacity`（房間容量）、`locked`（是否鎖定）；`room_created` / `joined` 另帶 `isHost`。

| type | payload | 說明 |
|------|---------|------|
| `room_created` | `{ code, self, members, capacity, locked, isHost }` | 建房 / 自動建房，建房者 `isHost: true` |
| `joined` | `{ code, self, members, capacity, locked, isHost }` | 成功加入既有房 |
| `peer_joined` | `{ name, members, capacity, locked }` | 房內既有成員收到：誰加入 |
| `message` | `{ name, text, ts }` | 轉發訊息給房內其他所有人（**不回送發送者本人**） |
| `typing` | `{ name, isTyping }` | 轉發某成員輸入狀態 |
| `peer_left` | `{ name, members, capacity, locked }` | 某成員斷線：誰離開 |
| `host_changed` | `{ isHost }` | 房主離開後，新房主收到 |
| `lock_changed` | `{ locked, by }` | 房間鎖定狀態變更，廣播全房 |
| `error` | `{ reason }` | `room_full`、`room_locked`、`invalid_code` |

### 暱稱規則
- 進房前可填，伺服器端 trim + 限 16 字；留空自動給 `訪客NNNN`
- 房內同名自動補 `(2)`、`(3)`…，避免撞名分不清

### 房間 / 房主規則
- 容量由建房者設定（2–10），存於房間；`room_full` 依該房容量判斷
- 房主 = 建房者；房主離開自動轉移給剩餘第一人（`host_changed`）
- 僅房主可鎖房；鎖定後新人被拒（`room_locked`），房內既有成員不受影響

### 錯誤處理規則
- 房間已滿（達該房容量）→ `error: room_full`
- 房間已鎖定 → `error: room_locked`
- 房號格式非 6 位英數 → `error: invalid_code`
- 任一成員斷線 → 其他成員收到 `peer_left`，房間空了即清理

---

## 5. 功能需求

### 必做
- [x] 建房產生 6 位房間碼 / 固定房號（join-or-create）
- [x] 房間人數可設（2–10，建房時）+ 進階設定面板
- [x] 暱稱（進房前填，同名自動去重）
- [x] 多人即時群聊（訊息帶發送者、時間戳、連續分組、已送出標記，無自我回聲）
- [x] 成員名單 + 房內人數 / 容量顯示
- [x] 輸入中 (typing) 指示（多人聚合：「A、B 正在輸入…」）
- [x] 房間滿員拒絕（依該房容量）
- [x] 房主鎖房（room_locked）+ 房主自動轉移
- [x] 深色模式（暖色系，localStorage 記憶）
- [x] localStorage 記住房號 + 暱稱，reload 自動填入
- [x] 加入 / 離開通知（peer_joined / peer_left，含誰 + 人數）
- [x] 邀請連結（帶 code 的 URL，對方點開直接填碼）

### UI / UX
- 移動端優先（mobile-first，`md:` / `lg:` 往上覆蓋）
- 暖色紙感風格，**全系統字體棧**（不引入境外 CDN）
- 兩個狀態畫面：① 大廳（建房 / 輸碼）② 聊天室（訊息流 + 輸入框 + typing）

### v2 擴充（規劃中）
- 端到端加密（伺服器端僅見密文）
- 暱稱 / 頭像
- 圖片傳輸

---

## 6. 專案結構

```
chatroom/
+-- server.js          (HTTP 靜態服務 + WebSocket 中轉)
+-- package.json
+-- public/
|     +-- index.html   (單檔聊天界面，內含 CSS + JS)
+-- SPEC.md
+-- README.md          (Mac/Windows 啟動 + 國內上線 Nginx 設定)
```

> 注意：`index.html` 必須放在 `public/` 子目錄，否則靜態服務找不到。

---

## 7. 啟動與部署

### 本機開發（Mac / Windows 指令相同）
```bash
npm install
npm start
# 開 http://localhost:3000
# 自測：一般視窗建房 + 無痕視窗輸碼加入
```

### 國內上線
1. 部署到阿里雲 / 騰訊雲
2. Nginx 反向代理（需設定 WebSocket `Upgrade` header 轉發）
3. 上 HTTPS（wss://），確保 WebSocket 走加密通道

---

## 8. 測試計畫

中轉核心邏輯自動化測試（`test.mjs`，目前 16/16 通過）：

| # | 測試項 | 預期 |
|---|--------|------|
| 1 | 建房回傳房號 + 暱稱 | `room_created` + 6 位碼 + `self` |
| 2 | 加入收 joined + 名單 | `joined` 含完整 `members` |
| 3 | 既有成員收 peer_joined | `peer_joined` 含新成員 `name` |
| 4 | 訊息轉發帶發送者名 | 對方收到 `{name, text}` |
| 5 | 無自我回聲 | 發送者收不到自己的訊息 |
| 6 | 多人廣播 | 房內其他人都收到同一則 |
| 7 | typing 帶發送者名 | 對方收到 `{name, isTyping}` |
| 8 | 自訂容量 + isHost | 建房 `capacity` 生效、建房者 `isHost: true` |
| 9 | 滿員拒絕（自訂容量） | 達容量後新人收 `room_full` |
| 10 | 加入者 isHost = false | `joined` 的 `isHost` 為 false |
| 11 | 鎖房廣播 | 房主 `set_lock` → 全房收 `lock_changed` |
| 12 | 鎖房後拒絕新人 | 新人收 `room_locked` |
| 13 | 固定房號 join-or-create | 不存在自動建房，第二人收 `joined` |
| 14 | 非法房號 | 收到 `invalid_code` |
| 15 | 斷線通知帶離開者名 | 其他人收到 `peer_left` 含 `name` |
| — | HTTP 靜態 + 目錄穿越防護 | 首頁 200、`../` 穿越被擋（手動驗） |

---

## 9. 風險與待確認

| 風險 | 影響 | 緩解 |
|------|------|------|
| 訊息經伺服器明文 | 隱私 | v2 端到端加密；v1 信任自架伺服器 |
| 房間碼碰撞 | 進錯房 | 6 位碼空間夠大；產碼時檢查現有 rooms 去重 |
| 伺服器重啟丟房間 | 連線中斷 | v1 接受（無持久化）；需要的話上 Redis |
| 大量房間記憶體佔用 | OOM | 斷線即清理房間；可加閒置 timeout 回收 |

### 需你拍板
- v1 是否需要訊息持久化？（預設否）
- 端到端加密是否提前到 v1？（影響伺服器是否能看到內容）
- 房間是否需要有效期 / 閒置自動關閉？
```
