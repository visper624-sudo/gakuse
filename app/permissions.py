"""组织身份、功能权限（RBAC）与四维数据权限（OR 语义）鉴权引擎。

核心约定（对应需求文档 2.9.4）：
  * 默认无权限：知识单元未配置任何权限实体时，除创建者外一律不可见；
  * 四类权限实体：global（全局公开）/ department（部门共享）/ role（角色可见）/ user（个人专属）；
  * 判定为充分条件（OR）：命中任意一类即放行；
  * 鉴权在服务端完成，且必须发生在“召回之后、拼 Prompt 之前”。
"""
from __future__ import annotations

import threading
from typing import Iterable

from . import db

# ---------------------------------------------------------------- 功能权限目录
PERM_CATALOG = [
    {
        "group": "菜单权限",
        "items": [
            {"code": "menu:dashboard", "name": "运营看板"},
            {"code": "menu:chat", "name": "AI 问答工作台"},
            {"code": "menu:kb", "name": "知识维护与导入"},
            {"code": "menu:faq", "name": "知识沉淀与运营"},
            {"code": "menu:org", "name": "组织与系统配置"},
        ],
    },
    {
        "group": "操作权限",
        "items": [
            {"code": "kb:create", "name": "知识导入/新增"},
            {"code": "kb:update", "name": "知识编辑/切片重建"},
            {"code": "kb:delete", "name": "知识删除/停用"},
            {"code": "kb:perm", "name": "数据权限配置"},
            {"code": "faq:review", "name": "FAQ 审核发布"},
            {"code": "gap:handle", "name": "知识缺口处理"},
            {"code": "ai:chat", "name": "AI 问答调用"},
            {"code": "org:manage", "name": "组织/用户/角色维护"},
            {"code": "sys:config", "name": "模型与系统参数配置"},
            {"code": "audit:view", "name": "审计日志查看"},
        ],
    },
]

ALL_PERMS = [i["code"] for g in PERM_CATALOG for i in g["items"]]

ROLE_DEFAULT_PERMS = {
    "admin": ALL_PERMS,
    "knowledge_admin": [
        "menu:dashboard", "menu:chat", "menu:kb", "menu:faq",
        "kb:create", "kb:update", "kb:delete", "kb:perm", "faq:review", "gap:handle", "ai:chat",
    ],
    "user": ["menu:dashboard", "menu:chat", "ai:chat"],
}


# ---------------------------------------------------------------- 组织
def dept_ancestors(dept_id: int | None) -> list[int]:
    """直属部门 + 所有上级部门（部门共享权限按“向上包含”语义生效）。"""
    out: list[int] = []
    cur = dept_id
    guard = 0
    while cur and guard < 20:
        out.append(cur)
        row = db.query_one("SELECT parent_id FROM depts WHERE id=?", (cur,))
        if not row:
            break
        cur = row["parent_id"]
        guard += 1
    return out


def dept_children(dept_id: int) -> list[int]:
    out: list[int] = []
    stack = [dept_id]
    while stack:
        cur = stack.pop()
        for r in db.query("SELECT id FROM depts WHERE parent_id=?", (cur,)):
            out.append(r["id"])
            stack.append(r["id"])
    return out


def dept_tree() -> list[dict]:
    rows = db.query("SELECT * FROM depts ORDER BY sort, id")
    nodes = {r["id"]: dict(r, children=[]) for r in rows}
    roots: list[dict] = []
    for n in nodes.values():
        pid = n.get("parent_id")
        if pid and pid in nodes:
            nodes[pid]["children"].append(n)
        else:
            roots.append(n)
    return roots


# ---------------------------------------------------------------- 用户上下文
PERSONAL_PERMS = {"ai:chat"}  # 所有登录用户天然具备的权限


def build_ctx(user_id: int) -> dict | None:
    u = db.query_one(
        """SELECT u.*, d.name AS dept_name FROM users u
           LEFT JOIN depts d ON d.id = u.dept_id WHERE u.id=?""",
        (user_id,),
    )
    if not u or u["status"] != "active":
        return None
    roles = db.query(
        """SELECT r.id, r.code, r.name FROM user_roles ur JOIN roles r ON r.id=ur.role_id
           WHERE ur.user_id=?""",
        (user_id,),
    )
    role_ids = [r["id"] for r in roles]
    perms: set[str] = set(PERSONAL_PERMS)
    if role_ids:
        q = ",".join("?" * len(role_ids))
        for row in db.query(f"SELECT perm FROM role_perms WHERE role_id IN ({q})", role_ids):
            perms.add(row["perm"])
    dept = u["dept_id"]
    return {
        "id": u["id"],
        "username": u["username"],
        "name": u["name"],
        "title": u["title"],
        "dept_id": dept,
        "dept_name": u["dept_name"],
        "dept_closure": dept_ancestors(dept) if dept else [],
        "role_ids": role_ids,
        "roles": [{"id": r["id"], "code": r["code"], "name": r["name"]} for r in roles],
        "perms": sorted(perms),
    }


