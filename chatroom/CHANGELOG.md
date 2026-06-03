# Changelog

## v1.2 — 2026-06-03

### 新增
- **進階設定面板**（大廳）：房間人數（2–10，建房時設定）、深色模式切換（暖色系暗色主題，localStorage 記憶）
- **房間級容量**：每房各自帶容量，`room_full` 依該房容量判斷（不再是全域固定值）
- **鎖房**：房主可鎖定 / 解除房間，鎖定後拒絕新人加入（`room_locked`）；房主離開時自動轉移給剩餘第一人
- 新協議：`set_lock`、`lock_changed`、`host_changed`，事件統一帶 `capacity` / `locked` / `isHost`

### 修復
- `leaveRoom` 提前清空 `socket.roomCode`，導致 `peer_left` 未送出（重寫 server 時的回歸，由測試卡住而暴露）

## v1.1 — 2026-06-03

### 新增
- **多人群聊**：房間容量由 1 對 1 提升至最多 5 人
- **暱稱**：進房前可填，同名自動補 `(2)`；訊息、typing、加入/離開皆帶發送者名字
- **成員名單 + 房內人數**顯示於頂部
- **固定房號**：`enter` 改為 join-or-create（房號不存在自動建房，雙方約定同一房號即可直接進同房）
- **localStorage**：記住房號 + 暱稱，reload 自動填回輸入框（URL `?room=` 優先）
- **訊息體驗**：時間戳、連續訊息分組（同人連發收緊、隱藏重複名字）、已送出標記 `✓`、入場動畫（尊重 `prefers-reduced-motion`）

### 修復
- **Windows 英文氣泡垂直排列**：`word-break: break-word`（非標準、flex 內塌縮）→ `overflow-wrap: break-word` + `width: fit-content`
- **iOS 行動端聚焦自動縮放 ≈1.1**：`textarea` `font-size` 15px → 16px（消除 iOS auto-zoom）

### 文件
- `SPEC.md`（規格）、`DEPLOY.md`（部署三路線）、`README.md` 完整化

## v1.0 — 2026-06-03
- 一對一房間碼聊天室，WebSocket 中轉，無自我回聲
- 移動端優先、暖色紙感 UI、系統字體（不依賴境外 CDN）
- 部署規格：Cloudflare 臨時隧道 / 阿里雲香港免費試用 / 大陸節點備案 三路線
- 9 項中轉邏輯自動化測試
