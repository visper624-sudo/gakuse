"""知识沉淀服务：高频问题聚类 → 候选 FAQ → 人工审核发布 → 缓存加速；以及知识缺口闭环。

聚类相似度采用「语义向量余弦 + 词形 Jaccard」融合分：
  * 本地哈希向量：阈值 0.60（config.active_thresholds("local")）；
  * BGE-M3 / 真实 Embedding：阈值自动切换为 0.88（config.active_thresholds("semantic")）。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

from . import config, db, embed, llm, search
from .util import estimate_tokens, jdumps, jloads, normalize_query, now_iso, tokenize, truncate


# ---------------------------------------------------------------- 相似度
def _terms(text: str) -> set[str]:
    return set(tokenize(text))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def combined_sim(va: list[float], ta: set[str], vb: list[float], tb: set[str]) -> float:
    return round(max(embed.cosine(va, vb), jaccard(ta, tb)), 4)


# ---------------------------------------------------------------- 聚类挖掘
def mine(days: int = 30, threshold: float | None = None, min_freq: int | None = None) -> dict:
    threshold = llm.thresholds()["cluster"] if threshold is None else threshold
    min_freq = config.FAQ_CLUSTER_MIN_FREQ if min_freq is None else min_freq
    embed_cfg = llm.get_config()
    start = db.query_one("SELECT date('now', ?) AS d", (f"-{days} day",))["d"] + " 00:00:00"
    rows = db.query("SELECT * FROM query_logs WHERE created_at >= ? AND answered=1", (start,))

    agg: dict[str, dict] = {}
    for r in rows:
        norm = r["query_norm"] or normalize_query(r["query"])
        if not norm:
            continue
        node = agg.setdefault(norm, {"norm": norm, "count": 0, "sample": r["query"], "logs": []})
        node["count"] += 1
        node["logs"].append(r["id"])

    items = list(agg.values())
    if not items:
        return {"clusters": 0, "created": 0, "candidates": []}

    for it in items:
        it["vec"] = embed.embed(it["sample"], embed_cfg, strict=True)
        it["terms"] = _terms(it["sample"])
        it["assigned"] = -1

    # 连通分量聚类（单链 + 并查集）：结果与遍历顺序无关，同一问题簇稳定聚成一组
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if combined_sim(items[i]["vec"], items[i]["terms"], items[j]["vec"], items[j]["terms"]) >= threshold:
                union(i, j)

    groups: dict[int, list[dict]] = {}
    for idx, it in enumerate(items):
        groups.setdefault(find(idx), []).append(it)
    clusters = list(groups.values())

    created = []
    for cl in clusters:
        freq = sum(x["count"] for x in cl)
        if freq < min_freq:
            continue
        rep = max(cl, key=lambda x: x["count"])
        key = hashlib.md5(rep["norm"].encode()).hexdigest()[:16]

        existing = db.query_one("SELECT * FROM faq_candidates WHERE cluster_key=?", (key,))
        if existing and existing["status"] == "published":
            db.execute("UPDATE faq_candidates SET freq=?, updated_at=? WHERE id=?",
                       (freq, now_iso(), existing["id"]))
            continue
        if existing and existing["status"] == "rejected":
            db.execute("UPDATE faq_candidates SET freq=?, updated_at=? WHERE id=?",
                       (freq, now_iso(), existing["id"]))
            continue

        hits = search.hybrid_search(rep["sample"], limit=8)
        related = []
        for h in hits:
            if h["ku_id"] not in related:
                related.append(h["ku_id"])
        draft = llm.generate_faq(rep["sample"], hits[:3])
        confidence = round(
            sum(combined_sim(rep["vec"], rep["terms"], x["vec"], x["terms"]) for x in cl) / len(cl), 4
        ) if len(cl) > 1 else 1.0

        if existing:
            db.execute(
                """UPDATE faq_candidates SET question=?, freq=?, related_ku=?, answer=?, confidence=?,
                   sample_query=?, updated_at=? WHERE id=?""",
                (draft["question"], freq, jdumps(related), draft["answer"], confidence,
                 rep["sample"], now_iso(), existing["id"]),
            )
            cid = existing["id"]
        else:
            cid = db.execute(
                """INSERT INTO faq_candidates(cluster_key,question,freq,related_ku,answer,confidence,status,
                    sample_query,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (key, draft["question"], freq, jdumps(related), draft["answer"], confidence,
                 "pending", rep["sample"], now_iso(), now_iso()),
            )
        created.append({"id": cid, "question": draft["question"], "freq": freq,
                        "confidence": confidence, "related": related})

    db.audit(None, "system", "faq.mine", "", f"clusters={len(clusters)} new={len(created)}")
    return {"clusters": len(clusters), "created": len(created), "candidates": created}


# ---------------------------------------------------------------- 候选 FAQ
def list_candidates(status: str | None = None) -> list[dict]:
    where, args = "", []
    if status:
        where, args = "WHERE status=?", [status]
    rows = db.query(f"SELECT * FROM faq_candidates {where} ORDER BY status='pending' DESC, freq DESC", args)
    out = []
    for r in rows:
        d = dict(r)
        d["related_ku"] = jloads(d["related_ku"], [])
        out.append(d)
    return out


def update_candidate(cid: int, question: str | None = None, answer: str | None = None) -> dict:
    cand = db.query_one("SELECT * FROM faq_candidates WHERE id=?", (cid,))
    if not cand:
        raise ValueError("候选 FAQ 不存在")
    db.execute(
        "UPDATE faq_candidates SET question=COALESCE(?,question), answer=COALESCE(?,answer), updated_at=? WHERE id=?",
        (question, answer, now_iso(), cid),
    )
    return dict(db.query_one("SELECT * FROM faq_candidates WHERE id=?", (cid,)))