def has_perm(ctx: dict, perm: str) -> bool:
    return perm in (ctx.get("perms") or [])


def require_perm(ctx: dict | None, perm: str) -> bool:
    return bool(ctx) and has_perm(ctx, perm)


# ---------------------------------------------------------------- 四维数据权限
_cache_lock = threading.Lock()
_vis_cache: dict[int, set[int]] = {}
_ku_cache: dict[int, dict] = {}
_version = 0


def bump_version() -> None:
    """权限变更后失效全部鉴权缓存（对应 2.9.7“权限规则即时生效并同步至鉴权引擎缓存”）。"""
    global _version
    with _cache_lock:
        _version += 1
        _vis_cache.clear()
        _ku_cache.clear()


def perm_version() -> int:
    return _version


def ku_perm_map(ku_id: int) -> dict:
    with _cache_lock:
        if ku_id in _ku_cache:
            return _ku_cache[ku_id]
    rows = db.query("SELECT perm_type, principal_id FROM ku_perms WHERE ku_id=?", (ku_id,))
    m = {"global": False, "department": set(), "role": set(), "user": set()}
    for r in rows:
        t, p = r["perm_type"], r["principal_id"]
        if t == "global":
            m["global"] = True
        elif t in ("department", "role"):
            m[t].add(int(p))
        elif t == "user":
            m["user"].add(int(p))
    with _cache_lock:
        _ku_cache[ku_id] = m
    return m


def can_access(ctx: dict, ku_id: int) -> bool:
    m = ku_perm_map(ku_id)
    if m["global"]:
        return True
    if m["department"] & set(ctx.get("dept_closure") or []):
        return True
    if m["role"] & set(ctx.get("role_ids") or []):
        return True
    if ctx.get("id") in m["user"]:
        return True
    return False


def visible_ku_ids(ctx: dict) -> set[int]:
    """当前用户可见的知识单元 ID 集合（服务端唯一鉴权口径）。"""
    uid = ctx["id"]
    with _cache_lock:
        cached = _vis_cache.get(uid)
        if cached is not None:
            return cached

    rows = db.query(
        """SELECT ku_id FROM ku_perms WHERE perm_type='global'
           UNION
           SELECT ku_id FROM ku_perms WHERE perm_type='user' AND principal_id=?""",
        (str(uid),),
    )
    visible = {r["ku_id"] for r in rows}

    depts = [str(d) for d in (ctx.get("dept_closure") or [])]
    if depts:
        q = ",".join("?" * len(depts))
        for r in db.query(f"SELECT ku_id FROM ku_perms WHERE perm_type='department' AND principal_id IN ({q})", depts):
            visible.add(r["ku_id"])
    roles = [str(r) for r in (ctx.get("role_ids") or [])]
    if roles:
        q = ",".join("?" * len(roles))
        for r in db.query(f"SELECT ku_id FROM ku_perms WHERE perm_type='role' AND principal_id IN ({q})", roles):
            visible.add(r["ku_id"])

    with _cache_lock:
        _vis_cache[uid] = visible
    return visible


def split_candidates(ctx: dict, candidate_ids: Iterable[int]) -> tuple[list[int], list[int]]:
    """把召回候选切分为（放行、拦截）两组，保持入参顺序。"""
    visible = visible_ku_ids(ctx)
    allowed, denied = [], []
    for cid in candidate_ids:
        (allowed if cid in visible else denied).append(cid)
    return allowed, denied


def set_ku_perms(ku_id: int, global_public: bool, depts: Iterable[int],
                 roles: Iterable[int], users: Iterable[int]) -> None:
    db.execute("DELETE FROM ku_perms WHERE ku_id=?", (ku_id,))
    rows: list[tuple] = []
    if global_public:
        rows.append((ku_id, "global", "*"))
    rows += [(ku_id, "department", str(d)) for d in depts]
    rows += [(ku_id, "role", str(r)) for r in roles]
    rows += [(ku_id, "user", str(u)) for u in users]
    if rows:
        db.execute_many("INSERT OR IGNORE INTO ku_perms(ku_id,perm_type,principal_id) VALUES(?,?,?)", rows)
    bump_version()


def get_ku_perms(ku_id: int) -> dict:
    rows = db.query("SELECT perm_type, principal_id FROM ku_perms WHERE ku_id=?", (ku_id,))
    out = {"global": False, "departments": [], "roles": [], "users": []}
    for r in rows:
        t = r["perm_type"]
        if t == "global":
            out["global"] = True
        elif t == "department":
            out["departments"].append(int(r["principal_id"]))
        elif t == "role":
            out["roles"].append(int(r["principal_id"]))
        elif t == "user":
            out["users"].append(int(r["principal_id"]))
    return out
