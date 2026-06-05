import { createServer, get as httpGet } from 'node:http';
import { readFile, readdir, writeFile, unlink, mkdir } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';

const PORT = Number(process.env.PORT) || 3000;
const PUBLIC_DIR = fileURLToPath(new URL('./public', import.meta.url));
const STICKERS_DIR = fileURLToPath(new URL('./stickers', import.meta.url)); // 貼圖資料夾（本機放圖）
const STICKER_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp']);
let adminPassword = process.env.STICKER_ADMIN_PASSWORD || ''; // 貼圖管理密碼（可由 /admin 修改）
const STICKER_PW_FILE = process.env.STICKER_PW_FILE || ''; // 密碼持久化檔（設了才能改密碼）
const MAX_STICKER_BYTES = 5 * 1024 * 1024; // 單張貼圖上限 5MB
const ROOM_CODE_LENGTH = 6;
const DEFAULT_CAPACITY = 5;
const MIN_CAPACITY = 2;
const MAX_CAPACITY = 10;
const HEARTBEAT_INTERVAL_MS = 30_000;
const ROOM_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // 去掉易混淆 0/O/1/I
const CODE_PATTERN = /^[A-Z0-9]{6}$/; // 房號：6 位英數（隨機與固定房號共用格式）

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.ico': 'image/x-icon',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
};

// rooms: Map<roomCode, { members: Set<WebSocket>, capacity: number, locked: boolean, host: WebSocket }>
const rooms = new Map();

// ---- 對話紀錄 cache（記憶體；UTC+8 每日重置）----
const HISTORY_MAX = 300;
const roomHistory = new Map(); // code -> { day:'YYYY-MM-DD', items:[{type,name,text|sticker,ts}] }
const dayKeyOf = (ts) => new Date(ts + 8 * 3600 * 1000).toISOString().slice(0, 10); // 東八區日期邊界
const pushHistory = (code, item) => {
  const today = dayKeyOf(item.ts);
  let h = roomHistory.get(code);
  if (!h || h.day !== today) { h = { day: today, items: [] }; roomHistory.set(code, h); } // 跨日重置
  h.items.push(item);
  if (h.items.length > HISTORY_MAX) h.items.splice(0, h.items.length - HISTORY_MAX);
};
const getHistory = (code) => {
  const h = roomHistory.get(code);
  if (!h || h.day !== dayKeyOf(Date.now())) { roomHistory.delete(code); return []; } // 跨日清空
  return h.items;
};

const send = (socket, type, payload = {}) => {
  if (socket.readyState === socket.OPEN) {
    socket.send(JSON.stringify({ type, ...payload }));
  }
};

const generateRoomCode = () => {
  let code = '';
  do {
    code = Array.from({ length: ROOM_CODE_LENGTH }, () => {
      const index = Math.floor(Math.random() * ROOM_CODE_ALPHABET.length);
      return ROOM_CODE_ALPHABET[index];
    }).join('');
  } while (rooms.has(code));
  return code;
};

const sanitizeCapacity = (n) => {
  const c = Number.parseInt(n, 10);
  return Number.isNaN(c) ? DEFAULT_CAPACITY : Math.min(MAX_CAPACITY, Math.max(MIN_CAPACITY, c));
};

const peersOf = (socket) => {
  const room = rooms.get(socket.roomCode);
  if (!room) return [];
  return [...room.members].filter((peer) => peer !== socket);
};

const membersIn = (code) => [...(rooms.get(code)?.members || [])].map((s) => s.name);

// 房間狀態快照（廣播給前端，讓人數/容量/鎖定一致）
const roomState = (code) => {
  const room = rooms.get(code);
  return { members: membersIn(code), capacity: room?.capacity ?? DEFAULT_CAPACITY, locked: room?.locked ?? false };
};

const sanitizeName = (raw) => {
  const name = String(raw || '').trim().slice(0, 16);
  return name || `訪客${Math.floor(1000 + Math.random() * 9000)}`;
};

// 房內若已有同名，補上 (2)、(3)…，避免撞名分不清
const uniqueName = (code, desired) => {
  const existing = new Set(membersIn(code));
  if (!existing.has(desired)) return desired;
  let n = 2;
  while (existing.has(`${desired}(${n})`)) n += 1;
  return `${desired}(${n})`;
};

