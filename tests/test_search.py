"""解析 / 切片 / 混合检索单元测试（对应验收第 2 条）。"""
from __future__ import annotations

import unittest
from pathlib import Path

import helpers
from app import config, parsers, search
from app.chunker import chunk_doc
from app.parsers import parse_text


class ParsingAndSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helpers.reset_db()
        cls.org = helpers.make_org()
        cls.staff = helpers.staff_ctx(cls.org)
        text = (
            "差旅报销标准\n"
            "第一章 住宿标准\n"
            "一线城市住宿标准为每人每晚 500 元，新一线城市为 400 元，其他城市为 300 元。\n"
            "第二章 交通标准\n"
            "市内交通优先公共交通，网约车单次不超过 100 元。\n"
            "第三章 餐费补贴\n"
            "出差期间按自然日发放餐补，一线城市 120 元每天。\n"
        )
        cls.doc = parse_text(text, "txt")
        cls.ku = helpers.make_doc("差旅报销标准", text, cls.staff, {"global": True})

    def test_parse_blocks_and_offsets(self):
        self.assertGreaterEqual(len(self.doc.blocks), 4)
        prev_end = 0
        for b in self.doc.blocks:
            self.assertEqual(b.char_start, prev_end)
            self.assertGreater(b.char_end, b.char_start)
            prev_end = b.char_end
        self.assertGreater(self.doc.text_length, 100)

    def test_chunking_metadata(self):
        chunks = chunk_doc(self.doc, size=120, overlap=0.15)
        self.assertGreaterEqual(len(chunks), 2)
        for c in chunks:
            self.assertTrue(c["content"].strip())
            self.assertGreaterEqual(c["char_end"], c["char_start"])
            self.assertGreater(c["page_no"], 0)

    def test_hybrid_search_hits_expected_doc(self):
        hits = search.hybrid_search("一线城市住宿标准是多少", limit=5)
        self.assertTrue(hits, "应召回候选切片")
        self.assertEqual(hits[0]["ku_id"], self.ku)
        self.assertGreater(hits[0]["score"], 0)
        self.assertTrue({"vector", "keyword"} & set(hits[0]["channels"]))

    def test_search_unknown_question_low_confidence(self):
        hits = search.hybrid_search("火星殖民计划预算", limit=5)
        top = hits[0] if hits else {"vector_score": 0}
        self.assertLessEqual(top["vector_score"], 0.35)


class ParserFormatTest(unittest.TestCase):
    """使用 seed/docs 中真实文件验证 DOCX / PDF 解析链路。"""

    SEED = Path(__file__).resolve().parent.parent / "seed" / "docs"

    def test_docx_parse(self):
        f = self.SEED / "onboarding.docx"
        if not f.exists():
            self.skipTest("示例 DOCX 不存在")
        doc = parsers.parse_file(f)
        self.assertGreater(doc.text_length, 200)
        self.assertIn("入职", doc.full_text)

    def test_pdf_parse_with_tounicode(self):
        f = self.SEED / "travel-reimburse.pdf"
        if not f.exists():
            self.skipTest("示例 PDF 不存在")
        doc = parsers.parse_file(f)
        self.assertGreater(doc.text_length, 300)
        self.assertIn("住宿标准", doc.full_text)
        self.assertTrue(all(b.page_no >= 1 for b in doc.blocks))

    def test_markdown_heading_path(self):
        f = self.SEED / "employee-handbook.md"
        if not f.exists():
            self.skipTest("示例 Markdown 不存在")
        doc = parsers.parse_file(f)
        self.assertTrue(any(b.heading_path for b in doc.blocks), "Markdown 标题应形成标题路径")
        self.assertTrue(any("考勤" in b.heading_path for b in doc.blocks))


if __name__ == "__main__":
    unittest.main()
