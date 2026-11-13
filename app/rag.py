"""AI 鉴权问答引擎（RAG 核心链路）。

流程严格对应需求 2.9.4 / 2.9.7：
  登录态 → FAQ 缓存命中 → 混合检索召回 → **服务端四维鉴权裁剪** → Prompt 编排
  → 流式生成 → 引用溯源 → 权限缺失提示 → 审计与缺口落库
"""
from __future__ import annotations

import time
from typing import Iterator

from . import config, db, embed, llm, permissions, search
from .util import estimate_tokens, jdumps, jloads, normalize_query, now_iso, tokenize, truncate


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

DENY_MESSAGE = (
    "检测到相关知识文档，但您当前所属部门/角色无权查阅该内容。"
    "已为你检索到相关资料，其中 {n} 份文档因权限设置无法展示。"
    "如需查阅，请联系对应部门知识管理员申请权限。"
)
NO_HIT_MESSAGE = (
    "未能在知识库中检索到与该问题匹配的内容。\n\n"
    "该问题已自动记入「知识缺口清单」，知识管理员补充文档后可再次提问。"
)


# ---------------------------------------------------------------- 会话
def ensure_session(ctx: dict, session_id: int | None, title: str | None = None) -> int:
    if session_id:
        row = db.query_one("SELECT id FROM sessions WHERE id=? AND user_id=?", (session_id, ctx["id"]))
        if row:
            return row["id"]
    sid = db.execute(
        "INSERT INTO sessions(user_id,title,created_at,updated_at) VALUES(?,?,?,?)",
        (ctx["id"], title or "新会话", now_iso(), now_iso()),
    )
    return sid


def list_sessions(ctx: dict) -> list[dict]:
    rows = db.query(
        """SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS msg_count
           FROM sessions s WHERE s.user_id=? ORDER BY s.updated_at DESC LIMIT 50""",
        (ctx["id"],),
    )
    return [dict(r) for r in rows]


def session_messages(ctx: dict, session_id: int) -> list[dict]:
    own = db.query_one("SELECT id FROM sessions WHERE id=? AND user_id=?", (session_id, ctx["id"]))
    if not own:
        return []
    rows = db.query("SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,))
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "citations": jloads(r["citations"], []),
            "trace": jloads(r["trace"], {}),
            "denied": r["denied"],
            "latency_ms": r["latency_ms"],
            "token_in": r["token_in"],
            "token_out": r["token_out"],
            "created_at": r["created_at"],
        })
    return out


# ---------------------------------------------------------------- FAQ 缓存
def faq_cache_lookup(ctx: dict, question: str) -> tuple[dict | None, list[int]]:
    """返回 (命中 FAQ, 因权限不可展示的 FAQ 关联知识数)。"""
    cfg = config.FAQ_CACHE_ENABLED
    if not cfg:
        return None, []
    rows = db.query("SELECT * FROM faqs WHERE status='online' AND cache_enabled=1")
    if not rows:
        return None, []
    cfg = llm.get_config()
    try:
        qv = embed.embed(question, cfg, strict=True)
    except embed.EmbeddingError:
        return None, []                       # 向量服务异常时跳过缓存，走正常检索
    qterms = set(tokenize(question))
    best, best_sim = None, 0.0
    for r in rows:
        sim = max(embed.cosine(qv, embed.unpack(r["vec"])),
                  jaccard(qterms, set(tokenize(r["question"]))))
        if sim > best_sim:
            best, best_sim = r, sim
    if not best or best_sim < llm.thresholds()["cache"]:
        return None, []
    visible = permissions.visible_ku_ids(ctx)
    related = [int(x) for x in (jloads(best["related_ku"], []) or [])]
    if related and not (set(related) & visible):
        return None, related
    return {"faq": dict(best), "sim": round(best_sim, 4), "related": related}, []