const leaveRoom = (socket) => {
  const { roomCode } = socket;
  if (!roomCode) return;
  const room = rooms.get(roomCode);
  if (!room) { socket.roomCode = null; return; }

  room.members.delete(socket);

  if (room.members.size === 0) {
    rooms.delete(roomCode);
    socket.roomCode = null;
    return;
  }

  // 房主離開 → 轉移給剩餘第一人
  if (room.host === socket) {
    room.host = [...room.members][0] || null;
    if (room.host) send(room.host, 'host_changed', { isHost: true });
  }

  // socket 已從 members 移除，剩下的即為要通知的 peers（不依賴 socket.roomCode）
  const state = roomState(roomCode);
  room.members.forEach((peer) => send(peer, 'peer_left', { name: socket.name, ...state }));
  socket.roomCode = null;
};

const handleCreate = (socket, name, capacity) => {
  leaveRoom(socket);
  const code = generateRoomCode();
  socket.name = uniqueName(code, sanitizeName(name));
  rooms.set(code, { members: new Set([socket]), capacity: sanitizeCapacity(capacity), locked: false, host: socket });
  socket.roomCode = code;
  send(socket, 'history', { items: getHistory(code) });
  send(socket, 'room_created', { code, self: socket.name, isHost: true, ...roomState(code) });
};

// 進入房號：不存在就建立（支援固定房號 / 斷線後重建），存在有空位且未鎖就加入
const handleEnter = (socket, code, name, capacity) => {
  const normalizedCode = String(code || '').trim().toUpperCase();
  if (!CODE_PATTERN.test(normalizedCode)) {
    send(socket, 'error', { reason: 'invalid_code' });
    return;
  }

  const room = rooms.get(normalizedCode);

  if (room && !room.members.has(socket)) {
    if (room.locked) {
      send(socket, 'error', { reason: 'room_locked' });
      return;
    }
    if (room.members.size >= room.capacity) {
      send(socket, 'error', { reason: 'room_full' });
      return;
    }
  }

  leaveRoom(socket);
  socket.name = uniqueName(normalizedCode, sanitizeName(name));

  if (!room) {
    rooms.set(normalizedCode, { members: new Set([socket]), capacity: sanitizeCapacity(capacity), locked: false, host: socket });
    socket.roomCode = normalizedCode;
    send(socket, 'history', { items: getHistory(normalizedCode) });
    send(socket, 'room_created', { code: normalizedCode, self: socket.name, isHost: true, ...roomState(normalizedCode) });
    return;
  }

  room.members.add(socket);
  socket.roomCode = normalizedCode;
  send(socket, 'history', { items: getHistory(normalizedCode) });
  send(socket, 'joined', { code: normalizedCode, self: socket.name, isHost: room.host === socket, ...roomState(normalizedCode) });
  peersOf(socket).forEach((peer) => send(peer, 'peer_joined', { name: socket.name, ...roomState(normalizedCode) }));
};

// 僅房主可鎖 / 解鎖房間
const handleSetLock = (socket, locked) => {
  const room = rooms.get(socket.roomCode);
  if (!room || room.host !== socket) return;
  room.locked = Boolean(locked);
  room.members.forEach((member) => send(member, 'lock_changed', { locked: room.locked, by: socket.name }));
};

// ---- 抖音 webUI 橋接：把房間訊息鏡像到 web_danmaku 的「聊天室」視窗 ----
// DANMAKU_URL 為空（獨立啟動）時不轉發；web_danmaku 未啟動時靜默略過，不影響聊天
const DANMAKU_URL = process.env.DANMAKU_URL || '';
const DANMAKU_PASSWORD = process.env.DANMAKU_PASSWORD || '';
const postIngest = (body) => {
  if (!DANMAKU_URL) return;
  const headers = { 'Content-Type': 'application/json' };
  if (DANMAKU_PASSWORD) headers.Authorization = 'Basic ' + Buffer.from(`:${DANMAKU_PASSWORD}`).toString('base64');
  fetch(`${DANMAKU_URL}/chat/ingest`, { method: 'POST', headers, body: JSON.stringify(body) })
    .catch(() => { /* web_danmaku 未啟動或斷線時靜默略過 */ });
};
const forwardToDanmaku = (room, name, text) => postIngest({ room, name, text });
// 貼圖鏡像：webUI 與 chatroom 同機，圖片用 127.0.0.1:<PORT>/stickers/ 載入
const forwardSticker = (room, name, stickerName) =>
  postIngest({ room, name, text: '[貼圖]', sticker_url: `http://127.0.0.1:${PORT}/stickers/${encodeURIComponent(stickerName)}` });

