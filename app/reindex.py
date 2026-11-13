"""向量索引重建（切换向量引擎后必须执行）。

同时被 CLI（scripts/reindex_embeddings.py）与后台接口（POST /api/system/reindex）复用。
"""
from __future__ import annotations

import time
from typing import Callable

from . import db, embed, llm, search


class ReindexError(RuntimeError):
    pass


def run(batch: int | None = None, dry_run: bool = False,
        progress: Callable[[int, int], None] | None = None) -> dict:
    """重建全部切片与 FAQ 向量；失败时抛 ReindexError（不会写入一半脏数据）。"""
    cfg = llm.get_config()
    info = embed.describe(cfg)

    probe = embed.embed("差旅住宿标准与报销流程", cfg, strict=True)
    dim = len(probe)

    chunks = db.query("SELECT id, content FROM chunks ORDER BY id")
    faqs = db.query("SELECT id, question FROM faqs")
    result = {"mode": info["mode"], "model": info["model"], "dim": dim,
              "chunks": len(chunks), "faqs": len(faqs), "dry_run": dry_run}

    if dry_run:
        return result

    batch_size = batch or max(1, int(cfg.get("embedding_batch") or 16))
    t0 = time.time()
    done = 0
    for i in range(0, len(chunks), batch_size):
        group = chunks[i:i + batch_size]
        try:
            vecs = embed.embed_texts([c["content"] for c in group], cfg, strict=True)
        except Exception as exc:  # noqa: BLE001
            raise ReindexError(f"第 {i + 1}-{i + len(group)} 个切片向量化失败：{exc}") from exc
        for row, vec in zip(group, vecs):
            db.execute("UPDATE chunks SET vec=?, norm=? WHERE id=?",
                       (embed.pack(vec), round(sum(v * v for v in vec), 6), row["id"]))
        done += len(group)
        if progress:
            progress(done, len(chunks))

    for f in faqs:
        vec = embed.embed(f["question"], cfg, strict=True)
        db.execute("UPDATE faqs SET vec=? WHERE id=?", (embed.pack(vec), f["id"]))

    search.invalidate_index()
    signature = embed.signature(cfg)
    db.kv_set("embedding_signature", signature)
    result.update({"elapsed_ms": int((time.time() - t0) * 1000), "signature": signature})
    return result