# ---------------------------------------------------------------- 检索 + 鉴权
def retrieve_and_authorize(ctx: dict, question: str) -> dict:
    hits = search.hybrid_search(question, limit=config.VECTOR_TOP_K)
    ordered_ku: list[int] = []
    for h in hits:
        if h["ku_id"] not in ordered_ku:
            ordered_ku.append(h["ku_id"])
    allowed_ku, denied_ku = permissions.split_candidates(ctx, ordered_ku)
    allowed_set = set(allowed_ku)
    allowed_chunks = [h for h in hits if h["ku_id"] in allowed_set][:config.CONTEXT_TOP_N]
    # 置信度 = max(向量相似度, 查询词在命中切片中的覆盖率)，与具体向量引擎口径解耦
    qterms = set(tokenize(question))

    def conf_of(items: list[dict]) -> float:
        cov, vec = 0.0, 0.0
        for h in items[:5]:
            cterms = set(tokenize(h["content"]))
            cov = max(cov, len(qterms & cterms) / (len(qterms) or 1))
            vec = max(vec, h["vector_score"])
        return round(max(cov, vec), 4)

    allowed_hits = [h for h in hits if h["ku_id"] in allowed_set]
    denied_hits = [h for h in hits if h["ku_id"] not in allowed_set]
    top1 = conf_of(hits)
    return {
        "hits": hits,
        "allowed_ku": allowed_ku,
        "denied_ku": denied_ku,
        "chunks": allowed_chunks,
        "top1_sim": top1,
        "conf_allowed": conf_of(allowed_hits),
        "conf_denied": conf_of(denied_hits),
        "retrieved": len(hits),
    }


def citations_of(chunks: list[dict]) -> list[dict]:
    out = []
    for i, ch in enumerate(chunks, start=1):
        body = ch["content"]
        out.append({
            "index": i,
            "ku_id": ch["ku_id"],
            "title": ch["title"],
            "code": ch["code"],
            "page_no": ch["page_no"],
            "para_index": (ch["para_index"] or 0) + 1,
            "heading_path": ch["heading_path"],
            "char_start": ch["char_start"],
            "char_end": ch["char_end"],
            "snippet": truncate(body, 220),
            "score": ch["score"],
            "vector_score": ch["vector_score"],
        })
    return out


def _trace_payload(retr: dict, cache_hit: bool = False) -> dict:
    cands = []
    for h in retr["hits"][:12]:
        cands.append({
            "chunk_id": h["chunk_id"],
            "ku_id": h["ku_id"],
            "title": h["title"] if h["ku_id"] in retr["allowed_ku"] else "受限知识单元（标题已隐藏）",
            "page_no": h["page_no"],
            "score": h["score"],
            "channels": h["channels"],
            "allowed": h["ku_id"] in retr["allowed_ku"],
        })
    return {
        "retrieved": retr["retrieved"],
        "allowed_ku": retr["allowed_ku"],
        "denied_ku": retr["denied_ku"],
        "allowed_count": len(retr["allowed_ku"]),
        "denied_count": len(retr["denied_ku"]),
        "top1_sim": retr["top1_sim"],
        "coverage": retr.get("coverage", 0),
        "candidates": cands,
        "cache_hit": cache_hit,
    }


# ---------------------------------------------------------------- 缺口 & 日志
def record_gap(question: str, ctx: dict, top1_sim: float) -> None:
    norm = normalize_query(question)
    if not norm:
        return
    row = db.query_one("SELECT * FROM gaps WHERE question_norm=?", (norm,))
    if row:
        db.execute("UPDATE gaps SET freq=freq+1, last_seen=?, top1_sim=MAX(top1_sim,?) WHERE id=?",
                   (now_iso(), top1_sim, row["id"]))
        return
    db.execute(
        """INSERT INTO gaps(question,question_norm,dept_id,freq,top1_sim,suggested_category,status,created_at,last_seen)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (question, norm, ctx.get("dept_id"), 1, top1_sim, "待定", "open", now_iso(), now_iso()),
    )


def log_query(ctx: dict, session_id: int, question: str, retr: dict, usage: dict,
              first_ms: int, total_ms: int, cache_hit: bool = False, faq_id: int | None = None,
              answered: bool = True) -> None:
    db.execute(
        """INSERT INTO query_logs(session_id,user_id,dept_id,query,query_norm,top1_sim,hit_count,
            allowed_ids,denied_ids,cache_hit,faq_id,token_in,token_out,first_token_ms,total_ms,answered,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session_id, ctx["id"], ctx.get("dept_id"), truncate(question, 300), normalize_query(question),
         retr["top1_sim"], len(retr["chunks"]), jdumps([c["ku_id"] for c in retr["chunks"]]),
         jdumps(retr["denied_ku"]), 1 if cache_hit else 0, faq_id,
         usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
         first_ms, total_ms, 1 if answered else 0, now_iso()),
    )


