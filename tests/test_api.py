"""端到端接口测试：登录、上传、鉴权问答、FAQ 沉淀与缓存、知识缺口（对应验收第 2/4/5/7/8 条）。"""
from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid

import helpers
from app import config, db, server


def _multipart(fields: dict, files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "----kbtest" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for name, filename, content in files:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: text/plain\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class ApiTest(unittest.TestCase):
    PORT = 8899
    BASE = f"http://127.0.0.1:{PORT}"

    @classmethod
    def setUpClass(cls):
        helpers.reset_db()
        cls.org = helpers.make_org()
        cls.staff_ctx = helpers.staff_ctx(cls.org)
        cls.travel = helpers.make_doc(
            "差旅报销标准",
            "差旅报销标准\n第一章 住宿标准\n一线城市住宿标准为每人每晚 500 元，新一线城市 400 元，其他城市 300 元。\n"
            "第二章 交通标准\n市内交通优先公共交通，网约车单次不超过 100 元。\n",
            cls.staff_ctx, {"global": True})
        cls.comp = helpers.make_doc(
            "高管薪酬与股权激励细则",
            "高管薪酬与股权激励细则\n第一章 薪酬结构\n基本年薪占年度现金薪酬的 60%，绩效奖金占 40%。\n"
            "第二章 股权激励\n限制性股票授予价格为授予日收盘价的 50%，分四年归属。\n",
            cls.staff_ctx, {"departments": [cls.org["hr"]]})

        cls.httpd = server.create_server("127.0.0.1", cls.PORT)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # ---------------------------------------------------------------- 工具
    def call(self, method, path, body=None, token=None, raw=None, content_type=None):
        data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        headers = {}
        if data is not None:
            headers["Content-Type"] = content_type or "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.BASE + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def login(self, username, password="123456"):
        status, body = self.call("POST", "/api/auth/login", {"username": username, "password": password})
        self.assertEqual(status, 200, body)
        return body["data"]["token"]

    # ---------------------------------------------------------------- 用例
    def test_01_login_and_auth_guard(self):
        status, body = self.call("POST", "/api/auth/login", {"username": "zhang", "password": "bad"})
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "invalid_credentials")
        status, _ = self.call("GET", "/api/kb")
        self.assertEqual(status, 401)
        token = self.login("zhang")
        self.assertTrue(token.count(".") == 2)

    def test_02_upload_multipart(self):
        token = self.login("wang")
        data = "员工培训与认证支持政策\n每人每年培训预算 3000 元，外部培训需提前 15 天申请。\n".encode()
        raw, ctype = _multipart({"category": "人力资源"}, [("files", "培训政策.txt", data)])
        status, body = self.call("POST", "/api/ingest/upload", token=token, raw=raw, content_type=ctype)
        self.assertEqual(status, 200, body)
        result = body["data"]["results"][0]
        self.assertTrue(result["ok"], result)
        ku = db.query_one("SELECT * FROM knowledge_units WHERE id=?", (result["ku_id"],))
        self.assertEqual(ku["format"], "txt")
        self.assertGreater(ku["chunk_count"], 0)

    def test_03_ask_allowed_with_citations(self):
        token = self.login("zhang")
        status, body = self.call("POST", "/api/chat/ask",
                                 {"question": "一线城市住宿标准是多少？"}, token)
        self.assertEqual(status, 200, body)
        data = body["data"]
        self.assertTrue(data["citations"], "应返回引用溯源卡片")
        self.assertIn(data["citations"][0]["ku_id"], (self.travel,))
        self.assertIn("500", data["answer"])
        self.assertNotIn("限制性股票", data["answer"], "未授权内容不得出现在回答中")

    def test_04_ask_denied_never_leaks(self):
        token = self.login("zhang")
        status, body = self.call("POST", "/api/chat/ask", {"question": "高管薪酬怎么算？"}, token)
        self.assertEqual(status, 200)
        data = body["data"]
        self.assertGreater(data["denied"], 0)
        self.assertIn("无权查阅", data["answer"])
        self.assertNotIn("限制性股票", data["answer"], "未授权内容不得出现在回答中")
        self.assertEqual(data["citations"], [])

    def test_05_department_member_can_access(self):
        token = self.login("wang")
        status, body = self.call("POST", "/api/chat/ask", {"question": "高管薪酬怎么算？"}, token)
        self.assertEqual(status, 200)
        self.assertIn("薪酬", body["data"]["answer"])
        self.assertTrue(any(c["ku_id"] == self.comp for c in body["data"]["citations"]))

    def test_06_faq_publish_and_cache_hit(self):
        token = self.login("wang")
        q = "一线城市住宿标准是多少"
        variants = ["一线城市住宿标准是多少", "一线城市住宿标准是多少元",
                    "一线城市的住宿标准是多少", "一线城市住宿标准多少钱",
                    "一线城市住宿标准是多少呢", "请问一线城市住宿标准是多少"]
        for variant in variants:
            for _ in range(2):
                self.call("POST", "/api/chat/ask", {"question": variant}, token)
        status, body = self.call("POST", "/api/faq/mine", {"days": 30}, token)
        self.assertEqual(status, 200, body)
        status, body = self.call("GET", "/api/faq/candidates", token=token)
        pending = [c for c in body["data"]["items"] if c["status"] == "pending"]
        self.assertTrue(pending, "应生成候选 FAQ")
        cid = pending[0]["id"]
        status, body = self.call("POST", f"/api/faq/candidates/{cid}/publish", {}, token)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["data"]["faq_id"])
        status, body = self.call("POST", "/api/chat/ask", {"question": q}, token)
        self.assertTrue(body["data"]["cache_hit"], "发布后同义提问应命中 FAQ 缓存")
        self.assertLess(body["data"]["latency_ms"], 2000)

    def test_07_gap_detection_and_frequency(self):
        token = self.login("zhang")
        question = "海外直邮保税仓清关延误怎么办"
        for _ in range(2):
            self.call("POST", "/api/chat/ask", {"question": question}, token)
        staff_token = self.login("wang")
        status, body = self.call("GET", "/api/gaps?status=open", token=staff_token)
        self.assertEqual(status, 200)
        row = next((g for g in body["data"]["items"] if "清关" in g["question"]), None)
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row["freq"], 2)
        status, body = self.call("POST", f"/api/gaps/{row['id']}/task", {"owner": "王五"}, staff_token)
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["task_id"])

    def test_07b_management_read_api_requires_menu_perm(self):
        """普通用户不能直接读取台账详情（防止猜 ID 越权）。"""
        token = self.login("zhang")
        status, _ = self.call("GET", f"/api/kb/{self.comp}", token=token)
        self.assertEqual(status, 403)
        status, _ = self.call("GET", f"/api/kb/{self.comp}/chunks", token=token)
        self.assertEqual(status, 403)
        status, _ = self.call("GET", "/api/kb", token=token)
        self.assertEqual(status, 403)

    def test_08_dashboard_metrics(self):
        token = self.login("zhang")
        status, body = self.call("GET", "/api/dashboard/overview?days=7", token=token)
        self.assertEqual(status, 200)
        kpi = body["data"]["kpi"]
        self.assertGreater(kpi["pv"], 0)
        self.assertGreaterEqual(kpi["uv"], 1)
        self.assertIn("cache_hit_rate", kpi)
        self.assertEqual(len(body["data"]["trend"]), 7)

    def test_09_permission_api_requires_perm(self):
        token = self.login("zhang")   # 普通用户无 kb:perm
        status, body = self.call("PUT", f"/api/kb/{self.travel}/perms", {"global": True}, token)
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