const handleMessage = (socket, text) => {
  const content = String(text || '').slice(0, 4000);
  if (!content || !socket.roomCode) return;
  const ts = Date.now();
  pushHistory(socket.roomCode, { type: 'message', name: socket.name, text: content, ts });
  // 轉給房內其他所有人，不回送自己（無自我回聲）
  peersOf(socket).forEach((peer) => send(peer, 'message', { name: socket.name, text: content, ts }));
  // 同時鏡像到抖音 webUI 的「聊天室」視窗
  forwardToDanmaku(socket.roomCode, socket.name, content);
};

// 列出 stickers 目錄內的貼圖檔（natural sort，與前端面板 / 重命名工具順序一致）
const listStickers = async () => {
  try {
    const files = await readdir(STICKERS_DIR);
    return files
      .filter((name) => STICKER_EXTS.has(extname(name).toLowerCase()))
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  } catch {
    return [];
  }
};

// 貼圖訊息：白名單驗證（必須是 stickers 目錄實際檔案）後廣播 + 鏡像 webUI
const handleSticker = async (socket, name) => {
  if (!socket.roomCode) return;
  const safeName = String(name || '');
  const stickers = await listStickers();
  if (!stickers.includes(safeName)) return; // 防注入 / 路徑穿越
  const ts = Date.now();
  pushHistory(socket.roomCode, { type: 'sticker', name: socket.name, sticker: safeName, ts });
  peersOf(socket).forEach((peer) => send(peer, 'sticker', { name: safeName, sender: socket.name, ts }));
  forwardSticker(socket.roomCode, socket.name, safeName);
};

// 房間內改暱稱：唯一化後廣播給房內（含自己確認），讓直接進房的訪客可改名
const handleRename = (socket, name) => {
  if (!socket.roomCode) return;
  const old = socket.name;
  const newName = uniqueName(socket.roomCode, sanitizeName(name));
  if (newName === old) return;
  socket.name = newName;
  send(socket, 'renamed', { self: newName, ...roomState(socket.roomCode) });
  peersOf(socket).forEach((peer) => send(peer, 'peer_renamed', { old, name: newName, ...roomState(socket.roomCode) }));
};

const handleTyping = (socket, isTyping) => {
  peersOf(socket).forEach((peer) => send(peer, 'typing', { name: socket.name, isTyping: Boolean(isTyping) }));
};

const handleClientMessage = (socket, raw) => {
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return; // 非法 JSON 靜默忽略
  }

  switch (data.type) {
    case 'create':
      return handleCreate(socket, data.name, data.capacity);
    case 'enter':
      return handleEnter(socket, data.code, data.name, data.capacity);
    case 'set_lock':
      return handleSetLock(socket, data.locked);
    case 'message':
      return handleMessage(socket, data.text);
    case 'sticker':
      return handleSticker(socket, data.name);
    case 'rename':
      return handleRename(socket, data.name);
    case 'typing':
      return handleTyping(socket, data.isTyping);
    default:
      return undefined;
  }
};

// ---- HTTP 靜態服務（含目錄穿越防護）----
const serveStatic = async (req, res) => {
  const rawPath = decodeURIComponent((req.url || '/').split('?')[0]);
  const relativePath = rawPath === '/' ? '/index.html' : rawPath;
  const resolved = normalize(join(PUBLIC_DIR, relativePath));

  if (!resolved.startsWith(PUBLIC_DIR)) {
    res.writeHead(403).end('Forbidden');
    return;
  }

  try {
    const file = await readFile(resolved);
    const ext = extname(resolved).toLowerCase();
    const headers = { 'Content-Type': MIME_TYPES[ext] || 'application/octet-stream' };
    if (ext === '.html') headers['Cache-Control'] = 'no-cache'; // html 不快取，更新重整即見
    res.writeHead(200, headers).end(file);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('Not Found');
  }
};

// ---- 貼圖：清單 + 靜態檔（副檔名白名單 + 目錄穿越防護）----
const serveStickerList = async (res) => {
  const stickers = await listStickers();
  res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' }).end(JSON.stringify({ stickers }));
};