def _save_message(session_id: int, role: str, content: str, citations: list | None = None,
                  trace: dict | None = None, denied: int = 0, token_in: int = 0,
                  token_out: int = 0, latency_ms: int = 0) -> int:
    mid = db.execute(
        """INSERT INTO messages(session_id,role,content,citations,trace,denied,token_in,token_out,latency_ms,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (session_id, role, content, jdumps(citations or []), jdumps(trace or {}),
         denied, token_in, token_out, latency_ms, now_iso()),
    )
    db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), session_id))
    return mid


# ---------------------------------------------------------------- 主流程
def answer_stream(ctx: dict, session_id: int | None, question: str) -> Iterator[dict]:
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "message": "请输入问题内容"}
        return

    t0 = time.time()
    history = []
    if session_id:
        for m in session_messages(ctx, session_id)[-6:]:
            history.append({"role": m["role"], "content": m["content"]})
    sid = ensure_session(ctx, session_id, title=truncate(question, 24))
    _save_message(sid, "user", question)
    yield {"type": "ack", "session_id": sid, "question": question}

    # 1) FAQ 高速缓存
    faq_hit, faq_denied = faq_cache_lookup(ctx, question)
    if faq_hit:
        faq = faq_hit["faq"]
        db.execute("UPDATE faqs SET hit_count=hit_count+1 WHERE id=?", (faq["id"],))
        fake_retr = {"top1_sim": faq_hit["sim"], "chunks": [], "denied_ku": [], "hits": [],
                     "allowed_ku": faq_hit["related"], "retrieved": 0}
        trace = _trace_payload(fake_retr, cache_hit=True)
        trace["faq"] = {"id": faq["id"], "question": faq["question"], "similarity": faq_hit["sim"]}
        yield {"type": "trace", "trace": trace, "cache_hit": True, "faq": trace["faq"]}

        answer = faq["answer"]
        for i in range(0, len(answer), 16):
            yield {"type": "delta", "text": answer[i:i + 16]}
            time.sleep(0.004)
        yield {"type": "citations", "items": []}
        first_ms = int((time.time() - t0) * 1000)
        total_ms = max(first_ms, int((time.time() - t0) * 1000))
        usage = {"prompt_tokens": 0, "completion_tokens": estimate_tokens(answer)}
        _save_message(sid, "assistant", answer, [], trace, 0, 0, usage["completion_tokens"], total_ms)
        log_query(ctx, sid, question, {**fake_retr, "top1_sim": faq_hit["sim"]}, usage,
                  first_ms, total_ms, cache_hit=True, faq_id=faq["id"])
        yield {"type": "done", "message_id": 0, "session_id": sid, "usage": usage,
               "latency_ms": total_ms, "cache_hit": True}
        return

    # 2) 混合检索 + 鉴权裁剪
    retr = retrieve_and_authorize(ctx, question)
    trace = _trace_payload(retr)
    if faq_denied:
        trace["faq_denied"] = len(faq_denied)
    embed_err = search.take_embed_error()
    if embed_err:
        trace["embedding_error"] = embed_err
        yield {"type": "notice", "message": f"向量服务暂不可用（{embed_err}），本次退化为关键词检索"}
    yield {"type": "trace", "trace": trace, "cache_hit": False}

    gap_thr = llm.thresholds()["gap"]
    citations = citations_of(retr["chunks"])
    denied_count = len(retr["denied_ku"])

    # 3) 无权优先：最匹配的知识单元属于受限范围时，不基于弱相关切片硬答
    denial_dominant = (
        denied_count > 0
        and retr["conf_denied"] >= max(retr["conf_allowed"], gap_thr)
    )
    if denial_dominant:
        yield {"type": "denied", "count": denied_count, "message": DENY_MESSAGE.format(n=denied_count)}
        answer = DENY_MESSAGE.format(n=denied_count)
        for i in range(0, len(answer), 14):
            yield {"type": "delta", "text": answer[i:i + 14]}
            time.sleep(0.004)
        yield {"type": "citations", "items": []}
        total_ms = int((time.time() - t0) * 1000)
        _save_message(sid, "assistant", answer, [], trace, denied_count, 0, 0, total_ms)
        log_query(ctx, sid, question, retr, {"prompt_tokens": 0, "completion_tokens": 0},
                  total_ms, total_ms, answered=False)
        db.audit(ctx.get("id"), ctx.get("username"), "ai.denied", f"session:{sid}",
                 f"query={truncate(question, 80)} denied_ku={retr['denied_ku']}")
        yield {"type": "done", "session_id": sid, "usage": {}, "latency_ms": total_ms,
               "cache_hit": False, "denied": denied_count}
        return

    # 4) 未命中 / 置信度不足 → 缺口池
    if not retr["chunks"] or retr["conf_allowed"] < gap_thr:
        answer = NO_HIT_MESSAGE
        record_gap(question, ctx, retr["conf_allowed"])
        for i in range(0, len(answer), 14):
            yield {"type": "delta", "text": answer[i:i + 14]}
            time.sleep(0.004)
        yield {"type": "citations", "items": []}
        total_ms = int((time.time() - t0) * 1000)
        _save_message(sid, "assistant", answer, [], trace, denied_count, 0, 0, total_ms)
        log_query(ctx, sid, question, retr, {"prompt_tokens": 0, "completion_tokens": 0},
                  total_ms, total_ms, answered=False)
        yield {"type": "done", "session_id": sid, "usage": {}, "latency_ms": total_ms,
               "cache_hit": False, "gap": not denied_count}
        return

    # 5) 生成答案
    cfg = llm.get_config()
    messages = llm.build_messages(question, retr["chunks"], history)
    first_ms = 0
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    text_parts: list[str] = []
    used_remote = False
    used_idx: list[int] | None = None
    if llm.is_remote(cfg):
        try:
            for ev in llm.stream_openai(messages, cfg):
                if ev["type"] == "delta":
                    if not first_ms:
                        first_ms = int((time.time() - t0) * 1000)
                    text_parts.append(ev["text"])
                    yield {"type": "delta", "text": ev["text"]}
                elif ev["type"] == "usage":
                    usage = ev["usage"]
                    used_remote = True
        except Exception as exc:  # noqa: BLE001
            yield {"type": "notice", "message": f"远程模型不可用（{exc}），已降级为本地检索增强引擎"}
    if not text_parts:
        local_text, used_idx = llm.local_answer(question, retr["chunks"])
        for ev in llm.local_stream(local_text):
            if ev["type"] == "delta":
                if not first_ms:
                    first_ms = int((time.time() - t0) * 1000)
                text_parts.append(ev["text"])
                yield {"type": "delta", "text": ev["text"]}
            elif ev["type"] == "usage":
                usage = ev["usage"]

    answer = "".join(text_parts)
    if used_idx:
        citations = [c for c in citations if c["index"] in used_idx]
    if denied_count:
        yield {"type": "denied", "count": denied_count, "message": DENY_MESSAGE.format(n=denied_count)}
    yield {"type": "citations", "items": citations}

    total_ms = int((time.time() - t0) * 1000)
    if not usage.get("completion_tokens"):
        usage = {"prompt_tokens": estimate_tokens(" ".join(m["content"] for m in messages)),
                 "completion_tokens": estimate_tokens(answer)}
    mid = _save_message(sid, "assistant", answer, citations, trace, denied_count,
                        usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), total_ms)
    log_query(ctx, sid, question, retr, usage, first_ms or total_ms // 2, total_ms)
    if denied_count:
        db.audit(ctx.get("id"), ctx.get("username"), "ai.denied", f"session:{sid}",
                 f"query={truncate(question, 80)} denied_ku={retr['denied_ku']}")
    elif retr["top1_sim"] < gap_thr:
        record_gap(question, ctx, retr["top1_sim"])
    yield {"type": "done", "message_id": mid, "session_id": sid, "usage": usage,
           "latency_ms": total_ms, "cache_hit": False, "engine": "remote" if used_remote else "local"}


def answer_sync(ctx: dict, session_id: int | None, question: str) -> dict:
    """非流式封装，供接口测试与脚本调用。"""
    text = ""
    result = {"answer": "", "citations": [], "trace": {}, "latency_ms": 0, "session_id": session_id,
              "cache_hit": False, "denied": 0}
    for ev in answer_stream(ctx, session_id, question):
        if ev["type"] == "delta":
            text += ev["text"]
        elif ev["type"] == "trace":
            result["trace"] = ev.get("trace", {})
            result["session_id"] = ev["trace"] and result["session_id"]
        elif ev["type"] == "citations":
            result["citations"] = ev["items"]
        elif ev["type"] == "denied":
            result["denied"] = ev["count"]
        elif ev["type"] == "ack":
            result["session_id"] = ev["session_id"]
        elif ev["type"] == "done":
            result["latency_ms"] = ev.get("latency_ms", 0)
            result["cache_hit"] = ev.get("cache_hit", False)
    result["answer"] = text
    return result
