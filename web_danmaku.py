# coding: utf-8
# 抖音弹幕 Web UI（简体中文 / 可切换直播间 / 已保存房间 / 多窗口自定义）
# 后台抓取弹幕，浏览器实时显示。功能：
#   - 输入房间号即时切换直播间
#   - 房间号 + 备注保存到本机 rooms.json，下拉选单一键切换/删除
#   - 自行添加窗口，每个窗口可选择显示内容（全部/聊天/礼物/进场/关注 + 关键字）
# 纯 Python 标准库 (SSE)，无需额外安装。
# 用法：放在 DouyinLiveWebFetcher 文件夹（与 liveMan.py 同层）运行。

import base64
import gzip
import json
import os
import queue
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import websocket
from liveMan import DouyinLiveWebFetcher, generateMsToken
from protobuf.douyin import (
    ChatMessage, GiftMessage, MemberMessage, SocialMessage,
    LikeMessage, RoomUserSeqMessage, PushFrame, Response,
)

VERSION = "v1.9 (2026-05-31) 指定用户窗口"   # 每次更新都会变化，可用于确认已是最新版
PORT = 8765
ENV_PATH = ".env"
ROOMS_PATH = "rooms.json"
STATS_DIR = "stats"
GIFT_NAMES_PATH = "gift_names.json"
PW_PATH = "ui_password.txt"
CHAT_URL_FILE = "chatroom_url.txt"   # 啟動腳本寫入聊天室的 Cloudflare 公網網址
DEFAULT_PASSWORD = "0425"
AUTH_PASSWORD = ""
HISTORY_MAX = 800           # 后端保留的最近弹幕条数（刷新后回填）

_subscribers = []
_sub_lock = threading.Lock()
_rooms_lock = threading.Lock()
_stats = {}                 # room -> { user -> { date -> {likes, gifts, items} } }
_stats_lock = threading.RLock()
_stats_dirty = set()
_history = []               # 当前房间最近弹幕（序列化后的字符串）

_HISTORY_TYPES = ("chat", "gift", "member", "social", "roomchat")

# ---- 诊断模式：每10秒在控制台打印各类消息累计数（含礼物）。不需要时改为 False ----
DIAG = True
_diag_counts = {}
_diag_lock = threading.Lock()

_METHOD_CN = {
    "WebcastChatMessage": "聊天",
    "WebcastGiftMessage": "礼物",
    "WebcastMemberMessage": "进场",
    "WebcastSocialMessage": "关注",
    "WebcastLikeMessage": "点赞",
    "WebcastRoomUserSeqMessage": "在线统计",
    "WebcastRoomRankMessage": "排行榜",
}


def _diag_bump(method):
    with _diag_lock:
        _diag_counts[method] = _diag_counts.get(method, 0) + 1


_dumped_methods = set()
_DUMP_METHODS = ("WebcastLightGiftMessage", "WebcastGiftMessage",
                 "WebcastGiftPlayEventMessage", "WebcastGiftSortMessage")


def _read_varint(b, i):
    shift = 0
    result = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, i


def _pb_dump(data, indent=0, lines=None):
    if lines is None:
        lines = []
    i, n = 0, len(data)
    pad = "  " * indent
    while i < n and len(lines) < 200:
        try:
            tag, i = _read_varint(data, i)
        except Exception:
            break
        field, wt = tag >> 3, tag & 7
        if wt == 0:
            try:
                val, i = _read_varint(data, i)
            except Exception:
                break
            lines.append(f"{pad}#{field} int={val}")
        elif wt == 2:
            try:
                ln, i = _read_varint(data, i)
            except Exception:
                break
            chunk = data[i:i + ln]
            i += ln
            s = None
            try:
                s = chunk.decode("utf-8")
                if any(ord(c) < 0x20 and c not in "\n\t" for c in s):
                    s = None
            except Exception:
                s = None
            has_cjk = bool(s and any("一" <= c <= "鿿" for c in s))
            if s is not None and (has_cjk or (ln < 40 and indent > 0)):
                lines.append(f"{pad}#{field} str=\"{s}\"")
            elif ln > 1 and indent < 4:
                lines.append(f"{pad}#{field} msg({ln}) {{")
                _pb_dump(chunk, indent + 1, lines)
                lines.append(f"{pad}}}")
            else:
                lines.append(f"{pad}#{field} bytes({ln})")
        elif wt == 5:
            i += 4
            lines.append(f"{pad}#{field} f32")
        elif wt == 1:
            i += 8
            lines.append(f"{pad}#{field} f64")
        else:
            break
    return lines


def _pb_parse(data):
    """通用 protobuf 解析：返回 {field_no: [value,...]}，varint 为 int，length-delimited 为 bytes。"""
    out = {}
    i, n = 0, len(data)
    while i < n:
        try:
            tag, i = _read_varint(data, i)
        except Exception:
            break
        f, wt = tag >> 3, tag & 7
        if wt == 0:
            try:
                v, i = _read_varint(data, i)
            except Exception:
                break
        elif wt == 2:
            try:
                ln, i = _read_varint(data, i)
            except Exception:
                break
            v = data[i:i + ln]
            i += ln
        elif wt == 5:
            v = data[i:i + 4]
            i += 4
        elif wt == 1:
            v = data[i:i + 8]
            i += 8
        else:
            break
        out.setdefault(f, []).append(v)
    return out


def lightgift_giftid(payload):
    """从 WebcastLightGiftMessage 提取礼物 id（字段 #7.#1 或 #13.#5）。"""
    top = _pb_parse(payload)
    for outer, inner_no in ((7, 1), (13, 5)):
        for val in top.get(outer, []):
            if isinstance(val, (bytes, bytearray)):
                inner = _pb_parse(val)
                for x in inner.get(inner_no, []):
                    if isinstance(x, int):
                        return x
    return None


# ---- 礼物名称清单（id -> 名称），本地保存，可由直播间礼物清单自动填充 ----
_gift_names = {}


def load_gift_names():
    global _gift_names
    if os.path.exists(GIFT_NAMES_PATH):
        try:
            with open(GIFT_NAMES_PATH, encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, dict):
                    _gift_names = {str(k): v for k, v in d.items()}
        except Exception:
            pass


