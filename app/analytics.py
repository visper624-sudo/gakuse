"""运营看板聚合：PV/UV、知识规模、高频问题榜、知识热度榜、Token 与响应时延。

指标口径（答辩口径）：
  PV = 问答请求次数；UV = 去重用户数；
  Token = 输入 + 输出 token 之和；时延 = 首 token 时延与整段时延分别统计 P50/P95；
  FAQ 缓存命中率 = 缓存命中次数 / 总提问次数；知识覆盖率 = 有召回的提问数 / 总提问数。
"""
from __future__ import annotations

from collections import Counter, defaultdict

from . import config, db, llm
from .util import day_list, jloads, percentile, truncate


def _range_days(days: int) -> list[str]:
    return day_list(days)


def overview(days: int = 7, user_id: int | None = None) -> dict:
    dlist = _range_days(days)
    start = dlist[0] + " 00:00:00"
    where = "WHERE created_at >= ?"
    args: list = [start]
    if user_id:
        where += " AND user_id=?"
        args.append(user_id)

    logs = [dict(r) for r in db.query(f"SELECT * FROM query_logs {where} ORDER BY id", args)]
    pv = len(logs)
    uv = len({r["user_id"] for r in logs if r["user_id"]})
    cache_hits = sum(1 for r in logs if r["cache_hit"])
    with_hits = sum(1 for r in logs if (r["hit_count"] or 0) > 0)
    token_in = sum(r["token_in"] or 0 for r in logs)
    token_out = sum(r["token_out"] or 0 for r in logs)
    latencies = [r["total_ms"] for r in logs if r["total_ms"]]
    firsts = [r["first_token_ms"] for r in logs if r["first_token_ms"]]
    denied_total = sum(len(jloads(r["denied_ids"], [])) for r in logs)

    by_day = defaultdict(lambda: {"pv": 0, "uv": set(), "tokens": 0, "latency": [], "cache": 0})
    for r in logs:
        d = (r["created_at"] or "")[:10]
        node = by_day[d]
        node["pv"] += 1
        if r["user_id"]:
            node["uv"].add(r["user_id"])
        node["tokens"] += (r["token_in"] or 0) + (r["token_out"] or 0)
        if r["total_ms"]:
            node["latency"].append(r["total_ms"])
        node["cache"] += 1 if r["cache_hit"] else 0

    trend = []
    for d in dlist:
        node = by_day.get(d, {"pv": 0, "uv": set(), "tokens": 0, "latency": [], "cache": 0})
        trend.append({
            "date": d,
            "pv": node["pv"],
            "uv": len(node["uv"]),
            "tokens": node["tokens"],
            "avg_latency": round(sum(node["latency"]) / len(node["latency"]), 1) if node["latency"] else 0,
            "cache_hit": node["cache"],
        })

    q_counter = Counter()
    q_sample: dict[str, str] = {}
    for r in logs:
        key = r["query_norm"] or "空"
        q_counter[key] += 1
        q_sample.setdefault(key, r["query"] or "")
    top_questions = [{"question": truncate(q_sample[k], 40), "count": c} for k, c in q_counter.most_common(8)]

    ku_counter: Counter[int] = Counter()
    for r in logs:
        for kid in jloads(r["allowed_ids"], []) or []:
            ku_counter[int(kid)] += 1
    ku_rows = {}
    if ku_counter:
        ids = list(ku_counter)
        q = ",".join("?" * len(ids))
        for row in db.query(f"SELECT id, title, category, format FROM knowledge_units WHERE id IN ({q})", ids):
            ku_rows[row["id"]] = dict(row)
    top_kb = []
    for kid, cnt in ku_counter.most_common(8):
        info = ku_rows.get(kid)
        if info:
            top_kb.append({**info, "count": cnt})

    ku_total = db.scalar("SELECT COUNT(*) FROM knowledge_units")
    faq_online = db.scalar("SELECT COUNT(*) FROM faqs WHERE status='online'")
    gap_open = db.scalar("SELECT COUNT(*) FROM gaps WHERE status='open'")
    pending_candidates = db.scalar("SELECT COUNT(*) FROM faq_candidates WHERE status='pending'")

    return {
        "kpi": {
            "pv": pv,
            "uv": uv,
            "ku_total": ku_total,
            "chunk_total": db.scalar("SELECT COUNT(*) FROM chunks"),
            "faq_online": faq_online,
            "gap_open": gap_open,
            "pending_candidates": pending_candidates,
            "cache_hit_rate": round(cache_hits * 100.0 / pv, 1) if pv else 0.0,
            "coverage": round(with_hits * 100.0 / pv, 1) if pv else 0.0,
            "token_in": token_in,
            "token_out": token_out,
            "token_total": token_in + token_out,
            "latency_avg": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "latency_p50": round(percentile(latencies, 0.5), 1),
            "latency_p95": round(percentile(latencies, 0.95), 1),
            "first_token_avg": round(sum(firsts) / len(firsts), 1) if firsts else 0.0,
            "denied_total": denied_total,
            "answer_rate": round((pv - sum(1 for r in logs if not r["answered"])) * 100.0 / pv, 1) if pv else 0.0,
        },
        "trend": trend,
        "top_questions": top_questions,
        "top_kb": top_kb,
        "format_dist": [dict(r) for r in db.query(
            "SELECT format, COUNT(*) AS cnt FROM knowledge_units GROUP BY format ORDER BY cnt DESC")],
        "category_dist": [dict(r) for r in db.query(
            "SELECT category, COUNT(*) AS cnt FROM knowledge_units GROUP BY category ORDER BY cnt DESC LIMIT 8")],
        "engine": "remote" if db.kv_get("llm_config") and '"provider": "openai"' in (db.kv_get("llm_config") or "") else "local",
        "thresholds": {"gap": llm.thresholds()["gap"], "faq_cache": llm.thresholds()["cache"]},
    }


def audit_list(page: int = 1, size: int = 20, action: str | None = None) -> dict:
    where, args = "", []
    if action:
        where = "WHERE action LIKE ?"
        args.append(f"{action}%")
    total = db.scalar(f"SELECT COUNT(*) FROM audit_logs {where}", args)
    rows = db.query(
        f"SELECT * FROM audit_logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*args, size, (page - 1) * size),
    )
    return {"total": total, "items": [dict(r) for r in rows], "page": page, "size": size}