const serveSticker = async (pathname, res) => {
  const name = pathname.slice('/stickers/'.length); // pathname 已在路由處 decode
  const resolved = normalize(join(STICKERS_DIR, name));
  if (!resolved.startsWith(STICKERS_DIR) || !STICKER_EXTS.has(extname(resolved).toLowerCase())) {
    res.writeHead(403).end('Forbidden');
    return;
  }
  try {
    const file = await readFile(resolved);
    const mime = MIME_TYPES[extname(resolved).toLowerCase()] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': mime }).end(file);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('Not Found');
  }
};

// ---- 貼圖 CRUD（上傳 / 刪除，管理密碼保護；以 base64 JSON 傳輸，免額外依賴）----
const jsonRes = (res, code, obj) => res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' }).end(JSON.stringify(obj));

const readJsonBody = (req, limit = 8 * 1024 * 1024) => new Promise((resolve, reject) => {
  let size = 0; const chunks = [];
  req.on('data', (c) => {
    size += c.length;
    if (size > limit) { reject(new Error('too large')); req.destroy(); return; }
    chunks.push(c);
  });
  req.on('end', () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch { reject(new Error('bad json')); } });
  req.on('error', reject);
});

// 副檔名由實際圖片格式(dataUrl MIME)決定，不依賴原檔名 → 放寬上傳（原檔無副檔名也行）
const EXT_BY_MIME = { png: '.png', jpeg: '.jpg', jpg: '.jpg', gif: '.gif', webp: '.webp' };
// 安全檔名主幹：去路徑、去原副檔名、清特殊字元（保留中英數與中文）
const safeStickerStem = (raw) => {
  const base = String(raw || '').replace(/[/\\]/g, '').trim();
  const dot = base.lastIndexOf('.');
  const stem = (dot > 0 ? base.slice(0, dot) : base).replace(/[^a-zA-Z0-9_一-龥-]/g, '_').slice(0, 60);
  return stem || 'sticker';
};

const checkAdmin = (body) => Boolean(adminPassword) && body && body.password === adminPassword;

const handleUpload = async (req, res) => {
  let body;
  try { body = await readJsonBody(req); } catch { return jsonRes(res, 400, { ok: false, err: '請求格式錯' }); }
  if (!checkAdmin(body)) return jsonRes(res, 403, { ok: false, err: '管理密碼錯誤' });
  const m = /^data:image\/([a-zA-Z.+-]+);base64,(.+)$/.exec(String(body.dataUrl || ''));
  if (!m) return jsonRes(res, 400, { ok: false, err: '圖片資料格式錯' });
  const ext = EXT_BY_MIME[m[1].toLowerCase()];
  if (!ext) return jsonRes(res, 400, { ok: false, err: '不支援的圖片格式（限 png/jpg/gif/webp）' });
  const name = `${safeStickerStem(body.name)}${ext}`;
  const buf = Buffer.from(m[2], 'base64');
  if (buf.length === 0 || buf.length > MAX_STICKER_BYTES) return jsonRes(res, 400, { ok: false, err: '檔案為空或超過 5MB' });
  try {
    await mkdir(STICKERS_DIR, { recursive: true });
    await writeFile(join(STICKERS_DIR, name), buf);
    return jsonRes(res, 200, { ok: true, name });
  } catch { return jsonRes(res, 500, { ok: false, err: '寫入失敗' }); }
};

const handleDelete = async (req, res) => {
  let body;
  try { body = await readJsonBody(req); } catch { return jsonRes(res, 400, { ok: false, err: '請求格式錯' }); }
  if (!checkAdmin(body)) return jsonRes(res, 403, { ok: false, err: '管理密碼錯誤' });
  const list = await listStickers();
  const name = String(body.name || '');
  if (!list.includes(name)) return jsonRes(res, 404, { ok: false, err: '貼圖不存在' });
  try { await unlink(join(STICKERS_DIR, name)); return jsonRes(res, 200, { ok: true }); }
  catch { return jsonRes(res, 500, { ok: false, err: '刪除失敗' }); }
};

// 修改管理密碼（需舊密碼正確；寫入 STICKER_PW_FILE 持久化）
const handleChangePassword = async (req, res) => {
  let body;
  try { body = await readJsonBody(req); } catch { return jsonRes(res, 400, { ok: false, err: '請求格式錯' }); }
  if (!adminPassword || body.oldPassword !== adminPassword) return jsonRes(res, 403, { ok: false, err: '舊密碼錯誤' });
  const np = String(body.newPassword || '').trim();
  if (np.length < 4) return jsonRes(res, 400, { ok: false, err: '新密碼至少 4 字' });
  if (!STICKER_PW_FILE) return jsonRes(res, 500, { ok: false, err: '伺服器未設定密碼檔，無法修改' });
  try { await writeFile(STICKER_PW_FILE, np); adminPassword = np; return jsonRes(res, 200, { ok: true }); }
  catch { return jsonRes(res, 500, { ok: false, err: '寫入失敗' }); }
};