def save_gift_names():
    try:
        with open(GIFT_NAMES_PATH, "w", encoding="utf-8") as f:
            json.dump(_gift_names, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _maybe_dump_gift(method, payload):
    if method in _DUMP_METHODS and method not in _dumped_methods:
        _dumped_methods.add(method)
        print(f"[礼物结构] {method} payload={len(payload)} bytes 字段结构：")
        try:
            for ln in _pb_dump(payload):
                print("   " + ln)
        except Exception as e:
            print("   结构解析失败:", e)
        print(f"[礼物结构] —— {method} 结束 ——")


def _diag_printer():
    while True:
        time.sleep(10)
        with _diag_lock:
            if not _diag_counts:
                print("[诊断] 最近10秒未收到任何消息（可能直播未开播或房间号有误）")
                continue
            items = sorted(_diag_counts.items(), key=lambda x: -x[1])
        parts = []
        for k, v in items:
            parts.append(f"{_METHOD_CN.get(k, k)}={v}")
        print("[诊断] 累计消息：" + "  ".join(parts))


def broadcast(event: dict):
    data = json.dumps(event, ensure_ascii=False)
    with _sub_lock:
        if event.get("type") in _HISTORY_TYPES:
            _history.append(data)
            if len(_history) > HISTORY_MAX:
                del _history[:len(_history) - HISTORY_MAX]
        for q in list(_subscribers):
            try:
                q.put_nowait(data)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(data)
                except Exception:
                    pass


def clear_history():
    with _sub_lock:
        _history.clear()


# ---- 自动关闭：浏览器分页全部关闭后退出进程（cmd 随之关闭） ----
AUTO_CLOSE = os.environ.get("DY_NO_AUTOCLOSE", "").strip() == ""  # 設 DY_NO_AUTOCLOSE=1 關閉自動退出（整合啟動腳本用，避免無瀏覽器時被殺）
GRACE_SECONDS = 3          # 宽限时间：刷新会在此时间内重连，不会误关
_active = 0
_active_lock = threading.Lock()
_shutdown_timer = None


def _client_connected():
    global _active, _shutdown_timer
    with _active_lock:
        _active += 1
        if _shutdown_timer:
            _shutdown_timer.cancel()
            _shutdown_timer = None


def _client_disconnected():
    global _active, _shutdown_timer
    with _active_lock:
        _active -= 1
        if _active <= 0 and AUTO_CLOSE:
            _shutdown_timer = threading.Timer(GRACE_SECONDS, _maybe_shutdown)
            _shutdown_timer.daemon = True
            _shutdown_timer.start()


def _maybe_shutdown():
    with _active_lock:
        if _active > 0:
            return
    _stats_flush()
    print("\n浏览器已全部关闭，自动退出。")
    os._exit(0)


def load_live_id():
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip().upper() == "LIVE_ID":
                    return v.strip()
    return ""


def save_live_id(live_id):
    try:
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write("# DouyinLiveWebFetcher config\n")
            f.write("# Room id = digits after live.douyin.com/\n")
            f.write(f"LIVE_ID={live_id}\n")
    except Exception:
        pass


def clean_room_id(raw):
    if not raw:
        return ""
    nums = re.findall(r"\d{5,}", raw)
    return nums[-1] if nums else raw.strip()


def load_password():
    # 优先环境变量；其次 ui_password.txt；否则用默认密码并写入文件
    p = os.environ.get("DY_UI_PASSWORD", "").strip()
    if p:
        return p
    if os.path.exists(PW_PATH):
        try:
            v = open(PW_PATH, encoding="utf-8").read().strip()
            if v:
                return v
        except Exception:
            pass
    try:
        with open(PW_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_PASSWORD)
    except Exception:
        pass
    return DEFAULT_PASSWORD


# ---- 已保存房间（房号 + 备注），持久化到 rooms.json ----
def load_rooms():
    if os.path.exists(ROOMS_PATH):
        try:
            with open(ROOMS_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [r for r in data if isinstance(r, dict) and r.get("id")]
        except Exception:
            pass
    return []


def save_rooms(rooms):
    try:
        with open(ROOMS_PATH, "w", encoding="utf-8") as f:
            json.dump(rooms, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add_room(rid, note=""):
    rid = clean_room_id(rid)
    if not rid:
        return load_rooms()
    note = (note or "").strip()
    with _rooms_lock:
        rooms = load_rooms()
        for r in rooms:
            if r.get("id") == rid:
                if note:
                    r["note"] = note
                save_rooms(rooms)
                return rooms
        rooms.append({"id": rid, "note": note or rid})
        save_rooms(rooms)
        return rooms


def del_room(rid):
    rid = clean_room_id(rid)
    with _rooms_lock:
        rooms = [r for r in load_rooms() if r.get("id") != rid]
        save_rooms(rooms)
        return rooms


def edit_room(old_id, new_id, note=""):
    old_id = clean_room_id(old_id)
    new_id = clean_room_id(new_id)
    note = (note or "").strip()
    if not new_id:
        return load_rooms()
    with _rooms_lock:
        rooms = [r for r in load_rooms() if r.get("id") != old_id]
        for r in rooms:
            if r.get("id") == new_id:
                r["note"] = note or r.get("note") or new_id
                save_rooms(rooms)
                return rooms
        rooms.append({"id": new_id, "note": note or new_id})
        save_rooms(rooms)
        return rooms


# ================= 统计：每天的送礼/点赞，按 user>date>data 本地保存 =================
def _today():
    return time.strftime("%Y-%m-%d", time.localtime())


def _stats_path(room):
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", str(room))
    return os.path.join(STATS_DIR, f"stats_{safe}.json")


def _load_room_stats(room):
    with _stats_lock:
        if room in _stats:
            return _stats[room]
    data = {}
    p = _stats_path(room)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
                if isinstance(j, dict):
                    data = j
        except Exception:
            data = {}
    with _stats_lock:
        _stats.setdefault(room, data)
        return _stats[room]


def _stat_rec(rs, user, date):
    u = rs.setdefault(user, {})
    return u.setdefault(date, {"likes": 0, "gifts": 0, "items": {}})


def record_like(room, user, cnt):
    if not room:
        return
    rs = _load_room_stats(room)
    with _stats_lock:
        rec = _stat_rec(rs, user or "?", _today())
        rec["likes"] += int(cnt or 0)
        _stats_dirty.add(room)


def record_gift(room, user, gift, cnt):
    if not room:
        return
    rs = _load_room_stats(room)
    with _stats_lock:
        rec = _stat_rec(rs, user or "?", _today())
        rec["gifts"] += int(cnt or 0)
        g = gift or "礼物"
        rec["items"][g] = rec["items"].get(g, 0) + int(cnt or 0)
        _stats_dirty.add(room)


def _stats_flush():
    with _stats_lock:
        rooms = [r for r in _stats_dirty if r in _stats]
        _stats_dirty.clear()
        blobs = {r: json.dumps(_stats[r], ensure_ascii=False) for r in rooms}
    if not blobs:
        return
    try:
        os.makedirs(STATS_DIR, exist_ok=True)
    except Exception:
        pass
    for r, blob in blobs.items():
        try:
            with open(_stats_path(r), "w", encoding="utf-8") as f:
                f.write(blob)
        except Exception:
            pass


def _stats_flusher():
    while True:
        time.sleep(20)
        _stats_flush()


def stats_for_date(room, date):
    rs = _load_room_stats(room)
    likes_total = gifts_total = 0
    gift_items, gift_users, like_users = {}, {}, {}
    with _stats_lock:
        for user, bydate in rs.items():
            rec = bydate.get(date)
            if not rec:
                continue
            lk = rec.get("likes", 0)
            gf = rec.get("gifts", 0)
            likes_total += lk
            gifts_total += gf
            if gf:
                gift_users[user] = gf
            if lk:
                like_users[user] = lk
            for g, c in rec.get("items", {}).items():
                gift_items[g] = gift_items.get(g, 0) + c
    return {"room": room, "date": date, "likes_total": likes_total, "gifts_total": gifts_total,
            "gift_items": gift_items, "gift_users": gift_users, "like_users": like_users}


def stats_dates(room):
    rs = _load_room_stats(room)
    s = set()
    with _stats_lock:
        for bydate in rs.values():
            for d in bydate.keys():
                s.add(d)
    return sorted(s, reverse=True)


def stats_user(room, q):
    rs = _load_room_stats(room)
    out = {}
    ql = (q or "").lower()
    with _stats_lock:
        for user, bydate in rs.items():
            if not ql or ql in user.lower():
                out[user] = bydate
    return out


# ================= 抓取器 =================
class WebFetcher(DouyinLiveWebFetcher):
    def _wsOnMessage(self, ws, message):
        try:
            pkg = PushFrame().parse(message)
            resp = Response().parse(gzip.decompress(pkg.payload))
            for msg in resp.messages_list:
                if DIAG:
                    _diag_bump(msg.method)
                    if "Gift" in msg.method:
                        _maybe_dump_gift(msg.method, msg.payload)
                if msg.method == "WebcastLightGiftMessage":
                    try:
                        self._parseLightGiftMsg(msg.payload)
                    except Exception:
                        pass
        except Exception:
            if DIAG:
                _diag_bump("__frame_parse_error__")
        super()._wsOnMessage(ws, message)

    def _ensure_gift_list(self):
        if getattr(self, "_gift_list_done", False):
            return
        self._gift_list_done = True
        try:
            self.fetch_gift_list()
        except Exception as e:
            if DIAG:
                print("[礼物清单] 获取失败（将显示礼物id，可手动编辑 gift_names.json）:", e)

    def fetch_gift_list(self):
        """获取直播间礼物清单：id -> 名称，写入本地 gift_names.json。尽力而为。"""
        msToken = generateMsToken()
        nonce = self.get_ac_nonce()
        signature = self.get_ac_signature(nonce)
        url = ("https://live.douyin.com/webcast/gift/list/?aid=6383&app_name=douyin_web"
               "&live_id=1&device_platform=web&language=zh-CN&cookie_enabled=true"
               "&screen_width=1920&screen_height=1080&browser_language=zh-CN&browser_platform=Win32"
               "&browser_name=Edge&browser_version=140.0.0.0&fetch_giftlist_from=2"
               f"&room_id={self.room_id}&msToken={msToken}")
        query = urlparse(url).query
        params = {kv.split('=')[0]: kv.split('=')[-1] for kv in query.split('&')}
        a_bogus = self.get_a_bogus(params)
        url += f"&a_bogus={a_bogus}"
        headers = self.headers.copy()
        headers.update({
            'Referer': f'https://live.douyin.com/{self.live_id}',
            'Cookie': f'ttwid={self.ttwid};__ac_nonce={nonce}; __ac_signature={signature}',
        })
        resp = self.session.get(url, headers=headers, timeout=10)
        data = resp.json().get('data') or {}
        gifts = data.get('gifts') or []
        cnt = 0
        for g in gifts:
            gid = g.get('id')
            name = g.get('name')
            if gid is not None and name:
                _gift_names[str(gid)] = name
                cnt += 1
        if cnt:
            save_gift_names()
            if DIAG:
                print(f"[礼物清单] 已载入 {cnt} 个礼物名称")

    def _parseLightGiftMsg(self, payload):
        gid = lightgift_giftid(payload)
        if gid is None:
            return
        self._ensure_gift_list()
        gname = _gift_names.get(str(gid)) or f"礼物#{gid}"
        broadcast({"type": "gift", "name": gname, "text": "×1", "gift_id": gid})
        record_gift(self.live_id, "(匿名礼物)", gname, 1)

    def _parseRankMsg(self, payload): pass
    def _parseRoomStatsMsg(self, payload): pass
    def _parseRoomMsg(self, payload): pass
    def _parseFansclubMsg(self, payload): pass
    def _parseEmojiChatMsg(self, payload): pass
    def _parseRoomStreamAdaptationMsg(self, payload): pass

    def _parseLikeMsg(self, payload):
        m = LikeMessage().parse(payload)
        try:
            name = (m.user.nick_name if m.user else "") or "?"
        except Exception:
            name = "?"
        cnt = getattr(m, "count", 0) or 1
        total = getattr(m, "total", 0)
        text = f"点了 {cnt} 个赞" + (f"（累计 {total}）" if total else "")
        broadcast({"type": "like", "name": name, "text": text, "count": cnt})
        record_like(self.live_id, name, cnt)

    def _sendHeartbeat(self):
        while True:
            try:
                hb = PushFrame(payload_type="hb").SerializeToString()
                self.ws.send(hb, websocket.ABNF.OPCODE_PING)
            except Exception:
                break
            time.sleep(5)

    def _parseChatMsg(self, payload):
        m = ChatMessage().parse(payload)
        broadcast({"type": "chat", "name": m.user.nick_name, "text": m.content})

    def _parseGiftMsg(self, payload):
        m = GiftMessage().parse(payload)
        try:
            name = (m.user.nick_name if m.user else "") or "?"
        except Exception:
            name = "?"
        gift_name = ""
        try:
            if m.gift and m.gift.name:
                gift_name = m.gift.name
        except Exception:
            gift_name = ""
        if not gift_name:
            gift_name = "礼物"
        cnt = (getattr(m, "combo_count", 0) or getattr(m, "total_count", 0)
               or getattr(m, "repeat_count", 0) or getattr(m, "group_count", 0) or 1)
        broadcast({"type": "gift", "name": name, "text": f"送出 {gift_name} x{cnt}"})
        record_gift(self.live_id, name, gift_name, cnt)

    def _parseMemberMsg(self, payload):
        m = MemberMessage().parse(payload)
        broadcast({"type": "member", "name": m.user.nick_name, "text": "进入直播间"})

    def _parseSocialMsg(self, payload):
        m = SocialMessage().parse(payload)
        broadcast({"type": "social", "name": m.user.nick_name, "text": "关注了主播"})

    def _parseRoomUserSeqMsg(self, payload):
        m = RoomUserSeqMessage().parse(payload)
        broadcast({"type": "stats", "current": m.total, "total": m.total_pv_for_anchor})


# ================= 抓取管理器（支持切换房间） =================
class FetcherManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.fetcher = None
        self.live_id = ""

    def switch(self, live_id):
        live_id = clean_room_id(live_id)
        if not live_id:
            return ""
        with self.lock:
            if self.fetcher is not None:
                try:
                    self.fetcher.stop()
                except Exception:
                    pass
            self.live_id = live_id
            save_live_id(live_id)
            self.fetcher = WebFetcher(live_id)
            threading.Thread(target=self._run, args=(self.fetcher, live_id),
                             daemon=True).start()
        clear_history()
        broadcast({"type": "switch", "room": live_id})
        broadcast({"type": "system", "text": f"已切换到房间 {live_id}，连接中…"})
        return live_id

    def _run(self, fetcher, live_id):
        try:
            fetcher.start()
        except Exception as e:
            broadcast({"type": "system", "text": f"房间 {live_id} 连接结束/错误：{e}"})


manager = FetcherManager()


# ================= 网页 =================
PAGE = r"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>抖音弹幕</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Segoe UI","Microsoft YaHei",sans-serif;
         background:#0f1116; color:#e7e9ee; height:100vh; display:flex; flex-direction:column; }
  header { padding:10px 16px; background:#171a21; border-bottom:1px solid #262a33;
           display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  .pill { background:#222732; border-radius:999px; padding:4px 11px; font-size:13px; }
  .pill b { color:#5db0ff; }
  .dot { width:8px; height:8px; border-radius:50%; background:#3ddc84; display:inline-block; margin-right:6px; }
  .dot.off { background:#ff5a5a; }
  input, select { background:#0f1116; border:1px solid #333a47; color:#e7e9ee;
                  border-radius:7px; padding:5px 9px; font-size:13px; }
  button { background:#2b5fa5; border:0; color:#fff; border-radius:7px;
           padding:6px 13px; font-size:13px; cursor:pointer; }
  button:hover { background:#3570bd; }
  button.ghost { background:#222732; }
  button.ghost:hover { background:#2c3340; }
  .spacer { margin-left:auto; }
  #board { flex:1; display:flex; gap:10px; padding:10px; overflow-x:auto; }
  .panel { flex:0 0 auto; width:320px; min-width:220px; max-width:1100px; resize:horizontal;
           display:flex; flex-direction:column;
           background:#13161d; border:1px solid #262a33; border-radius:10px; overflow:hidden; }
  .panel::-webkit-resizer { background:#2b5fa5; }
  #board.vertical { flex-direction:column; overflow-x:hidden; overflow-y:auto; }
  #board.vertical .panel { resize:vertical; width:auto; min-width:0; max-width:none;
                           height:260px; min-height:140px; flex:0 0 auto; }
  .panel-head { display:flex; align-items:center; gap:6px; padding:7px 9px; flex-wrap:wrap; row-gap:6px;
                background:#171a21; border-bottom:1px solid #262a33; }
  .panel-head select { flex:0 0 auto; }
  .panel-head input { flex:1 1 110px; min-width:0; }
  .panel-head input.pex { border-color:#5a3a40; }
  .panel-head input.pex::placeholder { color:#9a6a72; }
  .panel-head .pcount { font-size:12px; color:#7d8597; padding:0 2px; }
  .pfont { display:inline-flex; align-items:center; gap:5px; background:#1b202a;
           border:1px solid #2c333f; border-radius:7px; padding:2px 7px; }
  .pfont .pflabel { font-size:11px; color:#8a92a3; }
  .pfont .psize { font-size:12px; color:#e7e9ee; min-width:16px; text-align:center; }
  .panel-head .pfs { background:#2b5fa5; color:#fff; padding:2px 9px; font-size:13px;
                     font-weight:600; border-radius:6px; line-height:1.1; }
  .panel-head .pfs:hover { background:#3570bd; }
  .panel-head .pclose { background:transparent; color:#8a92a3; padding:2px 6px; font-size:16px; margin-left:auto; }
  .panel-head .pclose:hover { color:#ff6b6b; }
  .panel-feed { flex:1; overflow-y:auto; padding:8px 10px; font-size:var(--dfs,14px); }
  .row { padding:5px 9px; margin:3px 0; border-radius:7px; line-height:1.4; font-size:inherit;
         animation:fade .2s ease; word-break:break-word; }
  @keyframes fade { from{opacity:0; transform:translateY(5px);} to{opacity:1; transform:none;} }
  .name { font-weight:600; margin-right:5px; }
  .chat  { background:#181c24; } .chat .name { color:#9ecbff; }
  .gift  { background:#2a1f10; } .gift .name { color:#ffcf66; }
  .member{ background:#14201a; color:#8fe3b0; font-size:0.94em; }
  .social{ background:#201826; color:#e3a0ff; font-size:0.94em; }
  .system{ background:#1d2330; color:#9aa0ad; font-size:0.9em; text-align:center; }
  .dy-sticker { display:block; max-width:120px; max-height:120px; margin-top:4px; border-radius:6px; }
  .stat-ctrl { display:flex; gap:6px; margin-bottom:8px; flex-wrap:wrap; }
  .stat-sum { background:#1b2230; border-radius:6px; padding:6px 8px; margin-bottom:6px; font-size:13px; }
  .stat-sum b { color:#ffcf66; }
  .stat-h { color:#8fb6ff; font-size:12px; margin:8px 0 3px; border-bottom:1px solid #262a33; padding-bottom:2px; }
  .stat-row { display:flex; justify-content:space-between; gap:8px; padding:2px 2px; font-size:13px; }
  .stat-n { color:#ffcf66; white-space:nowrap; }
  .stat-empty { color:#6b7280; padding:4px 2px; font-size:13px; }
  footer { padding:6px 14px; font-size:12px; color:#6b7280; background:#171a21; border-top:1px solid #262a33; }
  .row.hl{ background:var(--hl-color,rgba(245,190,50,.22))!important; box-shadow:inset 3px 0 0 var(--hl-edge,#f0b400); }
  #hlInput{ background:#10131a; color:#e6e6e6; border:1px solid #2a2f3a; border-radius:6px; padding:5px 8px; font-size:13px; }
  #hlColor{ width:30px; height:28px; padding:0; border:1px solid #2a2f3a; border-radius:6px; background:#10131a; cursor:pointer; vertical-align:middle; }
  .roomchip{ display:inline-flex; align-items:center; gap:2px; background:#1f3a2e; color:#9fe6c0; border-radius:10px; padding:1px 3px 1px 8px; margin:0 2px; font-size:12px; }
  .roomchip-x{ background:none; border:none; color:#9fe6c0; cursor:pointer; font-size:14px; line-height:1; padding:0 2px; }
  .roomchip-x:hover{ color:#ff6b6b; }
  #roomChips:empty::after{ content:'（暂无房号）'; color:#5d6677; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>抖音弹幕</h1>
  <button class="ghost" title="另開聊天室（Cloudflare 公網網址）" onclick="fetch('/chatroom_url').then(r=>r.text()).then(u=>window.open((u||'').replace(/^﻿/,'').trim()||('http://'+location.hostname+':3000'),'_blank')).catch(()=>window.open('http://'+location.hostname+':3000','_blank'))">開啟聊天室 ↗</button>
  <button class="ghost" id="switchSrcBtn" title="切换到另一版资料来源（本机 ⇄ VPS）">⇄ 另一版</button>
  <span class="pill" style="background:#173a26;">版本 <b id="ver" style="color:#7ee0a1;">__VERSION__</b></span>
  <span class="pill"><span id="dot" class="dot off"></span><span id="status">连接中…</span></span>
  <span class="pill">房间 <b id="room">-</b></span>
  <span class="pill">在线 <b id="online">-</b></span>
  <select id="savedRooms" title="已保存房间"><option value="">已保存房间 ▼</option></select>
  <button class="ghost" id="editRoomBtn" title="编辑所选房间">✏ 编辑</button>
  <button class="ghost" id="delRoomBtn" title="删除所选房间">🗑</button>
  <input id="roomInput" placeholder="房间号或网址" style="width:128px;" />
  <input id="noteInput" placeholder="备注（可空）" style="width:108px;" />
  <button id="switchBtn">切换并保存</button>
  <button class="ghost" id="cancelEditBtn" style="display:none;">取消</button>
  <span class="spacer"></span>
  <label style="font-size:13px;display:flex;align-items:center;gap:6px;">字体(全部)
    <input type="range" id="fontRange" min="11" max="50" value="14" style="width:90px;"></label>
  <label style="font-size:13px;display:flex;align-items:center;gap:6px;">窗口宽度
    <input type="range" id="widthRange" min="220" max="800" value="320" style="width:110px;"></label>
  <label style="font-size:13px;cursor:pointer;"><input type="checkbox" id="autoscroll" checked> 自动滚动</label>
  <select id="orientSel" title="窗口排列方向">
    <option value="h">横向排列</option>
    <option value="v">纵向排列</option>
  </select>
  <select id="layoutMenu" title="房间布局">
    <option value="">布局…</option>
    <option value="tpl_save">把当前布局设为默认模板</option>
    <option value="tpl_apply">套用默认模板到本房间</option>
    <option value="reset">重置本房间布局</option>
  </select>
  <input id="hlInput" placeholder="高亮關鍵字（逗號分隔）" style="width:150px;" autocomplete="off" />
  <input id="hlColor" type="color" value="#f0b400" title="高亮顏色" />
  <button class="ghost" id="addBtn">＋ 添加窗口</button>
  <span class="spacer"></span>
  <select id="roomModeSel" title="聊天室监看模式"><option value="multi">多房监看</option><option value="single">单房</option></select>
  <button class="ghost" id="addRoomPanelBtn" title="开一个聊天室监看分区">＋ 聊天室分区</button>
  <button class="ghost" id="tileRoomsBtn" title="为每个监看房号各开一个分区">⊞ 每房分区</button>
  <span class="pill" style="background:#1f3a2e;">监看房号
    <input id="watchRoomInput" placeholder="房号" style="width:72px;" />
    <button class="ghost" id="addWatchRoomBtn" title="添加房号到监看清单">＋</button>
    <span id="roomChips"></span>
  </span>
</header>
<div id="board"></div>
<footer>本机运行 · 可保存/切换房间、添加多个窗口并各自选择显示内容 · 完全结束请在控制台按 Ctrl+C</footer>

<script>
  const TYPES = [
    {v:'all',    t:'全部'},
    {v:'chat',   t:'聊天'},
    {v:'roomchat', t:'聊天室'},
    {v:'gift',   t:'礼物'},
    {v:'member', t:'进场'},
    {v:'social', t:'关注'},
    {v:'like',   t:'点赞'},
    {v:'likerank', t:'点赞排行'},
    {v:'user',   t:'指定用户'},
    {v:'stats',  t:'统计'}
  ];
  const labels = { chat:'', roomchat:'', gift:'GIFT ', member:'IN ', social:'FOLLOW ', like:'LIKE ' };
  const board = document.getElementById('board');
  const buffer = [];
  const MAXBUF = 800;
  const MAXROWS = 500;
  let panels = [];
  let curRoom = '';
  let editingOldId = '';
  let defaultWidth = 320;
  try { const w = parseInt(localStorage.getItem('dy_width')); if (w) defaultWidth = w; } catch(e){}
  const defaultHeight = 260;
  let fontSize = 14;
  try { const f = parseInt(localStorage.getItem('dy_fs')); if (f) fontSize = f; } catch(e){}
  let orient = 'h';
  try { const o = localStorage.getItem('dy_orient'); if (o === 'v' || o === 'h') orient = o; } catch(e){}
  let HL = [];
  try { const h = localStorage.getItem('dy_highlight'); if (h) HL = h.split(/[\s,，、]+/).filter(Boolean); } catch(e){}

  function applyPanelSize(p){
    if (orient === 'v'){ p.el.style.width = ''; p.el.style.height = p.height + 'px'; }
    else { p.el.style.height = ''; p.el.style.width = p.width + 'px'; }
  }
  function applyOrient(){
    if (orient === 'v') board.classList.add('vertical'); else board.classList.remove('vertical');
    panels.forEach(applyPanelSize);
  }

  function escapeHtml(s){
    s = s || '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
            .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  function applyExclude(p, ev){
    if (!p.ex) return true;
    var hay = (ev.name||'') + ' ' + (ev.text||'');
    var words = p.ex.split(/[\s,，、]+/);
    for (var i = 0; i < words.length; i++){
      if (words[i] && hay.indexOf(words[i]) !== -1) return false;
    }
    return true;
  }
  function matches(p, ev){
    if (ev.type === 'system') return true;
    if (p.type === 'user'){
      // 指定用户：只看该用户（按昵称匹配），含所有类型（聊天/礼物/进场/关注/点赞）
      if (!p.kw) return false;
      if ((ev.name||'').indexOf(p.kw) === -1) return false;
      return applyExclude(p, ev);
    }
    if (p.type === 'all'){ if (ev.type === 'like') return false; }   // 全部不含点赞（太频繁）
    else if (p.type !== ev.type) return false;
    if (p.type === 'roomchat' && p.room && p.room !== 'all' && ev.room !== p.room) return false; // 聊天室按房号分流（all=不过滤）
    if (p.kw){
      var hay = (ev.name||'') + ' ' + (ev.text||'');
      if (hay.indexOf(p.kw) === -1) return false;
    }
    return applyExclude(p, ev);
  }

  function hlMatch(ev){
    if (!HL.length) return false;
    var hay = (ev.name||'') + ' ' + (ev.text||'');
    for (var i=0;i<HL.length;i++){ if (HL[i] && hay.indexOf(HL[i]) !== -1) return true; }
    return false;
  }
  function makeRow(ev){
    const row = document.createElement('div');
    row.className = 'row ' + ev.type;
    if (ev.type === 'system'){ row.textContent = ev.text; return row; }
    const pre = labels[ev.type] || '';
    const nameHtml = '<span class="name">'+escapeHtml(pre+ev.name)+'</span>';
    if (ev.sticker_url){
      // 貼圖：用 DOM 設定 img.src（不經 innerHTML 拼接），避免屬性注入
      row.innerHTML = nameHtml + '<span></span>';
      const img = document.createElement('img');
      img.className = 'dy-sticker'; img.alt = '貼圖'; img.src = ev.sticker_url;
      row.lastChild.appendChild(img);
    } else {
      row.innerHTML = nameHtml +
        '<span>'+(ev.type==='chat'?'：':'')+escapeHtml(ev.text||'')+'</span>';
    }
    if (hlMatch(ev)) row.classList.add('hl');
    return row;
  }
  function rescanHL(){
    document.querySelectorAll('#board .row').forEach(function(row){
      if (row.classList.contains('system')) return;
      var hit = false;
      for (var i=0;i<HL.length;i++){ if (HL[i] && row.textContent.indexOf(HL[i]) !== -1){ hit = true; break; } }
      row.classList.toggle('hl', hit);
    });
  }

  function appendTo(p, ev){
    const row = makeRow(ev);
    p.feed.appendChild(row);
    if (ev.type !== 'system'){ p.count++; p.countEl.textContent = p.count; }
    while (p.feed.children.length > MAXROWS) p.feed.removeChild(p.feed.firstChild);
    const stick = function(){ if (document.getElementById('autoscroll').checked) p.feed.scrollTop = p.feed.scrollHeight; };
    stick();
    // 貼圖為非同步載入，append 當下高度尚未計入；載入完成後需再校正一次，否則自動滾動對貼圖無效
    const img = row.querySelector('img.dy-sticker');
    if (img && !img.complete) img.addEventListener('load', stick);
  }

  function renderRank(p){
    if (!p.tally) p.tally = {};
    var arr = Object.keys(p.tally).map(function(k){ return [k, p.tally[k]]; });
    arr.sort(function(a, b){ return b[1] - a[1]; });
    var total = 0; arr.forEach(function(it){ total += it[1]; });
    p.feed.innerHTML = '';
    arr.slice(0, 100).forEach(function(it, i){
      var row = document.createElement('div'); row.className = 'row chat';
      var medal = (i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : (i + 1) + '.');
      row.innerHTML = '<span class="name">' + medal + '</span>' + escapeHtml(it[0]) +
                      ' <span style="color:#ffcf66;">' + it[1] + '</span>';
      p.feed.appendChild(row);
    });
    p.count = total; p.countEl.textContent = total;
  }
  function scheduleRankRender(p){
    if (p._rankTimer) return;
    p._rankTimer = setTimeout(function(){ p._rankTimer = null; renderRank(p); }, 400);
  }
  function seedLikeRank(p){
    // 刷新后从本机已保存的统计中读回今天的点赞数据作为基底
    fetch('/stats?room='+encodeURIComponent(curRoom)+'&date='+encodeURIComponent(todayStr()))
      .then(function(r){ return r.json(); }).then(function(s){
        var lu = s.like_users || {};
        Object.keys(lu).forEach(function(k){ p.tally[k] = lu[k]; });
        renderRank(p);
      }).catch(function(){});
  }
  function rerender(p){
    if (p.type === 'stats'){ return; }   // 统计窗口自有界面，不走弹幕渲染
    if (p.type === 'likerank'){ renderRank(p); return; }
    p.feed.innerHTML = ''; p.count = 0; p.countEl.textContent = 0;
    buffer.forEach(function(ev){ if (matches(p, ev)) appendTo(p, ev); });
  }

  function todayStr(){
    var d = new Date();
    return d.getFullYear() + '-' + ('0'+(d.getMonth()+1)).slice(-2) + '-' + ('0'+d.getDate()).slice(-2);
  }
  function statListHtml(arr, max){
    if (!arr.length) return '<div class="stat-empty">（无）</div>';
    return arr.slice(0, max||20).map(function(it, i){
      return '<div class="stat-row"><span>'+(i+1)+'. '+escapeHtml(it[0])+'</span><span class="stat-n">'+it[1]+'</span></div>';
    }).join('');
  }
  function renderStats(p, s){
    var gi = Object.keys(s.gift_items||{}).map(function(k){return [k,s.gift_items[k]];}).sort(function(a,b){return b[1]-a[1];});
    var gu = Object.keys(s.gift_users||{}).map(function(k){return [k,s.gift_users[k]];}).sort(function(a,b){return b[1]-a[1];});
    var lu = Object.keys(s.like_users||{}).map(function(k){return [k,s.like_users[k]];}).sort(function(a,b){return b[1]-a[1];});
    var h = '<div class="stat-sum">点赞总数 <b>'+(s.likes_total||0)+'</b>　礼物总数 <b>'+(s.gifts_total||0)+'</b></div>';
    h += '<div class="stat-h">礼物明细</div>' + statListHtml(gi, 50);
    h += '<div class="stat-h">送礼排行</div>' + statListHtml(gu, 30);
    h += '<div class="stat-h">点赞排行</div>' + statListHtml(lu, 30);
    p.statsBox.innerHTML = h;
  }
  function renderStatsUser(p, u){
    var users = Object.keys(u||{});
    if (!users.length){ p.statsBox.innerHTML = '<div class="stat-empty">查无此用户</div>'; return; }
    var h = '';
    users.slice(0, 20).forEach(function(name){
      h += '<div class="stat-h">'+escapeHtml(name)+'</div>';
      var bydate = u[name]; var dates = Object.keys(bydate).sort().reverse();
      dates.forEach(function(d){
        var r = bydate[d];
        h += '<div class="stat-row"><span>'+d+'</span><span class="stat-n">赞'+(r.likes||0)+' / 礼'+(r.gifts||0)+'</span></div>';
      });
    });
    p.statsBox.innerHTML = h;
  }
  function loadStats(p){
    var q = (p.userInput && p.userInput.value.trim()) || '';
    if (q){
      fetch('/stats/user?room='+encodeURIComponent(curRoom)+'&user='+encodeURIComponent(q))
        .then(function(r){return r.json();}).then(function(u){ renderStatsUser(p, u); }).catch(function(){});
      return;
    }
    var date = (p.dateSel && p.dateSel.value) || todayStr();
    fetch('/stats?room='+encodeURIComponent(curRoom)+'&date='+encodeURIComponent(date))
      .then(function(r){return r.json();}).then(function(s){ renderStats(p, s); }).catch(function(){});
  }
  function buildStatsPanel(p){
    p.feed.innerHTML = '';
    var ctrl = document.createElement('div'); ctrl.className = 'stat-ctrl';
    var dsel = document.createElement('select'); dsel.title = '日期';
    var rb = document.createElement('button'); rb.textContent = '刷新';
    var us = document.createElement('input'); us.placeholder = '查用户(可空)'; us.style.flex = '1 1 90px'; us.style.minWidth = '0';
    ctrl.appendChild(dsel); ctrl.appendChild(rb); ctrl.appendChild(us);
    var box = document.createElement('div'); box.className = 'stat-box';
    p.feed.appendChild(ctrl); p.feed.appendChild(box);
    p.dateSel = dsel; p.statsBox = box; p.userInput = us;
    fetch('/stats/dates?room='+encodeURIComponent(curRoom)).then(function(r){return r.json();}).then(function(ds){
      var today = todayStr(); if (ds.indexOf(today) < 0) ds.unshift(today);
      dsel.innerHTML = '';
      ds.forEach(function(d){ var o = document.createElement('option'); o.value = d; o.textContent = d; dsel.appendChild(o); });
      dsel.value = today; loadStats(p);
    }).catch(function(){ loadStats(p); });
    dsel.addEventListener('change', function(){ loadStats(p); });
    rb.addEventListener('click', function(){ loadStats(p); });
    var ut; us.addEventListener('input', function(){ clearTimeout(ut); ut = setTimeout(function(){ loadStats(p); }, 300); });
    if (p._statsTimer) clearInterval(p._statsTimer);
    p._statsTimer = setInterval(function(){ loadStats(p); }, 15000);
  }

  // ── 聊天室監看房號清單（手動添加 + 動態收集，存 localStorage('dy_watch_rooms')）──
  let knownRooms = [];
  try { const wr = JSON.parse(localStorage.getItem('dy_watch_rooms')); if (wr && wr.length) knownRooms = wr.slice(); } catch(e){}
  function saveWatchRooms(){ try { localStorage.setItem('dy_watch_rooms', JSON.stringify(knownRooms)); } catch(e){} }
  function buildRoomOptions(selEl, selected){
    selEl.innerHTML = '';
    const optAll = document.createElement('option'); optAll.value='all'; optAll.textContent='全部'; selEl.appendChild(optAll);
    knownRooms.forEach(function(r){ const o=document.createElement('option'); o.value=r; o.textContent=r; selEl.appendChild(o); });
    if (selected && selected!=='all' && knownRooms.indexOf(selected)===-1){ const o=document.createElement('option'); o.value=selected; o.textContent=selected; selEl.appendChild(o); }
    selEl.value = selected || 'all';
  }
  function refreshRoomSelectors(){ panels.forEach(function(p){ if (p.roomSel) buildRoomOptions(p.roomSel, p.room); }); }
  function renderRoomChips(){
    const box = document.getElementById('roomChips'); if (!box) return;
    box.innerHTML = '';
    knownRooms.forEach(function(r){
      const chip = document.createElement('span'); chip.className='roomchip'; chip.textContent = r;
      const x = document.createElement('button'); x.className='roomchip-x'; x.textContent='×'; x.title='从监看清单移除';
      x.addEventListener('click', function(){ knownRooms = knownRooms.filter(function(k){ return k!==r; }); saveWatchRooms(); refreshRoomSelectors(); renderRoomChips(); });
      chip.appendChild(x); box.appendChild(chip);
    });
  }
  function collectRoom(r){
    if (!r || knownRooms.indexOf(r) !== -1) return;
    knownRooms.push(r); saveWatchRooms(); refreshRoomSelectors(); renderRoomChips();
  }

  function addPanel(cfg){
    cfg = cfg || {type:'all', kw:''};
    const el = document.createElement('div'); el.className = 'panel';
    const head = document.createElement('div'); head.className = 'panel-head';
    const sel = document.createElement('select');
    TYPES.forEach(function(o){ const op=document.createElement('option');
      op.value=o.v; op.textContent=o.t; if(o.v===cfg.type) op.selected=true; sel.appendChild(op); });
    const roomSel = document.createElement('select'); roomSel.className='proom'; roomSel.title='聊天室房号（仅聊天室类型）';
    buildRoomOptions(roomSel, cfg.room||'all');
    roomSel.style.display = (cfg.type === 'roomchat') ? '' : 'none';
    const kw = document.createElement('input'); kw.placeholder='关键字（可空）'; kw.value=cfg.kw||'';
    if (cfg.type === 'user') kw.placeholder = '用户名（必填）';
    const ex = document.createElement('input'); ex.className='pex'; ex.placeholder='排除词（空格分隔）'; ex.value=cfg.ex||'';
    const cnt = document.createElement('span'); cnt.className='pcount'; cnt.textContent='0';
    const fontGroup = document.createElement('span'); fontGroup.className='pfont';
    const flabel = document.createElement('span'); flabel.className='pflabel'; flabel.textContent='字号';
    const fminus = document.createElement('button'); fminus.className='pfs'; fminus.textContent='A−'; fminus.title='缩小本窗口字体';
    const fsize = document.createElement('span'); fsize.className='psize';
    const fplus = document.createElement('button'); fplus.className='pfs'; fplus.textContent='A+'; fplus.title='放大本窗口字体';
    fontGroup.appendChild(flabel); fontGroup.appendChild(fminus); fontGroup.appendChild(fsize); fontGroup.appendChild(fplus);
    const close = document.createElement('button'); close.className='pclose'; close.textContent='×'; close.title='关闭窗口';
    const feed = document.createElement('div'); feed.className='panel-feed';
    head.appendChild(sel); head.appendChild(roomSel); head.appendChild(kw); head.appendChild(ex); head.appendChild(cnt);
    head.appendChild(fontGroup); head.appendChild(close);
    el.appendChild(head); el.appendChild(feed);
    board.appendChild(el);

    const p = {type:cfg.type, kw:cfg.kw||'', ex:cfg.ex||'', room:(cfg.room||'all'), width:(cfg.width||defaultWidth),
               height:(cfg.height||defaultHeight), fs:(cfg.fs||fontSize),
               el:el, feed:feed, countEl:cnt, count:0, tally:{}};
    p.roomSel = roomSel;
    panels.push(p);
    applyPanelSize(p);
    function applyPanelFont(){ el.style.setProperty('--dfs', p.fs + 'px'); fsize.textContent = p.fs; }
    p.applyFont = applyPanelFont;
    applyPanelFont();
    fminus.addEventListener('click', function(){ p.fs = Math.max(10, p.fs - 1); applyPanelFont(); save(); });
    fplus.addEventListener('click', function(){ p.fs = Math.min(50, p.fs + 1); applyPanelFont(); save(); });
    if (window.ResizeObserver){
      let rsTimer;
      const ro = new ResizeObserver(function(){ clearTimeout(rsTimer);
        rsTimer = setTimeout(function(){
          if (orient === 'v') p.height = el.offsetHeight; else p.width = el.offsetWidth;
          save(); }, 300); });
      ro.observe(el);
    }
    sel.addEventListener('change', function(){
      p.type = sel.value; roomSel.style.display = (p.type==='roomchat')?'':'none'; save(); rebuildPanels();   // 切换类型重建（统计窗口需特殊界面）
    });
    roomSel.addEventListener('change', function(){ p.room = roomSel.value; rerender(p); save(); }); // 聊天室分区切房号
    let kwTimer;
    kw.addEventListener('input', function(){ clearTimeout(kwTimer);
      kwTimer=setTimeout(function(){ p.kw=kw.value.trim(); rerender(p); save(); }, 250); });
    let exTimer;
    ex.addEventListener('input', function(){ clearTimeout(exTimer);
      exTimer=setTimeout(function(){ p.ex=ex.value.trim(); rerender(p); save(); }, 250); });
    close.addEventListener('click', function(){
      if (p._statsTimer) clearInterval(p._statsTimer);
      board.removeChild(el);
      panels = panels.filter(function(x){ return x!==p; }); save(); });
    if (p.type === 'stats') buildStatsPanel(p);
    if (p.type === 'likerank') seedLikeRank(p);
    rerender(p); save();
    return p;
  }

  function panelsKey(){ return 'dy_panels:' + (curRoom || 'default'); }

  function currentCfgs(){
    return panels.map(function(p){ return {type:p.type, kw:p.kw, ex:p.ex, room:p.room, width:p.width, height:p.height, fs:p.fs}; });
  }
  function getTemplate(){
    try { const t = JSON.parse(localStorage.getItem('dy_template')); if (t && t.length) return t; } catch(e){}
    return null;
  }
  function localSystem(text){
    panels.forEach(function(p){ appendTo(p, {type:'system', text:text}); });
  }

  function save(){
    try { localStorage.setItem(panelsKey(), JSON.stringify(currentCfgs())); } catch(e){}
  }
  function loadPanels(){
    let cfgs = null;
    try { cfgs = JSON.parse(localStorage.getItem(panelsKey())); } catch(e){}
    if (!cfgs || !cfgs.length) cfgs = getTemplate();                 // 房间没保存过 -> 默认模板
    if (!cfgs || !cfgs.length){
      try { cfgs = JSON.parse(localStorage.getItem('dy_panels')); } catch(e){}  // 旧全局布局（兼容）
    }
    if (!cfgs || !cfgs.length) cfgs = [{type:'all',kw:''},{type:'gift',kw:''}];
    cfgs.forEach(addPanel);
  }
  function rebuildPanels(){
    board.innerHTML = '';
    panels = [];
    loadPanels();
    applyOrient();
  }

  function onEvent(ev){
    if (ev.type === 'stats'){ document.getElementById('online').textContent = ev.current; return; }
    if (ev.type === 'switch'){
      curRoom = ev.room;
      document.getElementById('room').textContent = ev.room;
      document.getElementById('online').textContent = '-';
      buffer.length = 0;
      rebuildPanels();   // 还原该房间各自保存的窗口布局
      loadRooms();
      return;
    }
    if (ev.type === 'roomchat' && ev.room) collectRoom(ev.room); // 動態收集聊天室房號
    buffer.push(ev); if (buffer.length > MAXBUF) buffer.shift();
    panels.forEach(function(p){
      if (p.type === 'stats') return;   // 统计窗口从服务器拉取，不接收实时事件
      if (p.type === 'likerank'){
        if (ev.type === 'like'){
          var key = ev.name || '?';
          p.tally[key] = (p.tally[key] || 0) + (ev.count || 1);
          scheduleRankRender(p);
        }
        return;
      }
      if (matches(p, ev)) appendTo(p, ev);
    });
  }

  function loadRooms(){
    fetch('/rooms').then(function(r){ return r.json(); }).then(function(list){
      const sel = document.getElementById('savedRooms');
      sel.innerHTML = '<option value="">已保存房间 ▼</option>';
      list.forEach(function(r){
        const o = document.createElement('option');
        o.value = r.id;
        o.textContent = (r.note || r.id) + '（' + r.id + '）';
        if (r.id === curRoom) o.selected = true;
        sel.appendChild(o);
      });
    }).catch(function(){});
  }

  function setEditing(oldId, note){
    editingOldId = oldId || '';
    const sw = document.getElementById('switchBtn');
    const cancel = document.getElementById('cancelEditBtn');
    if (editingOldId){
      sw.textContent = '保存修改';
      cancel.style.display = '';
    } else {
      sw.textContent = '切换并保存';
      cancel.style.display = 'none';
    }
  }

  function doSwitch(){
    const v = document.getElementById('roomInput').value.trim();
    const note = document.getElementById('noteInput').value.trim();
    if (!v) return;
    if (editingOldId){
      // 编辑模式：仅更新已保存的房号/备注，不切换
      fetch('/rooms/edit?old=' + encodeURIComponent(editingOldId) +
            '&room=' + encodeURIComponent(v) + '&note=' + encodeURIComponent(note))
        .then(function(r){ return r.json(); })
        .then(function(){ document.getElementById('roomInput').value='';
                          document.getElementById('noteInput').value='';
                          setEditing('', ''); loadRooms(); })
        .catch(function(){});
      return;
    }
    fetch('/switch?room=' + encodeURIComponent(v) + '&note=' + encodeURIComponent(note))
      .then(function(r){ return r.json(); })
      .then(function(j){ if (j.ok){ document.getElementById('roomInput').value='';
                                    document.getElementById('noteInput').value=''; } })
      .catch(function(){});
  }

  document.getElementById('switchBtn').addEventListener('click', doSwitch);
  document.getElementById('roomInput').addEventListener('keydown', function(e){ if(e.key==='Enter') doSwitch(); });
  document.getElementById('noteInput').addEventListener('keydown', function(e){ if(e.key==='Enter') doSwitch(); });
  document.getElementById('addBtn').addEventListener('click', function(){ addPanel({type:'all',kw:''}); });
  (function(){
    var hi = document.getElementById('hlInput');
    hi.value = HL.join(' ');
    hi.addEventListener('input', function(){
      HL = hi.value.split(/[\s,，、]+/).filter(Boolean);
      try { localStorage.setItem('dy_highlight', hi.value); } catch(e){}
      rescanHL();
    });
    var hc = document.getElementById('hlColor');
    function hexToRgba(hex, a){ var h = hex.replace('#',''); return 'rgba('+parseInt(h.slice(0,2),16)+','+parseInt(h.slice(2,4),16)+','+parseInt(h.slice(4,6),16)+','+a+')'; }
    function applyHlColor(hex){ var s = document.documentElement.style; s.setProperty('--hl-color', hexToRgba(hex, 0.28)); s.setProperty('--hl-edge', hex); }
    try { var sv = localStorage.getItem('dy_hlcolor'); if (sv) hc.value = sv; } catch(e){}
    applyHlColor(hc.value);
    hc.addEventListener('input', function(){ try { localStorage.setItem('dy_hlcolor', hc.value); } catch(e){} applyHlColor(hc.value); });
  })();

  document.getElementById('layoutMenu').addEventListener('change', function(e){
    const v = e.target.value;
    e.target.value = '';
    if (v === 'tpl_save'){
      try { localStorage.setItem('dy_template', JSON.stringify(currentCfgs())); } catch(err){}
      localSystem('已把当前布局设为「默认模板」，新房间将自动套用');
    } else if (v === 'tpl_apply'){
      let cfgs = getTemplate();
      if (!cfgs){ localSystem('尚未设定默认模板'); return; }
      board.innerHTML = ''; panels = [];
      cfgs.forEach(addPanel); save();
      localSystem('已套用默认模板到本房间');
    } else if (v === 'reset'){
      try { localStorage.removeItem(panelsKey()); } catch(err){}
      rebuildPanels();
      localSystem('已重置本房间布局');
    }
  });

  document.getElementById('savedRooms').addEventListener('change', function(e){
    const id = e.target.value; if (!id) return;
    if (editingOldId) setEditing('', '');   // 切换房间时退出编辑模式
    fetch('/switch?room=' + encodeURIComponent(id)).then(function(r){ return r.json(); }).catch(function(){});
  });
  document.getElementById('delRoomBtn').addEventListener('click', function(){
    const sel = document.getElementById('savedRooms');
    const id = sel.value; if (!id) return;
    fetch('/rooms/del?room=' + encodeURIComponent(id)).then(function(){ loadRooms(); }).catch(function(){});
  });
  document.getElementById('editRoomBtn').addEventListener('click', function(){
    const sel = document.getElementById('savedRooms');
    const id = sel.value; if (!id) return;
    const opt = sel.options[sel.selectedIndex];
    let note = opt.textContent || '';
    const i = note.lastIndexOf('（'); if (i >= 0) note = note.slice(0, i);
    document.getElementById('roomInput').value = id;
    document.getElementById('noteInput').value = note;
    setEditing(id, note);
    document.getElementById('noteInput').focus();
  });
  document.getElementById('cancelEditBtn').addEventListener('click', function(){
    document.getElementById('roomInput').value='';
    document.getElementById('noteInput').value='';
    setEditing('', '');
  });

  const orientSel = document.getElementById('orientSel');
  orientSel.value = orient;
  orientSel.addEventListener('change', function(){
    orient = orientSel.value === 'v' ? 'v' : 'h';
    try { localStorage.setItem('dy_orient', orient); } catch(e){}
    applyOrient();
  });

  const wr = document.getElementById('widthRange');
  wr.value = defaultWidth;
  wr.addEventListener('input', function(){
    defaultWidth = parseInt(wr.value) || 320;
    try { localStorage.setItem('dy_width', defaultWidth); } catch(e){}
    panels.forEach(function(p){ p.width = defaultWidth; applyPanelSize(p); });
    save();
  });

  document.documentElement.style.setProperty('--dfs', fontSize + 'px');
  const fr = document.getElementById('fontRange');
  fr.value = fontSize;
  fr.addEventListener('input', function(){
    fontSize = parseInt(fr.value) || 14;
    try { localStorage.setItem('dy_fs', fontSize); } catch(e){}
    panels.forEach(function(p){ p.fs = fontSize; if (p.applyFont) p.applyFont(); });
    save();
  });

  // ── 聊天室監看：模式切換 + 房號管理 init ──
  let roomMode = 'multi';
  try { const m = localStorage.getItem('dy_room_mode'); if (m) roomMode = m; } catch(e){}
  function applyRoomMode(){
    const ms = document.getElementById('roomModeSel'); if (ms) ms.value = roomMode;
    const tb = document.getElementById('tileRoomsBtn'); if (tb) tb.style.display = (roomMode==='multi') ? '' : 'none';
  }
  document.getElementById('roomModeSel').addEventListener('change', function(){
    roomMode = this.value; try { localStorage.setItem('dy_room_mode', roomMode); } catch(e){} applyRoomMode();
  });
  document.getElementById('addRoomPanelBtn').addEventListener('click', function(){ addPanel({type:'roomchat', room:'all'}); save(); });
  document.getElementById('tileRoomsBtn').addEventListener('click', function(){
    if (!knownRooms.length){ alert('尚无房号，可手动添加或等聊天室訊息进来'); return; }
    knownRooms.forEach(function(r){ addPanel({type:'roomchat', room:r}); }); save();
  });
  document.getElementById('addWatchRoomBtn').addEventListener('click', function(){
    const inp = document.getElementById('watchRoomInput'); const v = (inp.value||'').trim().slice(0,32); if (!v) return;
    if (knownRooms.indexOf(v) === -1){ knownRooms.push(v); saveWatchRooms(); refreshRoomSelectors(); renderRoomChips(); }
    inp.value = '';
  });
  document.getElementById('watchRoomInput').addEventListener('keydown', function(e){ if (e.key==='Enter') document.getElementById('addWatchRoomBtn').click(); });
  renderRoomChips(); applyRoomMode();

  // 切換資料來源版本（本機 localhost ⇄ VPS 167.179.84.87）
  (function(){
    var btn = document.getElementById('switchSrcBtn'); if (!btn) return;
    var isVPS = (location.hostname === '167.179.84.87');
    btn.textContent = isVPS ? '⇄ 切到本机版' : '⇄ 切到VPS版';
    btn.addEventListener('click', function(){
      window.open(isVPS ? 'http://localhost:8765/' : 'http://167.179.84.87:8765/', '_blank');
    });
  })();

  const es = new EventSource('/stream');
  es.onopen = function(){ document.getElementById('dot').classList.remove('off');
                          document.getElementById('status').textContent='已连接'; };
  es.onerror = function(){ document.getElementById('dot').classList.add('off');
                           document.getElementById('status').textContent='连接中断，重试中…'; };
  es.onmessage = function(e){ try { onEvent(JSON.parse(e.data)); } catch(_){} };

  fetch('/room').then(function(r){ return r.text(); }).then(function(t){
    curRoom=t; document.getElementById('room').textContent=t; loadRooms(); rebuildPanels();
  }).catch(function(){ rebuildPanels(); });
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/plain; charset=utf-8", code=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not AUTH_PASSWORD:
            return True
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                dec = base64.b64decode(hdr[6:]).decode("utf-8", "ignore")
                _, _, pw = dec.partition(":")
                if pw == AUTH_PASSWORD:
                    return True
            except Exception:
                pass
        return False

    def _require_auth(self):
        # 統一鑑權閘門（do_GET / do_POST 共用，避免漏掉造成端點裸奔）
        if self._authed():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Douyin Danmaku"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("需要密码 / Authentication required".encode("utf-8"))
        return False

    def do_POST(self):
        if not self._require_auth():
            return
        path = urlparse(self.path).path

        # chatroom 模組把房間訊息灌進弹幕同頁（type=roomchat，走同一條 SSE）
        if path == "/chat/ingest":
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length > 0 else b""
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                self._send(json.dumps({"ok": False, "err": "bad json"}), "application/json; charset=utf-8", 400)
                return
            name = (str(data.get("name", "")).strip() or "匿名")[:32]
            text = str(data.get("text", "")).strip()[:500]
            room = str(data.get("room", "")).strip()[:32]
            if not text:
                self._send(json.dumps({"ok": False, "err": "empty"}), "application/json; charset=utf-8", 400)
                return
            ev_type = str(data.get("type", "roomchat")).strip()
            if ev_type not in ("roomchat", "chat", "gift", "member", "social", "like"):
                ev_type = "roomchat"
            label = f"[{room}] {name}" if room else name
            ev = {"type": ev_type, "name": label, "text": text}
            if room:
                ev["room"] = room  # 獨立 room 欄位供前端按房號分流（保留 label 維持單房顯示零回歸）
            # 貼圖：僅接受本機 chatroom 的貼圖 URL，避免任意外部 URL 注入 webUI
            sticker_url = str(data.get("sticker_url", "")).strip()
            if re.match(r'^http://127\.0\.0\.1:\d+/stickers/', sticker_url):
                ev["sticker_url"] = sticker_url[:500]
            broadcast(ev)
            self._send(json.dumps({"ok": True}), "application/json; charset=utf-8")
            return

        self._send("Not Found", "text/plain; charset=utf-8", 404)

    def do_GET(self):
        if not self._require_auth():
            return

        path = urlparse(self.path).path

        if path == "/" or path.startswith("/index"):
            self._send(PAGE.replace("__VERSION__", VERSION), "text/html; charset=utf-8")
            return

        if path == "/chatroom_url":
            url = ""
            try:
                if os.path.exists(CHAT_URL_FILE):
                    url = open(CHAT_URL_FILE, encoding="utf-8-sig").read().strip()
            except Exception:
                pass
            self._send(url, "text/plain; charset=utf-8")
            return

        if path == "/room":
            self._send(str(manager.live_id))
            return

        if path == "/rooms":
            self._send(json.dumps(load_rooms(), ensure_ascii=False),
                       "application/json; charset=utf-8")
            return

        if path == "/rooms/del":
            qs = parse_qs(urlparse(self.path).query)
            rid = (qs.get("room", [""])[0] or "").strip()
            del_room(rid)
            self._send(json.dumps({"ok": True}), "application/json; charset=utf-8")
            return

        if path == "/rooms/edit":
            qs = parse_qs(urlparse(self.path).query)
            old = (qs.get("old", [""])[0] or "").strip()
            new = (qs.get("room", [""])[0] or "").strip()
            note = (qs.get("note", [""])[0] or "").strip()
            edit_room(old, new, note)
            self._send(json.dumps({"ok": True}), "application/json; charset=utf-8")
            return

        if path == "/stats":
            qs = parse_qs(urlparse(self.path).query)
            room = (qs.get("room", [""])[0] or "").strip() or manager.live_id
            date = (qs.get("date", [""])[0] or "").strip() or _today()
            self._send(json.dumps(stats_for_date(room, date), ensure_ascii=False),
                       "application/json; charset=utf-8")
            return

        if path == "/stats/dates":
            qs = parse_qs(urlparse(self.path).query)
            room = (qs.get("room", [""])[0] or "").strip() or manager.live_id
            self._send(json.dumps(stats_dates(room), ensure_ascii=False),
                       "application/json; charset=utf-8")
            return

        if path == "/stats/user":
            qs = parse_qs(urlparse(self.path).query)
            room = (qs.get("room", [""])[0] or "").strip() or manager.live_id
            user = (qs.get("user", [""])[0] or "").strip()
            self._send(json.dumps(stats_user(room, user), ensure_ascii=False),
                       "application/json; charset=utf-8")
            return

        if path == "/switch":
            qs = parse_qs(urlparse(self.path).query)
            raw = (qs.get("room", [""])[0] or "").strip()
            note = (qs.get("note", [""])[0] or "").strip()
            new_id = manager.switch(raw)
            if new_id:
                add_room(new_id, note)
            self._send(json.dumps({"ok": bool(new_id), "room": new_id}),
                       "application/json; charset=utf-8")
            return

        if path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = queue.Queue(maxsize=2000)
            with _sub_lock:
                snapshot = list(_history)   # 与订阅原子化，避免重复/遗漏
                _subscribers.append(q)
            _client_connected()
            try:
                self.wfile.write(b": connected\n\n")
                for d in snapshot:
                    self.wfile.write(("data: " + d + "\n\n").encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _sub_lock:
                    if q in _subscribers:
                        _subscribers.remove(q)
                _client_disconnected()
            return

        self.send_response(404)
        self.end_headers()


def main():
    global AUTH_PASSWORD
    AUTH_PASSWORD = load_password()
    load_gift_names()

    live_id = load_live_id()
    if not live_id:
        live_id = input("输入抖音直播间号 (live.douyin.com/ 后面的数字): ").strip()
    if not live_id:
        print("未提供直播间号，结束。")
        sys.exit(1)

    bind_host = os.environ.get("DY_BIND", "127.0.0.1")  # 預設僅本機；VPS 設 DY_BIND=0.0.0.0 對外（webUI 切換來源用）
    server = ThreadingHTTPServer((bind_host, PORT), Handler)
    server.daemon_threads = True

    manager.switch(live_id)
    add_room(live_id)

    if DIAG:
        threading.Thread(target=_diag_printer, daemon=True).start()
    threading.Thread(target=_stats_flusher, daemon=True).start()

    url = f"http://127.0.0.1:{PORT}/"
    print("=" * 52)
    print(f"  抖音弹幕 Web UI 已启动  {VERSION}")
    print(f"  房间：{manager.live_id}")
    print(f"  在浏览器打开：{url}")
    print("  （按 Ctrl+C 结束）")
    print("=" * 52)
    if os.environ.get("DY_NO_BROWSER", "").strip() == "":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
