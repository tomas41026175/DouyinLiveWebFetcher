import { createServer, get as httpGet } from 'node:http';
import { readFile, readdir } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { WebSocketServer } from 'ws';

const PORT = Number(process.env.PORT) || 3000;
const PUBLIC_DIR = fileURLToPath(new URL('./public', import.meta.url));
const STICKERS_DIR = fileURLToPath(new URL('./stickers', import.meta.url)); // 貼圖資料夾（本機放圖）
const STICKER_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp']);
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
    send(socket, 'room_created', { code: normalizedCode, self: socket.name, isHost: true, ...roomState(normalizedCode) });
    return;
  }

  room.members.add(socket);
  socket.roomCode = normalizedCode;
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
  peersOf(socket).forEach((peer) => send(peer, 'sticker', { name: safeName, sender: socket.name, ts }));
  forwardSticker(socket.roomCode, socket.name, safeName);
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
    const mime = MIME_TYPES[extname(resolved)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': mime }).end(file);
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

const httpServer = createServer((req, res) => {
  const pathname = decodeURIComponent((req.url || '/').split('?')[0]);
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

httpServer.listen(PORT, () => {
  console.log(`Chatroom 已啟動：http://localhost:${PORT}`);
  connectDanmaku();
});
