"""生成示例知识库文档（开发期一次性脚本）。

用法（需 python-docx）：
    python tools/build_seed_docs.py
输出：
    seed/docs/*.md|*.txt|*.docx
    build/pdf_src/*.docx（供 Word 转 PDF）
    seed/docs/manifest.json（导入清单：标题、分类、权限）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.seed_content import DOCS  # noqa: E402

SEED_DIR = ROOT / "seed" / "docs"
PDF_SRC = ROOT / "build" / "pdf_src"
HEAD = re.compile(r"^(第[一二三四五六七八九十百]+[章节]|[一二三四五六七八九十]+、|\d+\.\s*[^\d])")


def write_docx(path: Path, title: str, text: str) -> None:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(12)

    doc.add_heading(title, level=0)
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line == title:
            continue
        if HEAD.match(line) and len(line) <= 40:
            doc.add_heading(line, level=1)
        else:
            doc.add_paragraph(line)
    doc.save(str(path))


def main() -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    PDF_SRC.mkdir(parents=True, exist_ok=True)
    manifest = []
    for d in DOCS:
        slug, fmt = d["slug"], d["fmt"]
        text = d["text"]
        if fmt == "md":
            path = SEED_DIR / f"{slug}.md"
            path.write_text(text, encoding="utf-8")
        elif fmt == "txt":
            path = SEED_DIR / f"{slug}.txt"
            path.write_text(text, encoding="utf-8")
        elif fmt == "docx":
            path = SEED_DIR / f"{slug}.docx"
            write_docx(path, d["title"], text)
        elif fmt == "pdf":
            src = PDF_SRC / f"{slug}.docx"
            write_docx(src, d["title"], text)
            path = SEED_DIR / f"{slug}.pdf"     # 由 Word 转换生成
        else:
            raise SystemExit(f"未知格式 {fmt}")
        manifest.append({
            "slug": slug,
            "file": path.name,
            "title": d["title"],
            "category": d["category"],
            "format": fmt,
            "perms": d["perms"],
        })
        print(f"  [{fmt:4}] {path.name}")
    (SEED_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"共生成 {len(manifest)} 篇，其中待转 PDF {sum(1 for d in DOCS if d['fmt'] == 'pdf')} 篇")


if __name__ == "__main__":
    main()
