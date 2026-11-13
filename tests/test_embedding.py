"""向量化适配层测试：OpenAI 兼容接口解析、维度校验、失败降级与签名（对应 BGE-M3 接入）。"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import embed  # noqa: E402

DIM = 8


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: A003
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode())
        if self.path != "/v1/embeddings":
            self.send_response(404)
            self.end_headers()
            return
        if (self.headers.get("authorization") or "") != "Bearer test-key":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"bad key"}')
            return
        texts = payload["input"]
        if isinstance(texts, str):
            texts = [texts]
        if payload.get("model") == "mismatch-model":
            vec = [0.1] * 16
            data = [{"index": i, "embedding": vec} for i in range(len(texts))]
        else:
            data = []
            for i, t in enumerate(texts):
                seed = sum(ord(c) for c in t) % 97 or 1
                vec = [((seed * (k + 1)) % 13) / 13.0 for k in range(DIM)]
                data.append({"index": i, "embedding": vec})
        body = json.dumps({"data": data, "model": payload.get("model")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class EmbeddingAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def cfg(self, **kw):
        base = {
            "embedding_mode": "openai",
            "embedding_base_url": f"http://127.0.0.1:{self.port}/v1",
            "embedding_api_key": "test-key",
            "embedding_model": "BAAI/bge-m3",
            "embedding_dim": DIM,
            "timeout": 5,
        }
        base.update(kw)
        return base

    def test_api_embedding_returns_normalized_vectors(self):
        vecs = embed.embed_texts(["差旅住宿标准", "生鲜退款"], self.cfg())
        self.assertEqual(len(vecs), 2)
        self.assertEqual(len(vecs[0]), DIM)
        for v in vecs:
            norm = sum(x * x for x in v) ** 0.5
            self.assertAlmostEqual(norm, 1.0, places=5)

    def test_batching_keeps_order(self):
        texts = [f"问题{i}" for i in range(37)]
        vecs = embed.embed_texts(texts, self.cfg(embedding_batch=8))
        self.assertEqual(len(vecs), 37)
        # 同一文本在不同批次应得到相同向量
        again = embed.embed_texts([texts[0], texts[-1]], self.cfg(embedding_batch=1))
        self.assertAlmostEqual(vecs[0][0], again[0][0], places=6)
        self.assertAlmostEqual(vecs[-1][0], again[1][0], places=6)

    def test_dimension_mismatch_raises(self):
        with self.assertRaises(embed.EmbeddingError) as ctx:
            embed.embed("差旅", self.cfg(embedding_model="mismatch-model"), strict=True)
        self.assertIn("维度不符", str(ctx.exception))

    def test_bad_key_fails_fast(self):
        cfg = self.cfg(embedding_api_key="wrong-key")
        with self.assertRaises(embed.EmbeddingError):
            embed.embed("差旅", cfg, strict=True)

    def test_downtime_falls_back_to_local_when_not_strict(self):
        cfg = self.cfg()
        cfg["embedding_base_url"] = "http://127.0.0.1:9/v1"      # 不可达端口
        cfg["timeout"] = 1
        vec = embed.embed("差旅住宿标准", cfg, strict=False)
        self.assertEqual(len(vec), embed.config.VEC_DIM)

    def test_signature_and_describe(self):
        self.assertTrue(embed.signature(self.cfg()).startswith("openai:BAAI/bge-m3"))
        self.assertTrue(embed.signature({"embedding_mode": "local"}).startswith("local:hash"))
        info = embed.describe(self.cfg())
        self.assertEqual(info["mode"], "openai")
        self.assertIn(info["key_masked"], ("已配置", "test-key"))


if __name__ == "__main__":
    unittest.main()
