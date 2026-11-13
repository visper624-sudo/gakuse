"""测试夹具：使用独立的临时数据库，避免污染演示数据。

注意：必须在导入 app.* 之前设置 KB_DB_PATH / KB_DATA_DIR。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMP = ROOT / "build" / "test-tmp" / f"run_{__import__('uuid').uuid4().hex[:8]}"
TMP.mkdir(parents=True, exist_ok=True)
import os  # noqa: E402

os.environ["KB_DB_PATH"] = str(TMP / "test.sqlite3")
os.environ["KB_DATA_DIR"] = str(TMP)
os.environ["KB_JWT_SECRET"] = "test-secret"

from app import config, db, embed, ingest, permissions, search  # noqa: E402
from app.util import hash_password  # noqa: E402

config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def reset_db() -> None:
    """清空测试库（不删文件，避免多测试类之间文件锁冲突）。"""
    db.init_db()
    conn = db.connect()
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    for name in names:
        conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.commit()
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    search.invalidate_index()
    permissions.bump_version()
    db.init_db()


def cleanup() -> None:
    shutil.rmtree(TMP, ignore_errors=True)


# ---------------------------------------------------------------- 建数据
def make_org() -> dict:
    root = db.execute("INSERT INTO depts(name,parent_id,sort,created_at) VALUES('总部',NULL,0,'2026-01-01')")
    fin = db.execute("INSERT INTO depts(name,parent_id,sort,created_at) VALUES('财务部',?,1,'2026-01-01')", (root,))
    sub = db.execute("INSERT INTO depts(name,parent_id,sort,created_at) VALUES('结算组',?,1,'2026-01-01')", (fin,))
    hr = db.execute("INSERT INTO depts(name,parent_id,sort,created_at) VALUES('人力资源部',?,2,'2026-01-01')", (root,))
    role_kadmin = db.execute("INSERT INTO roles(code,name) VALUES('knowledge_admin','知识管理员')")
    role_user = db.execute("INSERT INTO roles(code,name) VALUES('user','普通用户')")
    for perm in ("menu:kb", "menu:faq", "kb:create", "kb:update", "kb:delete", "kb:perm",
                 "faq:review", "gap:handle", "ai:chat"):
        db.execute("INSERT INTO role_perms(role_id,perm) VALUES(?,?)", (role_kadmin, perm))
    db.execute("INSERT INTO role_perms(role_id,perm) VALUES(?,?)", (role_user, "ai:chat"))

    def add_user(username, name, dept_id, role_id):
        pwd, salt = hash_password("123456")
        uid = db.execute(
            "INSERT INTO users(username,name,password_hash,salt,dept_id,status,created_at) "
            "VALUES(?,?,?,?,?,?,'2026-01-01')", (username, name, pwd, salt, dept_id, "active"))
        db.execute("INSERT INTO user_roles(user_id,role_id) VALUES(?,?)", (uid, role_id))
        return uid

    return {
        "root": root, "fin": fin, "sub": sub, "hr": hr,
        "role_kadmin": role_kadmin, "role_user": role_user,
        "zhang": add_user("zhang", "张三", fin, role_user),
        "li": add_user("li", "李四", sub, role_user),
        "wang": add_user("wang", "王五", hr, role_kadmin),
    }


def make_doc(name: str, text: str, ctx: dict, perms_spec: dict | None = None) -> int:
    upload = config.UPLOAD_DIR / f"{name}.txt"
    upload.write_text(text, encoding="utf-8")
    ku_id = ingest.process_file(upload, upload.name, ctx, category="测试")
    if perms_spec is not None:
        permissions.set_ku_perms(
            ku_id,
            global_public=bool(perms_spec.get("global")),
            depts=perms_spec.get("departments") or [],
            roles=perms_spec.get("roles") or [],
            users=perms_spec.get("users") or [],
        )
    return ku_id


def ctx_of(user_id: int) -> dict:
    return permissions.build_ctx(user_id)


def staff_ctx(users: dict) -> dict:
    return permissions.build_ctx(users["wang"])
