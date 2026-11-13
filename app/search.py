"""混合检索：向量召回 + BM25 关键词召回 + RRF 融合。

返回结构保留每个候选的命中通道与分数，便于前端“检索与鉴权轨迹”展示。
"""
from __future__ import annotations

import math
from collections import defaultdict

from . import config, db, llm
from .embed import EmbeddingError, cosine, embed, unpack
from .util import tokenize

# ---------------------------------------------------------------- 向量索引（进程内）
_vec_index: dict[int, tuple[int, list[float]]] = {}   # chunk_id -> (ku_id, vec)
_index_ready = False
EMBED_ERROR: str | None = None      # 最近一次向量化失败原因（用于前端提示）


def take_embed_error() -> str | None:
    global EMBED_ERROR
    err, EMBED_ERROR = EMBED_ERROR, None
    return err


def invalidate_index() -> None:
    global _index_ready, EMBED_ERROR
    _index_ready = False
    EMBED_ERROR = None
    _vec_index.clear()


def _ensure_index() -> None:
    global _index_ready
    if _index_ready:
        return
    _vec_index.clear()
    for row in db.query("SELECT id, ku_id, vec FROM chunks"):
        vec = unpack(row["vec"])
        if vec:
            _vec_index[row["id"]] = (row["ku_id"], vec)
    _index_ready = True


# ---------------------------------------------------------------- BM25
def _bm25(query: str, top_k: int = config.KEYWORD_TOP_K) -> list[tuple[int, float]]:
    terms = list(dict.fromkeys(tokenize(query)))
    if not terms:
        return []
    n_docs = db.scalar("SELECT COUNT(*) FROM chunks") or 1
    avgdl = db.scalar("SELECT AVG(token_count) FROM chunks", default=40.0) or 40.0
    k1, b = 1.5, 0.75

    scores: dict[int, float] = defaultdict(float)
    q = ",".join("?" * len(terms))
    rows = db.query(
        f"""SELECT t.term, t.chunk_id, t.tf, c.token_count
            FROM chunk_terms t JOIN chunks c ON c.id=t.chunk_id
            WHERE t.term IN ({q})""",
        terms,
    )
    df: dict[str, int] = defaultdict(int)
    for r in rows:
        df[r["term"]] += 1
    for r in rows:
        idf = math.log(1 + (n_docs - df[r["term"]] + 0.5) / (df[r["term"]] + 0.5))
        dl = r["token_count"] or avgdl
        denom = r["tf"] + k1 * (1 - b + b * dl / avgdl)
        scores[r["chunk_id"]] += idf * (r["tf"] * (k1 + 1)) / (denom or 1)
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    return ranked


# ---------------------------------------------------------------- 向量召回
def _vector(query: str, top_k: int = config.VECTOR_TOP_K) -> list[tuple[int, float]]:
    global EMBED_ERROR
    _ensure_index()
    cfg = llm.get_config()
    try:
        qv = embed(query, cfg, strict=True)
    except EmbeddingError as exc:
        # 向量服务不可用：退化为纯关键词检索，并在回答里提示（不让相似度口径混用）
        EMBED_ERROR = str(exc)
        return []
    if not qv or not _vec_index:
        return []
    scored = [(cid, cosine(qv, vec)) for cid, (_ku, vec) in _vec_index.items()]
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


# ---------------------------------------------------------------- 融合
def hybrid_search(query: str, limit: int = config.VECTOR_TOP_K) -> list[dict]:
    """返回候选切片列表，按 RRF 融合分降序。"""
    vec_hits = _vector(query)
    kw_hits = _bm25(query)

    rrf: dict[int, float] = defaultdict(float)
    for rank, (cid, _score) in enumerate(vec_hits, start=1):
        rrf[cid] += 1.0 / (config.RRF_K + rank)
    for rank, (cid, _score) in enumerate(kw_hits, start=1):
        rrf[cid] += 1.0 / (config.RRF_K + rank)

    vec_map = {cid: s for cid, s in vec_hits}
    kw_map = {cid: s for cid, s in kw_hits}
    ordered = sorted(rrf.items(), key=lambda x: -x[1])[:limit]
    if not ordered:
        return []

    ids = [cid for cid, _ in ordered]
    q = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in db.query(
        f"""SELECT c.*, k.title, k.code, k.format, k.category
            FROM chunks c JOIN knowledge_units k ON k.id=c.ku_id
            WHERE c.id IN ({q})""", ids)}

    out = []
    for cid, score in ordered:
        r = rows.get(cid)
        if not r:
            continue
        out.append({
            "chunk_id": r["id"],
            "ku_id": r["ku_id"],
            "title": r["title"],
            "code": r["code"],
            "format": r["format"],
            "category": r["category"],
            "content": r["content"],
            "heading_path": r["heading_path"],
            "page_no": r["page_no"],
            "para_index": r["para_index"],
            "char_start": r["char_start"],
            "char_end": r["char_end"],
            "score": round(score, 6),
            "vector_score": round(vec_map.get(cid, 0.0), 4),
            "keyword_score": round(kw_map.get(cid, 0.0), 4),
            "channels": [c for c, m in (("vector", vec_map), ("keyword", kw_map)) if cid in m],
        })
    return out


def top1_similarity(query: str, hits: list[dict] | None = None) -> float:
    if hits is None:
        hits = hybrid_search(query, limit=5)
    return max((h["vector_score"] for h in hits), default=0.0)
