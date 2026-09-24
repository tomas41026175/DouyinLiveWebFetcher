# coding: utf-8
# web_danmaku 單元測試：密碼驗證開關、今日音浪統計、連擊禮物去重、HTTP 鑑權。
# 只用標準庫 unittest（需先裝 requirements.txt）。
# 用法（repo 根目錄）： python -m unittest discover -s tests -v

import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import web_danmaku as w  # noqa: E402
from protobuf.douyin import GiftMessage, GiftStruct, User  # noqa: E402

ROOM = "R1"


def gift_payload(uid, gid, group_id, combo, diamond, name="小心心"):
    return bytes(GiftMessage(
        gift_id=gid, group_id=group_id, combo_count=combo,
        user=User(id=uid, nick_name=f"u{uid}"),
        gift=GiftStruct(id=gid, name=name, diamond_count=diamond)))


class _IsolatedCwd(unittest.TestCase):
    """web_danmaku 以相對路徑讀寫 config.json / stats / gift_diamonds.json，每個測試在獨立暫存目錄執行。"""

    def setUp(self):
        self._old_cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp()
        os.chdir(self._tmp)
        self._old_env = os.environ.pop("DY_AUTH", None)
        w._stats.clear()
        w._stats_dirty.clear()
        w._gift_names.clear()
        w._gift_diamonds.clear()
        self._old_broadcast = w.broadcast
        w.broadcast = lambda ev: None   # 不需要 SSE 推播

    def tearDown(self):
        w.broadcast = self._old_broadcast
        os.environ.pop("DY_AUTH", None)
        if self._old_env is not None:
            os.environ["DY_AUTH"] = self._old_env
        os.chdir(self._old_cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write_config(self, data):
        with open(w.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)


class AuthEnabledTest(_IsolatedCwd):
    def test_default_on_without_config(self):
        self.assertTrue(w.auth_enabled())

    def test_config_disables(self):
        self.write_config({"auth": {"enabled": False}})
        self.assertFalse(w.auth_enabled())

    def test_config_missing_field_defaults_on(self):
        self.write_config({"services": {"danmaku": True}})
        self.assertTrue(w.auth_enabled())

    def test_broken_config_defaults_on(self):
        with open(w.CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertTrue(w.auth_enabled())

    def test_env_overrides_config(self):
        self.write_config({"auth": {"enabled": True}})
        os.environ["DY_AUTH"] = "0"
        self.assertFalse(w.auth_enabled())
        self.write_config({"auth": {"enabled": False}})
        os.environ["DY_AUTH"] = "1"
        self.assertTrue(w.auth_enabled())


class GiftStatsTest(_IsolatedCwd):
    def setUp(self):
        super().setUp()
        self.f = w.WebFetcher.__new__(w.WebFetcher)   # 不連線，只測解析
        self.f.live_id = ROOM

    def today(self):
        return w.stats_for_date(ROOM, w._today())

    def test_combo_counts_only_increment(self):
        # 連擊 x1→x3，再加一則 repeat_end 的 x3：只算 3 件
        for c in (1, 2, 3, 3):
            self.f._parseGiftMsg(gift_payload(1, 100, 555, c, 10))
        s = self.today()
        self.assertEqual(s["gifts_total"], 3)
        self.assertEqual(s["diamonds_total"], 30)
        self.assertEqual(s["diamond_users"], {"u1": 30})

    def test_separate_combo_groups_are_both_counted(self):
        self.f._parseGiftMsg(gift_payload(1, 100, 555, 2, 10))
        self.f._parseGiftMsg(gift_payload(1, 100, 556, 1, 10))
        self.assertEqual(self.today()["diamonds_total"], 30)

    def test_non_combo_without_group_counts_each(self):
        self.f._parseGiftMsg(gift_payload(2, 200, 0, 1, 1, "玫瑰"))
        self.f._parseGiftMsg(gift_payload(2, 200, 0, 1, 1, "玫瑰"))
        s = self.today()
        self.assertEqual(s["gifts_total"], 2)
        self.assertEqual(s["diamonds_total"], 2)
        self.assertEqual(s["gift_items"], {"玫瑰": 2})

    def test_unit_price_saved_and_used_as_fallback(self):
        self.f._parseGiftMsg(gift_payload(1, 100, 0, 1, 52))
        self.assertEqual(w._gift_diamonds["100"], 52)
        with open(w.GIFT_DIAMONDS_PATH, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"100": 52})
        # 訊息沒帶單價時，用已記下的單價
        self.f._parseGiftMsg(gift_payload(3, 100, 0, 1, 0))
        self.assertEqual(self.today()["diamond_users"], {"u1": 52, "u3": 52})

    def test_unknown_price_counts_zero_diamonds(self):
        self.f._parseGiftMsg(gift_payload(1, 999, 0, 1, 0, "神秘"))
        s = self.today()
        self.assertEqual(s["gifts_total"], 1)
        self.assertEqual(s["diamonds_total"], 0)

    def test_old_stats_without_diamonds_field(self):
        # 舊版統計檔沒有 diamonds 欄位，讀取與累加都不能出錯
        os.makedirs(w.STATS_DIR)
        with open(w._stats_path(ROOM), "w", encoding="utf-8") as f:
            json.dump({"old": {w._today(): {"likes": 5, "gifts": 2, "items": {"玫瑰": 2}}}}, f)
        self.assertEqual(self.today()["diamonds_total"], 0)
        w.record_gift(ROOM, "old", "玫瑰", 1, 1)
        s = self.today()
        self.assertEqual(s["gifts_total"], 3)
        self.assertEqual(s["diamonds_total"], 1)


class HttpAuthTest(_IsolatedCwd):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), w.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        super().setUp()
        self._old_pw = w.AUTH_PASSWORD
        w.manager.live_id = ROOM
        w.record_gift(ROOM, "u1", "小心心", 3, 30)

    def tearDown(self):
        w.AUTH_PASSWORD = self._old_pw
        super().tearDown()

    def get(self, path, pw=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if pw is not None:
            token = base64.b64encode(f":{pw}".encode()).decode()
            req.add_header("Authorization", "Basic " + token)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, ""

    def test_auth_off_allows_anonymous(self):
        w.AUTH_PASSWORD = ""
        code, body = self.get("/stats")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["diamonds_total"], 30)

    def test_auth_on_requires_password(self):
        w.AUTH_PASSWORD = "pw"
        self.assertEqual(self.get("/stats")[0], 401)
        self.assertEqual(self.get("/stats", "wrong")[0], 401)
        self.assertEqual(self.get("/stats", "pw")[0], 200)

    def test_page_has_diamonds_pill(self):
        w.AUTH_PASSWORD = ""
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn('id="diamondsToday"', body)


if __name__ == "__main__":
    unittest.main()