// 純驗證管理密碼（登入用，不做任何操作）
const handleVerify = async (req, res) => {
  let body; try { body = await readJsonBody(req); } catch { return jsonRes(res, 400, { ok: false }); }
  return jsonRes(res, 200, { ok: checkAdmin(body) });
};

const httpServer = createServer((req, res) => {
  const pathname = decodeURIComponent((req.url || '/').split('?')[0]);
  if (req.method === 'POST' && pathname === '/stickers/verify') return handleVerify(req, res);
  if (req.method === 'POST' && pathname === '/stickers/upload') return handleUpload(req, res);
  if (req.method === 'POST' && pathname === '/stickers/delete') return handleDelete(req, res);
  if (req.method === 'POST' && pathname === '/stickers/password') return handleChangePassword(req, res);
  if (pathname === '/admin' || pathname === '/admin/') { req.url = '/admin.html'; return serveStatic(req, res); }
  if (pathname === '/stickers') return serveStickerList(res);
  if (pathname.startsWith('/stickers/')) return serveSticker(pathname, res);
  return serveStatic(req, res);
});
const wss = new WebSocketServer({ server: httpServer });

wss.on('connection', (socket) => {
  socket.roomCode = null;
  socket.name = null;
  socket.isAlive = true;

  socket.on('pong', () => {
    socket.isAlive = true;
  });

  socket.on('message', (raw) => handleClientMessage(socket, raw.toString()));
  socket.on('close', () => leaveRoom(socket));
  socket.on('error', () => leaveRoom(socket));
});

// ---- 心跳：清掉斷線但沒觸發 close 的殭屍連線 ----
const heartbeat = setInterval(() => {
  wss.clients.forEach((socket) => {
    if (socket.isAlive === false) {
      socket.terminate();
      return;
    }
    socket.isAlive = false;
    socket.ping();
  });
}, HEARTBEAT_INTERVAL_MS);

wss.on('close', () => clearInterval(heartbeat));

// ---- 需求 A：訂閱 web_danmaku 的彈幕 SSE，注入所有房間（房內成員即時看彈幕）----
const DANMAKU_TYPES = new Set(['chat', 'gift', 'member', 'social', 'like']);

const broadcastDanmaku = (ev) => {
  // 只轉真彈幕；排除 roomchat（那是 chatroom 自己鏡像出去的，避免回聲 loop）
  if (!ev || !DANMAKU_TYPES.has(ev.type)) return;
  const payload = { dtype: ev.type, name: ev.name || '', text: ev.text || '' };
  wss.clients.forEach((sock) => { if (sock.roomCode) send(sock, 'danmaku', payload); });
};

const connectDanmaku = () => {
  if (!DANMAKU_URL) return; // 獨立啟動（無抖音）時不訂閱
  let url;
  try { url = new URL('/stream', DANMAKU_URL); } catch { return; }
  const headers = { Accept: 'text/event-stream' };
  if (DANMAKU_PASSWORD) headers.Authorization = 'Basic ' + Buffer.from(`:${DANMAKU_PASSWORD}`).toString('base64');
  const req = httpGet(url, { headers }, (res) => {
    if (res.statusCode !== 200) { res.resume(); setTimeout(connectDanmaku, 3000); return; }
    res.setEncoding('utf8');
    let buf = '';
    res.on('data', (chunk) => {
      buf += chunk;
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line.startsWith('data:')) {
          try { broadcastDanmaku(JSON.parse(line.slice(5).trim())); } catch { /* 非 JSON 行略過 */ }
        }
      }
    });
    res.on('end', () => setTimeout(connectDanmaku, 3000)); // 斷線自動重連
  });
  req.on('error', () => setTimeout(connectDanmaku, 3000));
};

httpServer.listen(PORT, async () => {
  if (STICKER_PW_FILE) {
    try { const v = (await readFile(STICKER_PW_FILE, 'utf8')).trim(); if (v) adminPassword = v; } catch { /* 用 env 初始值 */ }
  }
  console.log(`Chatroom 已啟動：http://localhost:${PORT}`);
  connectDanmaku();
});
