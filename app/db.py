"""SQLite 数据访问层：建表、连接、通用查询封装。

单文件数据库（WAL 模式），无需额外部署；向量以 BLOB 存储，关键词索引自建倒排表。
"""
from __future__ import annotations

import atexit
import sqlite3
import threading
from typing import Any, Iterable

from . import config
from .util import now_iso

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS depts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    parent_id   INTEGER,
    sort        INTEGER DEFAULT 0,
    remark      TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    remark      TEXT
);

CREATE TABLE IF NOT EXISTS role_perms (
    role_id     INTEGER NOT NULL,
    perm        TEXT NOT NULL,
    PRIMARY KEY (role_id, perm)
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    dept_id       INTEGER,
    title         TEXT,
    status        TEXT DEFAULT 'active',
    created_at    TEXT,
    last_login    TEXT
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS knowledge_units (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    category        TEXT,
    format          TEXT,
    source_filename TEXT,
    md5             TEXT,
    owner_dept      INTEGER,
    status          TEXT DEFAULT 'enabled',
    chunk_count     INTEGER DEFAULT 0,
    char_count      INTEGER DEFAULT 0,
    token_count     INTEGER DEFAULT 0,
    summary         TEXT,
    created_by      INTEGER,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ku_id       INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    page_no     INTEGER,
    para_index  INTEGER,
    char_start  INTEGER,
    char_end    INTEGER,
    heading_path TEXT,
    content     TEXT,
    token_count INTEGER,
    vec         BLOB,
    norm        REAL
);
CREATE INDEX IF NOT EXISTS idx_chunks_ku ON chunks(ku_id);

CREATE TABLE IF NOT EXISTS chunk_terms (
    chunk_id INTEGER NOT NULL,
    term     TEXT NOT NULL,
    tf       INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, term)
);
CREATE INDEX IF NOT EXISTS idx_chunk_terms_term ON chunk_terms(term);

CREATE TABLE IF NOT EXISTS ku_perms (
    ku_id        INTEGER NOT NULL,
    perm_type    TEXT NOT NULL,           -- global | department | role | user
    principal_id TEXT NOT NULL,           -- global 存 '*'
    PRIMARY KEY (ku_id, perm_type, principal_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    title      TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    role        TEXT NOT NULL,             -- user | assistant
    content     TEXT NOT NULL,
    citations   TEXT,
    trace       TEXT,
    denied      INTEGER DEFAULT 0,
    token_in    INTEGER DEFAULT 0,
    token_out   INTEGER DEFAULT 0,
    latency_ms  INTEGER DEFAULT 0,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS query_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER,
    user_id        INTEGER,
    dept_id        INTEGER,
    query          TEXT,
    query_norm     TEXT,
    top1_sim       REAL DEFAULT 0,
    hit_count      INTEGER DEFAULT 0,
    allowed_ids    TEXT,
    denied_ids     TEXT,
    cache_hit      INTEGER DEFAULT 0,
    faq_id         INTEGER,
    token_in       INTEGER DEFAULT 0,
    token_out      INTEGER DEFAULT 0,
    first_token_ms INTEGER DEFAULT 0,
    total_ms       INTEGER DEFAULT 0,
    answered       INTEGER DEFAULT 1,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_query_logs_created ON query_logs(created_at);

CREATE TABLE IF NOT EXISTS faq_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_key   TEXT UNIQUE,
    question      TEXT NOT NULL,
    freq          INTEGER DEFAULT 0,
    related_ku    TEXT,
    answer        TEXT,
    confidence    REAL DEFAULT 0,
    status        TEXT DEFAULT 'pending',   -- pending | published | rejected
    sample_query  TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS faqs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    question     TEXT NOT NULL,
    question_norm TEXT NOT NULL,
    answer       TEXT NOT NULL,
    related_ku   TEXT,
    status       TEXT DEFAULT 'online',
    cache_enabled INTEGER DEFAULT 1,
    hit_count    INTEGER DEFAULT 0,
    vec          BLOB,
    published_by INTEGER,
    published_at TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS gaps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    question       TEXT NOT NULL,
    question_norm  TEXT NOT NULL UNIQUE,
    dept_id        INTEGER,
    freq           INTEGER DEFAULT 1,
    top1_sim       REAL DEFAULT 0,
    suggested_category TEXT,
    status         TEXT DEFAULT 'open',   -- open | task | resolved | ignored
    created_at     TEXT,
    last_seen      TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_id     INTEGER,
    title      TEXT NOT NULL,
    category   TEXT,
    owner      TEXT,
    priority   TEXT DEFAULT 'medium',
    status     TEXT DEFAULT 'todo',      -- todo | doing | done
    created_by INTEGER,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    username   TEXT,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT,
    ip         TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);

CREATE TABLE IF NOT EXISTS ingest_tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ku_id      INTEGER,
    task_id    TEXT,
    filename   TEXT,
    status     TEXT DEFAULT 'pending',   -- pending | parsing | vectorizing | done | failed
    progress   INTEGER DEFAULT 0,
    message    TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


_local = threading.local()


def connect() -> sqlite3.Connection:
    """每线程一个连接（http.server 为多线程模型）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(config.DB_PATH), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        _local.conn = None


atexit.register(close_connection)


def init_db() -> None:
    conn = connect()
    conn.executescript(_SCHEMA)
    conn.commit()


def query(sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(connect().execute(sql, tuple(args)).fetchall())


def query_one(sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(args)).fetchone()


def scalar(sql: str, args: Iterable[Any] = (), default: Any = 0) -> Any:
    row = connect().execute(sql, tuple(args)).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def execute(sql: str, args: Iterable[Any] = ()) -> int:
    conn = connect()
    cur = conn.execute(sql, tuple(args))
    conn.commit()
    return cur.lastrowid if cur.lastrowid else cur.rowcount


def execute_many(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    conn = connect()
    conn.executemany(sql, [tuple(r) for r in rows])
    conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- kv
def kv_get(k: str, default: str | None = None) -> str | None:
    row = query_one("SELECT v FROM kv WHERE k=?", (k,))
    return row["v"] if row else default


def kv_set(k: str, v: str) -> None:
    execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def next_code(prefix: str) -> str:
    """生成业务编号：KU-20261112-0007。"""
    from datetime import datetime

    d = datetime.now().strftime("%Y%m%d")
    n = scalar("SELECT COUNT(*) FROM knowledge_units WHERE code LIKE ?", (f"KU-{d}-%",)) + 1
    return f"KU-{d}-{n:04d}"


def audit(user_id: int | None, username: str | None, action: str, target: str = "",
          detail: str = "", ip: str = "") -> None:
    execute(
        "INSERT INTO audit_logs(user_id,username,action,target,detail,ip,created_at) VALUES(?,?,?,?,?,?,?)",
        (user_id, username, action, target, detail[:1000], ip, now_iso()),
    )