def publish_candidate(cid: int, ctx: dict) -> dict:
    cand = db.query_one("SELECT * FROM faq_candidates WHERE id=?", (cid,))
    if not cand:
        raise ValueError("候选 FAQ 不存在")
    q = (cand["question"] or "").strip()
    a = (cand["answer"] or "").strip()
    if not q or not a:
        raise ValueError("问题与答案不能为空")
    vec = embed.embed(q, llm.get_config(), strict=True)
    fid = db.execute(
        """INSERT INTO faqs(question,question_norm,answer,related_ku,status,cache_enabled,hit_count,vec,
            published_by,published_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (q, normalize_query(q), a, cand["related_ku"], "online", 1, 0, embed.pack(vec),
         ctx.get("id"), now_iso(), now_iso()),
    )
    db.execute("UPDATE faq_candidates SET status='published', updated_at=? WHERE id=?", (now_iso(), cid))
    db.audit(ctx.get("id"), ctx.get("username"), "faq.publish", f"faq:{fid}",
             f"{truncate(q, 60)}（来源簇频次 {cand['freq']}）")
    return {"faq_id": fid, "cache_enabled": True}


def reject_candidate(cid: int, ctx: dict, reason: str = "") -> None:
    db.execute("UPDATE faq_candidates SET status='rejected', updated_at=? WHERE id=?", (now_iso(), cid))
    db.audit(ctx.get("id"), ctx.get("username"), "faq.reject", f"candidate:{cid}", reason)


# ---------------------------------------------------------------- 已发布 FAQ
def list_faqs(keyword: str = "") -> list[dict]:
    where, args = "", []
    if keyword:
        where, args = "WHERE question LIKE ? OR answer LIKE ?", [f"%{keyword}%", f"%{keyword}%"]
    rows = db.query(f"SELECT * FROM faqs {where} ORDER BY id DESC", args)
    out = []
    for r in rows:
        d = dict(r)
        d.pop("vec", None)
        d["related_ku"] = jloads(d["related_ku"], [])
        out.append(d)
    return out


def update_faq(fid: int, patch: dict, ctx: dict) -> dict:
    faq = db.query_one("SELECT * FROM faqs WHERE id=?", (fid,))
    if not faq:
        raise ValueError("FAQ 不存在")
    question = patch.get("question", faq["question"])
    answer = patch.get("answer", faq["answer"])
    cache_enabled = int(patch.get("cache_enabled", faq["cache_enabled"]))
    vec = embed.embed(question, llm.get_config()) if question != faq["question"] else faq["vec"]
    db.execute(
        "UPDATE faqs SET question=?,question_norm=?,answer=?,cache_enabled=?,vec=?,updated_at=? WHERE id=?",
        (question, normalize_query(question), answer, cache_enabled, vec, now_iso(), fid),
    )
    db.audit(ctx.get("id"), ctx.get("username"), "faq.update", f"faq:{fid}", truncate(question, 60))
    return {"ok": True}


def delete_faq(fid: int, ctx: dict) -> None:
    db.execute("DELETE FROM faqs WHERE id=?", (fid,))
    db.audit(ctx.get("id"), ctx.get("username"), "faq.delete", f"faq:{fid}", "")


# ---------------------------------------------------------------- 知识缺口
def list_gaps(status: str | None = "open") -> list[dict]:
    where, args = "", []
    if status and status != "all":
        where, args = "WHERE g.status=?", [status]
    rows = db.query(
        f"""SELECT g.*, d.name AS dept_name FROM gaps g
            LEFT JOIN depts d ON d.id=g.dept_id {where} ORDER BY g.freq DESC, g.last_seen DESC""",
        args,
    )
    return [dict(r) for r in rows]


def gap_to_task(gid: int, ctx: dict, owner: str = "") -> dict:
    gap = db.query_one("SELECT * FROM gaps WHERE id=?", (gid,))
    if not gap:
        raise ValueError("缺口不存在")
    tid = db.execute(
        """INSERT INTO tasks(gap_id,title,category,owner,priority,status,created_by,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (gid, truncate(gap["question"], 60), gap["suggested_category"] or "待定", owner or "知识管理员",
         "high" if gap["freq"] >= 10 else "medium", "todo", ctx.get("id"), now_iso(), now_iso()),
    )
    db.execute("UPDATE gaps SET status='task' WHERE id=?", (gid,))
    db.audit(ctx.get("id"), ctx.get("username"), "gap.task", f"gap:{gid}", gap["question"][:60])
    return {"task_id": tid}


def update_gap(gid: int, status: str, ctx: dict) -> None:
    db.execute("UPDATE gaps SET status=? WHERE id=?", (status, gid))
    db.audit(ctx.get("id"), ctx.get("username"), "gap.update", f"gap:{gid}", status)


def list_tasks() -> list[dict]:
    rows = db.query("SELECT * FROM tasks ORDER BY CASE status WHEN 'todo' THEN 0 WHEN 'doing' THEN 1 ELSE 2 END, id DESC")
    return [dict(r) for r in rows]


def update_task(tid: int, status: str, ctx: dict) -> None:
    db.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now_iso(), tid))
    db.audit(ctx.get("id"), ctx.get("username"), "task.update", f"task:{tid}", status)
