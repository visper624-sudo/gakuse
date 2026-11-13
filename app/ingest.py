"""知识导入与解析服务：落盘 → 解析 → 切片 → 向量化 → 入库（带进度与去重）。"""
from __future__ import annotations

import hashlib
import re
import time
import traceback
import uuid
from pathlib import Path

from . import config, db, embed, llm, parsers, permissions, search
from .chunker import chunk_doc
from .util import estimate_tokens, now_iso, truncate


class IngestError(RuntimeError):
    pass


class DuplicateDoc(IngestError):
    def __init__(self, ku_id: int, title: str):
        super().__init__(f"文档已存在：{title}")
        self.ku_id = ku_id
        self.title = title


def safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip() or "unnamed"
    return name[:180]


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _task_update(task_id: str, **fields) -> None:
    if not task_id:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE ingest_tasks SET {sets}, updated_at=? WHERE task_id=?",
               (*fields.values(), now_iso(), task_id))


def create_task(filename: str, task_id: str | None = None) -> str:
    task_id = task_id or uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO ingest_tasks(task_id,filename,status,progress,message,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (task_id, filename, "pending", 0, "排队中", now_iso(), now_iso()),
    )
    return task_id


def _insert_chunks(ku_id: int, chunks: list[dict], task_id: str | None) -> None:
    total = max(len(chunks), 1)
    embed_cfg = llm.get_config()
    for i, ch in enumerate(chunks):
        vec = embed.embed(ch["content"], embed_cfg, strict=True)
        cid = db.execute(
            """INSERT INTO chunks(ku_id,chunk_index,page_no,para_index,char_start,char_end,
                                  heading_path,content,token_count,vec,norm)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (ku_id, i, ch["page_no"], ch["para_index"], ch["char_start"], ch["char_end"],
             ch["heading_path"], ch["content"], ch["token_count"], embed.pack(vec),
             round(sum(v * v for v in vec), 6)),
        )
        from collections import Counter

        from .util import tokenize

        tf = Counter(tokenize(ch["content"]))
        db.execute_many(
            "INSERT INTO chunk_terms(chunk_id,term,tf) VALUES(?,?,?)",
            [(cid, t, c) for t, c in tf.items()],
        )
        if task_id and i % 3 == 0:
            _task_update(task_id, progress=35 + int(60 * (i + 1) / total))


def process_file(path: Path, original_name: str, ctx: dict,
                 category: str = "未分类", task_id: str | None = None,
                 owner_dept: int | None = None, title: str | None = None) -> int:
    """解析并入库单个文件，返回知识单元 ID。"""
    try:
        _task_update(task_id, status="parsing", progress=10, message="解析文档结构")
        parsed = parsers.parse_file(path)
        if not parsed.blocks:
            raise IngestError("未解析到有效文本内容")

        md5 = _md5(path)
        dup = db.query_one("SELECT id, title FROM knowledge_units WHERE md5=?", (md5,))
        if dup:
            raise DuplicateDoc(dup["id"], dup["title"])

        _task_update(task_id, progress=30, message="文本切片")
        chunks = chunk_doc(parsed)
        if not chunks:
            raise IngestError("切片结果为空")

        first_head = next((b.text.strip() for b in parsed.blocks if b.level > 0 and len(b.text.strip()) < 60), "")
        title = title or first_head or Path(original_name).stem

        code = db.next_code("KU")
        summary = truncate(re.sub(r"\s+", " ", parsed.full_text).strip(), 160)
        ku_id = db.execute(
            """INSERT INTO knowledge_units(code,title,category,format,source_filename,md5,owner_dept,
                status,chunk_count,char_count,token_count,summary,created_by,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, title[:120], category or "未分类", parsed.format, safe_name(original_name), md5,
             owner_dept or ctx.get("dept_id"), "enabled", len(chunks), parsed.text_length,
             sum(c["token_count"] for c in chunks), summary, ctx.get("id"), now_iso(), now_iso()),
        )

        _task_update(task_id, status="vectorizing", progress=35, message="语义向量化与索引构建")
        _insert_chunks(ku_id, chunks, task_id)

        # 安全默认值：不上架任何公开权限，仅授予创建者个人访问权
        permissions.set_ku_perms(ku_id, global_public=False, depts=[], roles=[], users=[ctx["id"]])
        search.invalidate_index()

        _task_update(task_id, status="done", progress=100, message=f"已完成 {len(chunks)} 个切片",
                     ku_id=ku_id)
        db.execute("UPDATE ingest_tasks SET ku_id=? WHERE task_id=?", (ku_id, task_id))
        db.audit(ctx.get("id"), ctx.get("username"), "kb.ingest", f"ku:{ku_id}",
                 f"{original_name} → {len(chunks)} 切片")
        return ku_id
    except DuplicateDoc as exc:
        _task_update(task_id, status="failed", progress=100, message=str(exc), ku_id=exc.ku_id)
        raise
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _task_update(task_id, status="failed", progress=100, message=f"失败：{exc}")
        if isinstance(exc, IngestError):
            raise
        raise IngestError(str(exc)) from exc


def reparse(ku_id: int, ctx: dict) -> int:
    """按最新切片参数重建索引（保留权限配置）。"""
    ku = db.query_one("SELECT * FROM knowledge_units WHERE id=?", (ku_id,))
    if not ku:
        raise IngestError("知识单元不存在")
    path = config.UPLOAD_DIR / (ku["source_filename"] or "")
    if not path.exists():
        raise IngestError("原始文件缺失，无法重建索引")
    parsed = parsers.parse_file(path)
    chunks = chunk_doc(parsed)
    db.execute("DELETE FROM chunk_terms WHERE chunk_id IN (SELECT id FROM chunks WHERE ku_id=?)", (ku_id,))
    db.execute("DELETE FROM chunks WHERE ku_id=?", (ku_id,))
    _insert_chunks(ku_id, chunks, None)
    db.execute(
        "UPDATE knowledge_units SET chunk_count=?, char_count=?, token_count=?, updated_at=? WHERE id=?",
        (len(chunks), parsed.text_length, sum(c["token_count"] for c in chunks), now_iso(), ku_id),
    )
    search.invalidate_index()
    db.audit(ctx.get("id"), ctx.get("username"), "kb.reparse", f"ku:{ku_id}", f"{len(chunks)} 切片")
    return len(chunks)


def delete_ku(ku_id: int, ctx: dict) -> None:
    db.execute("DELETE FROM chunk_terms WHERE chunk_id IN (SELECT id FROM chunks WHERE ku_id=?)", (ku_id,))
    db.execute("DELETE FROM chunks WHERE ku_id=?", (ku_id,))
    db.execute("DELETE FROM ku_perms WHERE ku_id=?", (ku_id,))
    db.execute("DELETE FROM knowledge_units WHERE id=?", (ku_id,))
    db.execute("DELETE FROM faq_candidates WHERE related_ku LIKE ?", (f"%{ku_id}%",))
    search.invalidate_index()
    permissions.bump_version()
    db.audit(ctx.get("id"), ctx.get("username"), "kb.delete", f"ku:{ku_id}", "")


def mark_duplicate(ku_id: int) -> bool:
    return db.query_one("SELECT id FROM knowledge_units WHERE id=?", (ku_id,)) is not None


def ingest_upload(filename: str, content: bytes, ctx: dict, category: str = "未分类") -> tuple[int, str]:
    """保存并同步处理一个上传文件，返回 (ku_id, task_id)。"""
    name = safe_name(filename)
    stored = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{name}"
    dest = config.UPLOAD_DIR / stored
    dest.write_bytes(content)
    task_id = create_task(name)
    try:
        ku_id = process_file(dest, name, ctx, category=category, task_id=task_id)
    except DuplicateDoc:
        dest.unlink(missing_ok=True)
        raise
    return ku_id, task_id


def stats() -> dict:
    return {
        "ku_total": db.scalar("SELECT COUNT(*) FROM knowledge_units"),
        "chunk_total": db.scalar("SELECT COUNT(*) FROM chunks"),
        "token_total": db.scalar("SELECT COALESCE(SUM(token_count),0) FROM knowledge_units"),
        "format_dist": [dict(r) for r in db.query(
            "SELECT format, COUNT(*) AS cnt FROM knowledge_units GROUP BY format ORDER BY cnt DESC")],
        "category_dist": [dict(r) for r in db.query(
            "SELECT category, COUNT(*) AS cnt FROM knowledge_units GROUP BY category ORDER BY cnt DESC")],
    }
