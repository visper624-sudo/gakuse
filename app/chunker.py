"""文本切片：按标题语义 + 目标长度（约 480 token）+ 15% 重叠。

切片保留 heading_path / page_no / para_index / char_start / char_end，
为“引用溯源卡片”和原文高亮定位提供依据。
"""
from __future__ import annotations

from . import config
from .parsers import Block, ParsedDoc
from .util import estimate_tokens, split_sentences

MAX_BLOCK_CHARS = 1200


def _split_long_text(text: str, budget: int) -> list[str]:
    """超长段落按句子边界切开，避免单块过大稀释检索精度。"""
    if len(text) <= budget:
        return [text]
    parts: list[str] = []
    cur = ""
    for sent in split_sentences(text):
        if len(cur) + len(sent) > budget and cur:
            parts.append(cur)
            cur = sent
        else:
            cur += sent
    if cur.strip():
        parts.append(cur)
    return parts


def chunk_doc(doc: ParsedDoc, size: int = config.CHUNK_SIZE,
              overlap: float = config.CHUNK_OVERLAP) -> list[dict]:
    budget_chars = int(size * 1.0)          # 中文 1 字 ≈ 1 token
    overlap_chars = max(24, int(budget_chars * overlap))

    # 1) 段落 → 可切分片段（带元数据）
    pieces: list[dict] = []
    for b in doc.blocks:
        text = b.text.strip()
        if not text:
            continue
        if len(text) > MAX_BLOCK_CHARS:
            offset = 0
            for seg in _split_long_text(text, budget_chars):
                pieces.append({
                    "text": seg,
                    "heading_path": b.heading_path,
                    "page_no": b.page_no,
                    "para_index": b.para_index,
                    "char_start": b.char_start + offset,
                    "char_end": b.char_start + offset + len(seg),
                    "level": b.level,
                })
                offset += len(seg)
        else:
            pieces.append({
                "text": text,
                "heading_path": b.heading_path,
                "page_no": b.page_no,
                "para_index": b.para_index,
                "char_start": b.char_start,
                "char_end": b.char_end,
                "level": b.level,
            })

    # 2) 打包为切片，标题段落作为新切片起点
    chunks: list[dict] = []
    cur: list[dict] = []
    cur_len = 0

    def flush():
        nonlocal cur, cur_len
        if not cur:
            return
        body = "\n".join(p["text"] for p in cur).strip()
        if not body:
            cur, cur_len = [], 0
            return
        head = cur[0]["heading_path"] or ""
        prefix = f"《{head}》\n" if head else ""
        chunks.append({
            "content": prefix + body,
            "heading_path": head,
            "page_no": cur[0]["page_no"],
            "para_index": cur[0]["para_index"],
            "char_start": cur[0]["char_start"],
            "char_end": cur[-1]["char_end"],
            "token_count": estimate_tokens(prefix + body),
        })
        # 构造重叠：保留尾部片段（不超过 overlap_chars）
        tail: list[dict] = []
        tail_len = 0
        for p in reversed(cur):
            if tail_len + len(p["text"]) > overlap_chars and tail:
                break
            tail.insert(0, p)
            tail_len += len(p["text"])
        cur, cur_len = tail, tail_len

    for p in pieces:
        plen = len(p["text"])
        is_heading = p["level"] > 0
        if is_heading and cur and cur_len > budget_chars * 0.5:
            flush()
        if cur_len + plen > budget_chars and cur:
            flush()
        cur.append(p)
        cur_len += plen
        if cur_len >= budget_chars:
            flush()

    if cur:
        body = "\n".join(p["text"] for p in cur).strip()
        if body:
            head = cur[0]["heading_path"] or ""
            prefix = f"《{head}》\n" if head else ""
            chunks.append({
                "content": prefix + body,
                "heading_path": head,
                "page_no": cur[0]["page_no"],
                "para_index": cur[0]["para_index"],
                "char_start": cur[0]["char_start"],
                "char_end": cur[-1]["char_end"],
                "token_count": estimate_tokens(prefix + body),
            })

    # 3) 去重（重叠尾块可能重复产出相同正文）
    seen = set()
    uniq = []
    for c in chunks:
        key = c["content"][:120]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq
