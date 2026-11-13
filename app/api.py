"""HTTP API 层：路由注册、参数解析、鉴权拦截、SSE 流式输出。"""
from __future__ import annotations

import json
import re
import traceback
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Iterator

from . import analytics, config, db, faq, ingest, llm, permissions, rag, reindex, search
from .util import hash_password, jloads, jdumps, jwt_decode, jwt_encode, now_iso, truncate, verify_password


# ---------------------------------------------------------------- 请求/响应
@dataclass
class Request:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes = b""
    ip: str = ""
    params: dict = field(default_factory=dict)
    _json: dict | None = None
    _files: list | None = None
    _ctx: dict | None = None

    @property
    def token(self) -> str | None:
        auth = self.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if self.query.get("token"):
            return self.query["token"]
        cookie = self.headers.get("cookie", "")
        m = re.search(r"kb_token=([^;]+)", cookie)
        return m.group(1) if m else None

    @property
    def ctx(self) -> dict | None:
        if self._ctx is None:
            token = self.token
            payload = jwt_decode(token) if token else None
            if payload:
                self._ctx = permissions.build_ctx(int(payload["sub"]))
        return self._ctx

    def json(self) -> dict:
        if self._json is None:
            if not self.body:
                self._json = {}
            else:
                try:
                    self._json = json.loads(self.body.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    self._json = {}
        return self._json

    def files(self) -> list[dict]:
        if self._files is None:
            self._files = _parse_multipart(self.headers.get("content-type", ""), self.body)
        return self._files

    def form(self, name: str, default: str = "") -> str:
        for f in self.files():
            if f["name"] == name and not f["filename"]:
                return f["data"].decode("utf-8", "ignore")
        return (self.json() or {}).get(name, default)


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict = field(default_factory=dict)


@dataclass
class StreamResponse:
    generator: Callable[[], Iterator[bytes]]
    content_type: str = "text/event-stream; charset=utf-8"
    headers: dict = field(default_factory=dict)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.status = status
        self.code = code


def ok(data=None, **extra) -> Response:
    payload = {"code": 0, "data": data if data is not None else {}}
    payload.update(extra)
    return Response(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def fail(message: str, status: int = 400, code: str = "bad_request") -> Response:
    return Response(status, json.dumps({"code": 1, "message": message, "error": code},
                                       ensure_ascii=False).encode("utf-8"))


# ---------------------------------------------------------------- multipart
def _parse_multipart(content_type: str, body: bytes) -> list[dict]:
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m or not body:
        return []
    boundary = m.group(1).strip('"').encode()
    parts = body.split(b"--" + boundary)
    out = []
    for part in parts:
        if not part or part in (b"--\r\n", b"--\r\n\r\n", b"--"):
            continue
        part = part.lstrip(b"\r\n")
        if part.startswith(b"--"):
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        if not _:
            continue
        data = data.rstrip(b"\r\n")
        headers = {}
        for line in head.decode("utf-8", "ignore").split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        disp = headers.get("content-disposition", "")
        name_m = re.search(r'name="([^"]*)"', disp)
        file_m = re.search(r'filename="([^"]*)"', disp)
        out.append({
            "name": name_m.group(1) if name_m else "",
            "filename": file_m.group(1) if file_m else "",
            "content_type": headers.get("content-type", ""),
            "data": data,
            "size": len(data),
        })
    return out


# ---------------------------------------------------------------- 路由表
ROUTES: list[tuple[str, re.Pattern, Callable, str | None]] = []


def route(method: str, pattern: str, perm: str | None = None):
    compiled = re.compile("^" + pattern + "$")

    def deco(fn: Callable):
        ROUTES.append((method, compiled, fn, perm))
        return fn

    return deco


def _require(req: Request, perm: str | None) -> dict:
    ctx = req.ctx
    if not ctx:
        raise ApiError("登录已过期，请重新登录", 401, "unauthorized")
    if perm and not permissions.has_perm(ctx, perm):
        raise ApiError(f"缺少功能权限：{perm}", 403, "forbidden")
    return ctx


# ================================================================ 认证
@route("POST", r"/api/auth/login")
def login(req: Request) -> Response:
    body = req.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    row = db.query_one("SELECT * FROM users WHERE username=?", (username,))
    if not row or not verify_password(password, row["password_hash"], row["salt"]):
        db.audit(None, username, "auth.login.fail", "", "密码错误", req.ip)
        raise ApiError("账号或密码错误", 401, "invalid_credentials")
    if row["status"] != "active":
        raise ApiError("账号已被停用，请联系系统管理员", 403, "inactive")
    ctx = permissions.build_ctx(row["id"])
    token = jwt_encode({"sub": str(row["id"]), "username": row["username"]})
    db.execute("UPDATE users SET last_login=COALESCE(last_login, ?) WHERE id=?", (now_iso(), row["id"]))
    db.audit(row["id"], row["username"], "auth.login", f"user:{row['id']}", "", req.ip)
    return ok({"token": token, "user": ctx})


@route("GET", r"/api/auth/me")
def me(req: Request) -> Response:
    ctx = _require(req, None)
    return ok({"user": ctx})


@route("POST", r"/api/auth/logout")
def logout(req: Request) -> Response:
    ctx = req.ctx
    if ctx:
        db.audit(ctx["id"], ctx["username"], "auth.logout", "", "")
    return ok({})


# ================================================================ 组织
@route("GET", r"/api/org/tree")
def org_tree(req: Request) -> Response:
    _require(req, None)

    def decorate(nodes: list[dict]) -> list[dict]:
        for n in nodes:
            n["user_count"] = db.scalar("SELECT COUNT(*) FROM users WHERE dept_id=?", (n["id"],))
            n["ku_count"] = db.scalar("SELECT COUNT(*) FROM knowledge_units WHERE owner_dept=?", (n["id"],))
            decorate(n["children"])
        return nodes

    return ok({"tree": decorate(permissions.dept_tree())})


@route("POST", r"/api/org/depts", "org:manage")
def create_dept(req: Request) -> Response:
    ctx = _require(req, "org:manage")
    body = req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError("部门名称不能为空")
    did = db.execute("INSERT INTO depts(name,parent_id,sort,remark,created_at) VALUES(?,?,?,?,?)",
                     (name, body.get("parent_id"), int(body.get("sort", 0)), body.get("remark", ""), now_iso()))
    db.audit(ctx["id"], ctx["username"], "org.dept.create", f"dept:{did}", name)
    return ok({"id": did})


@route("PUT", r"/api/org/depts/(?P<did>\d+)", "org:manage")
def update_dept(req: Request) -> Response:
    ctx = _require(req, "org:manage")
    did = int(req.params["did"])
    body = req.json()
    db.execute("UPDATE depts SET name=COALESCE(?,name), remark=COALESCE(?,remark), sort=COALESCE(?,sort) WHERE id=?",
               (body.get("name"), body.get("remark"), body.get("sort"), did))
    db.audit(ctx["id"], ctx["username"], "org.dept.update", f"dept:{did}", jdumps(body))
    return ok({})


@route("DELETE", r"/api/org/depts/(?P<did>\d+)", "org:manage")
def delete_dept(req: Request) -> Response:
    ctx = _require(req, "org:manage")
    did = int(req.params["did"])
    if db.scalar("SELECT COUNT(*) FROM users WHERE dept_id=?", (did,)):
        raise ApiError("该部门下仍有用户，无法删除")
    if db.scalar("SELECT COUNT(*) FROM depts WHERE parent_id=?", (did,)):
        raise ApiError("请先删除子部门")
    db.execute("DELETE FROM depts WHERE id=?", (did,))
    db.audit(ctx["id"], ctx["username"], "org.dept.delete", f"dept:{did}", "")
    return ok({})


@route("GET", r"/api/org/users")
def list_users(req: Request) -> Response:
    _require(req, None)
    keyword = req.query.get("keyword", "")
    dept_id = req.query.get("dept_id")
    sql = """SELECT u.id,u.username,u.name,u.title,u.status,u.dept_id,d.name AS dept_name,u.last_login
             FROM users u LEFT JOIN depts d ON d.id=u.dept_id WHERE 1=1"""
    args: list = []
    if keyword:
        sql += " AND (u.username LIKE ? OR u.name LIKE ?)"
        args += [f"%{keyword}%", f"%{keyword}%"]
    if dept_id:
        ids = [int(dept_id)] + permissions.dept_children(int(dept_id))
        sql += f" AND u.dept_id IN ({','.join('?' * len(ids))})"
        args += ids
    sql += " ORDER BY u.id"
    rows = [dict(r) for r in db.query(sql, args)]
    for r in rows:
        roles = db.query("SELECT r.id,r.name,r.code FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE ur.user_id=?",
                         (r["id"],))
        r["roles"] = [dict(x) for x in roles]
    return ok({"items": rows, "total": len(rows)})


@route("POST", r"/api/org/users", "org:manage")
def create_user(req: Request) -> Response:
    ctx = _require(req, "org:manage")
    body = req.json()
    username = (body.get("username") or "").strip()
    name = (body.get("name") or "").strip()
    if not username or not name:
        raise ApiError("账号与姓名不能为空")
    if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        raise ApiError("账号已存在")
    pwd_hash, salt = hash_password(body.get("password") or "123456")
    uid = db.execute(
        "INSERT INTO users(username,name,password_hash,salt,dept_id,title,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (username, name, pwd_hash, salt, body.get("dept_id"), body.get("title", ""), "active", now_iso()),
    )
    for rid in body.get("role_ids") or []:
        db.execute("INSERT OR IGNORE INTO user_roles(user_id,role_id) VALUES(?,?)", (uid, rid))
    db.audit(ctx["id"], ctx["username"], "org.user.create", f"user:{uid}", username)
    return ok({"id": uid})


@route("PUT", r"/api/org/users/(?P<uid>\d+)", "org:manage")
def update_user(req: Request) -> Response:
    ctx = _require(req, "org:manage")
    uid = int(req.params["uid"])
    body = req.json()
    db.execute(
        """UPDATE users SET name=COALESCE(?,name), dept_id=COALESCE(?,dept_id),
           title=COALESCE(?,title), status=COALESCE(?,status) WHERE id=?""",
        (body.get("name"), body.get("dept_id"), body.get("title"), body.get("status"), uid),
    )
    if body.get("role_ids") is not None:
        db.execute("DELETE FROM user_roles WHERE user_id=?", (uid,))
        for rid in body["role_ids"]:
            db.execute("INSERT OR IGNORE INTO user_roles(user_id,role_id) VALUES(?,?)", (uid, rid))
    permissions.bump_version()
    db.audit(ctx["id"], ctx["username"], "org.user.update", f"user:{uid}", jdumps(body))
    return ok({})


@route("POST", r"/api/org/users/(?P<uid>\d+)/reset-password", "org:manage")
def reset_password(req: Request) -> Response:
    ctx = _require(req, "org:manage")
    uid = int(req.params["uid"])
    pwd = req.json().get("password") or "123456"
    pwd_hash, salt = hash_password(pwd)
    db.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (pwd_hash, salt, uid))
    db.audit(ctx["id"], ctx["username"], "org.user.resetpwd", f"user:{uid}", "")
    return ok({"password": pwd})


@route("GET", r"/api/org/roles")
def list_roles(req: Request) -> Response:
    _require(req, None)
    rows = [dict(r) for r in db.query("SELECT * FROM roles ORDER BY id")]
    for r in rows:
        r["perms"] = [x["perm"] for x in db.query("SELECT perm FROM role_perms WHERE role_id=?", (r["id"],))]
        r["user_count"] = db.scalar("SELECT COUNT(*) FROM user_roles WHERE role_id=?", (r["id"],))
    return ok({"items": rows, "catalog": permissions.PERM_CATALOG})


@route("PUT", r"/api/org/roles/(?P<rid>\d+)/perms", "org:manage")
def update_role_perms(req: Request) -> Response:
    ctx = _require(req, "org:manage")
    rid = int(req.params["rid"])
    perms = [p for p in req.json().get("perms", []) if p in permissions.ALL_PERMS]
    db.execute("DELETE FROM role_perms WHERE role_id=?", (rid,))
    db.execute_many("INSERT INTO role_perms(role_id,perm) VALUES(?,?)", [(rid, p) for p in perms])
    permissions.bump_version()
    db.audit(ctx["id"], ctx["username"], "org.role.perms", f"role:{rid}", jdumps(perms))
    return ok({"perms": perms})


# ================================================================ 知识库
def _perm_tags(ku_ids: list[int]) -> dict[int, dict]:
    if not ku_ids:
        return {}
    q = ",".join("?" * len(ku_ids))
    tags: dict[int, dict] = {kid: {"global": False, "dept": [], "role": [], "user": []} for kid in ku_ids}
    for r in db.query(f"SELECT * FROM ku_perms WHERE ku_id IN ({q})", ku_ids):
        t = tags[r["ku_id"]]
        if r["perm_type"] == "global":
            t["global"] = True
        elif r["perm_type"] == "department":
            t["dept"].append(int(r["principal_id"]))
        elif r["perm_type"] == "role":
            t["role"].append(int(r["principal_id"]))
        else:
            t["user"].append(int(r["principal_id"]))
    return tags


@route("GET", r"/api/kb", "menu:kb")
def list_kb(req: Request) -> Response:
    _require(req, None)
    page = max(1, int(req.query.get("page", 1)))
    size = min(100, max(1, int(req.query.get("size", 10))))
    where, args = ["1=1"], []
    if req.query.get("keyword"):
        where.append("(k.title LIKE ? OR k.code LIKE ? OR k.summary LIKE ?)")
        kw = f"%{req.query['keyword']}%"
        args += [kw, kw, kw]
    for fld in ("format", "category", "status"):
        if req.query.get(fld):
            where.append(f"k.{fld}=?")
            args.append(req.query[fld])
    if req.query.get("dept_id"):
        where.append("k.owner_dept=?")
        args.append(int(req.query["dept_id"]))
    clause = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM knowledge_units k WHERE {clause}", args)
    rows = db.query(
        f"""SELECT k.*, d.name AS dept_name, u.name AS creator_name
            FROM knowledge_units k LEFT JOIN depts d ON d.id=k.owner_dept
            LEFT JOIN users u ON u.id=k.created_by
            WHERE {clause} ORDER BY k.updated_at DESC LIMIT ? OFFSET ?""",
        (*args, size, (page - 1) * size),
    )
    items = [dict(r) for r in rows]
    tags = _perm_tags([i["id"] for i in items])
    visible = permissions.visible_ku_ids(req.ctx)
    for i in items:
        i["perms"] = tags.get(i["id"], {})
        i["accessible"] = i["id"] in visible
    perm_filter = req.query.get("perm")
    if perm_filter:
        def match(t: dict) -> bool:
            if perm_filter == "global":
                return t["global"]
            if perm_filter == "department":
                return bool(t["dept"])
            if perm_filter == "role":
                return bool(t["role"])
            if perm_filter == "user":
                return bool(t["user"])
            if perm_filter == "none":
                return not any(t.values())
            return True
        items = [i for i in items if match(i["perms"])]
    return ok({"items": items, "total": total, "page": page, "size": size})


@route("GET", r"/api/kb/filters", "menu:kb")
def kb_filters(req: Request) -> Response:
    _require(req, None)
    return ok({
        "categories": [r["category"] for r in db.query(
            "SELECT DISTINCT category FROM knowledge_units WHERE category IS NOT NULL ORDER BY category")],
        "formats": [r["format"] for r in db.query(
            "SELECT DISTINCT format FROM knowledge_units WHERE format IS NOT NULL ORDER BY format")],
        "depts": [dict(r) for r in db.query("SELECT id,name FROM depts ORDER BY sort,id")],
        "roles": [dict(r) for r in db.query("SELECT id,name,code FROM roles ORDER BY id")],
        "users": [dict(r) for r in db.query("SELECT id,name,username FROM users WHERE status='active' ORDER BY id")],
    })


@route("GET", r"/api/kb/(?P<kid>\d+)", "menu:kb")
def kb_detail(req: Request) -> Response:
    ctx = _require(req, None)
    kid = int(req.params["kid"])
    row = db.query_one(
        """SELECT k.*, d.name AS dept_name, u.name AS creator_name FROM knowledge_units k
           LEFT JOIN depts d ON d.id=k.owner_dept LEFT JOIN users u ON u.id=k.created_by WHERE k.id=?""",
        (kid,),
    )
    if not row:
        raise ApiError("知识单元不存在", 404, "not_found")
    data = dict(row)
    data["perms"] = permissions.get_ku_perms(kid)
    data["accessible"] = kid in permissions.visible_ku_ids(ctx)
    data["chunks"] = [dict(r) for r in db.query(
        """SELECT id,chunk_index,page_no,para_index,char_start,char_end,heading_path,
                  substr(content,1,400) AS preview,token_count FROM chunks WHERE ku_id=?
           ORDER BY chunk_index LIMIT 3""", (kid,))]
    return ok({"item": data})


@route("GET", r"/api/kb/(?P<kid>\d+)/chunks", "menu:kb")
def kb_chunks(req: Request) -> Response:
    _require(req, None)
    kid = int(req.params["kid"])
    page = max(1, int(req.query.get("page", 1)))
    size = min(50, max(1, int(req.query.get("size", 5))))
    total = db.scalar("SELECT COUNT(*) FROM chunks WHERE ku_id=?", (kid,))
    rows = db.query(
        """SELECT id,chunk_index,page_no,para_index,char_start,char_end,heading_path,content,token_count
           FROM chunks WHERE ku_id=? ORDER BY chunk_index LIMIT ? OFFSET ?""",
        (kid, size, (page - 1) * size),
    )
    return ok({"items": [dict(r) for r in rows], "total": total, "page": page, "size": size})


@route("PUT", r"/api/kb/(?P<kid>\d+)", "kb:update")
def kb_update(req: Request) -> Response:
    ctx = _require(req, "kb:update")
    kid = int(req.params["kid"])
    body = req.json()
    db.execute(
        "UPDATE knowledge_units SET title=COALESCE(?,title), category=COALESCE(?,category), updated_at=? WHERE id=?",
        (body.get("title"), body.get("category"), now_iso(), kid),
    )
    db.audit(ctx["id"], ctx["username"], "kb.update", f"ku:{kid}", jdumps(body))
    return ok({})


@route("DELETE", r"/api/kb/(?P<kid>\d+)", "kb:delete")
def kb_delete(req: Request) -> Response:
    ctx = _require(req, "kb:delete")
    ingest.delete_ku(int(req.params["kid"]), ctx)
    return ok({})


@route("PUT", r"/api/kb/(?P<kid>\d+)/status", "kb:delete")
def kb_status(req: Request) -> Response:
    ctx = _require(req, "kb:delete")
    kid = int(req.params["kid"])
    status = req.json().get("status", "enabled")
    if status not in ("enabled", "disabled"):
        raise ApiError("非法状态")
    db.execute("UPDATE knowledge_units SET status=?, updated_at=? WHERE id=?", (status, now_iso(), kid))
    db.audit(ctx["id"], ctx["username"], "kb.status", f"ku:{kid}", status)
    return ok({})


@route("PUT", r"/api/kb/(?P<kid>\d+)/perms", "kb:perm")
def kb_perms(req: Request) -> Response:
    ctx = _require(req, "kb:perm")
    kid = int(req.params["kid"])
    body = req.json()
    permissions.set_ku_perms(
        kid,
        global_public=bool(body.get("global")),
        depts=body.get("departments") or [],
        roles=body.get("roles") or [],
        users=body.get("users") or [],
    )
    db.audit(ctx["id"], ctx["username"], "kb.perms", f"ku:{kid}",
             f"global={bool(body.get('global'))} depts={body.get('departments')} "
             f"roles={body.get('roles')} users={body.get('users')}")
    return ok({"perms": permissions.get_ku_perms(kid)})


@route("POST", r"/api/kb/(?P<kid>\d+)/reparse", "kb:update")
def kb_reparse(req: Request) -> Response:
    ctx = _require(req, "kb:update")
    n = ingest.reparse(int(req.params["kid"]), ctx)
    return ok({"chunks": n})


@route("POST", r"/api/ingest/upload", "kb:create")
def upload(req: Request) -> Response:
    ctx = _require(req, "kb:create")
    files = [f for f in req.files() if f["filename"]]
    if not files:
        raise ApiError("未接收到文件")
    category = req.form("category") or "未分类"
    results = []
    for f in files:
        try:
            ku_id, task_id = ingest.ingest_upload(f["filename"], f["data"], ctx, category)
            results.append({"filename": f["filename"], "ok": True, "ku_id": ku_id, "task_id": task_id})
        except ingest.DuplicateDoc as exc:
            results.append({"filename": f["filename"], "ok": False, "duplicate": True,
                            "ku_id": exc.ku_id, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            results.append({"filename": f["filename"], "ok": False, "message": str(exc)})
    return ok({"results": results})


@route("GET", r"/api/ingest/tasks")
def ingest_tasks(req: Request) -> Response:
    _require(req, None)
    rows = db.query("SELECT * FROM ingest_tasks ORDER BY id DESC LIMIT 30")
    return ok({"items": [dict(r) for r in rows]})


# ================================================================ AI 问答
@route("GET", r"/api/chat/sessions")
def chat_sessions(req: Request) -> Response:
    ctx = _require(req, None)
    return ok({"items": rag.list_sessions(ctx)})


@route("POST", r"/api/chat/sessions")
def chat_new_session(req: Request) -> Response:
    ctx = _require(req, None)
    sid = rag.ensure_session(ctx, None, title=req.json().get("title") or "新会话")
    return ok({"id": sid})


@route("DELETE", r"/api/chat/sessions/(?P<sid>\d+)")
def chat_del_session(req: Request) -> Response:
    ctx = _require(req, None)
    sid = int(req.params["sid"])
    db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
    db.execute("DELETE FROM sessions WHERE id=? AND user_id=?", (sid, ctx["id"]))
    return ok({})


@route("GET", r"/api/chat/sessions/(?P<sid>\d+)/messages")
def chat_messages(req: Request) -> Response:
    ctx = _require(req, None)
    return ok({"items": rag.session_messages(ctx, int(req.params["sid"]))})


@route("GET", r"/api/chat/suggestions")
def chat_suggestions(req: Request) -> Response:
    _require(req, None)
    rows = db.query("SELECT question FROM faqs WHERE status='online' ORDER BY hit_count DESC LIMIT 3")
    out = [r["question"] for r in rows]
    out += ["差旅住宿标准是多少？", "年假可以跨年结转吗？", "生鲜食品破损如何申请退款？", "新员工入职需要哪些材料？"]
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return ok({"items": uniq[:6]})


@route("POST", r"/api/chat/stream")
def chat_stream(req: Request) -> StreamResponse:
    ctx = _require(req, "ai:chat")
    body = req.json()
    question = (body.get("question") or "").strip()
    session_id = body.get("session_id")

    def gen() -> Iterator[bytes]:
        try:
            for ev in rag.answer_stream(ctx, int(session_id) if session_id else None, question):
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            err = {"type": "error", "message": f"服务异常：{exc}"}
            yield f"event: error\ndata: {json.dumps(err, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"event: end\ndata: {\"type\":\"end\"}\n\n"

    return StreamResponse(gen)


@route("POST", r"/api/chat/ask")
def chat_ask(req: Request) -> Response:
    """非流式问答接口（便于脚本与接口测试）。"""
    ctx = _require(req, "ai:chat")
    body = req.json()
    result = rag.answer_sync(ctx, body.get("session_id"), (body.get("question") or "").strip())
    return ok(result)


# ================================================================ 看板
@route("GET", r"/api/dashboard/overview")
def dashboard(req: Request) -> Response:
    ctx = _require(req, None)
    days = max(1, min(30, int(req.query.get("days", 7))))
    data = analytics.overview(days)
    data["scope"] = "平台全量" if permissions.has_perm(ctx, "menu:org") else "个人视图"
    return ok(data)


@route("GET", r"/api/system/audit")
def audit_logs(req: Request) -> Response:
    _require(req, "audit:view")
    page = max(1, int(req.query.get("page", 1)))
    return ok(analytics.audit_list(page, int(req.query.get("size", 20)), req.query.get("action")))


# ================================================================ 沉淀 / 缺口
@route("GET", r"/api/faq/candidates", "menu:faq")
def faq_candidates(req: Request) -> Response:
    _require(req, None)
    return ok({"items": faq.list_candidates(req.query.get("status") or None)})


@route("POST", r"/api/faq/mine", "faq:review")
def faq_mine(req: Request) -> Response:
    ctx = _require(req, "faq:review")
    body = req.json() or {}
    res = faq.mine(days=int(body.get("days", 30)), threshold=body.get("threshold"),
                   min_freq=int(body.get("min_freq", config.FAQ_CLUSTER_MIN_FREQ)))
    db.audit(ctx["id"], ctx["username"], "faq.mine", "", f"created={res['created']}")
    return ok(res)


@route("PUT", r"/api/faq/candidates/(?P<cid>\d+)", "faq:review")
def faq_candidate_update(req: Request) -> Response:
    _require(req, "faq:review")
    body = req.json()
    return ok(faq.update_candidate(int(req.params["cid"]), body.get("question"), body.get("answer")))


@route("POST", r"/api/faq/candidates/(?P<cid>\d+)/publish", "faq:review")
def faq_publish(req: Request) -> Response:
    ctx = _require(req, "faq:review")
    return ok(faq.publish_candidate(int(req.params["cid"]), ctx))


@route("POST", r"/api/faq/candidates/(?P<cid>\d+)/reject", "faq:review")
def faq_reject(req: Request) -> Response:
    ctx = _require(req, "faq:review")
    faq.reject_candidate(int(req.params["cid"]), ctx, (req.json() or {}).get("reason", ""))
    return ok({})


@route("GET", r"/api/faqs", "menu:faq")
def faq_list(req: Request) -> Response:
    _require(req, None)
    return ok({"items": faq.list_faqs(req.query.get("keyword", ""))})


@route("PUT", r"/api/faqs/(?P<fid>\d+)", "faq:review")
def faq_update(req: Request) -> Response:
    ctx = _require(req, "faq:review")
    return ok(faq.update_faq(int(req.params["fid"]), req.json(), ctx))


@route("DELETE", r"/api/faqs/(?P<fid>\d+)", "faq:review")
def faq_delete(req: Request) -> Response:
    ctx = _require(req, "faq:review")
    faq.delete_faq(int(req.params["fid"]), ctx)
    return ok({})


@route("GET", r"/api/gaps", "menu:faq")
def gaps(req: Request) -> Response:
    _require(req, None)
    return ok({"items": faq.list_gaps(req.query.get("status", "open"))})


@route("POST", r"/api/gaps/(?P<gid>\d+)/task", "gap:handle")
def gap_task(req: Request) -> Response:
    ctx = _require(req, "gap:handle")
    return ok(faq.gap_to_task(int(req.params["gid"]), ctx, (req.json() or {}).get("owner", "")))


@route("PUT", r"/api/gaps/(?P<gid>\d+)", "gap:handle")
def gap_update(req: Request) -> Response:
    ctx = _require(req, "gap:handle")
    faq.update_gap(int(req.params["gid"]), (req.json() or {}).get("status", "ignored"), ctx)
    return ok({})


@route("GET", r"/api/tasks", "menu:faq")
def tasks(req: Request) -> Response:
    _require(req, None)
    return ok({"items": faq.list_tasks()})


@route("PUT", r"/api/tasks/(?P<tid>\d+)", "gap:handle")
def task_update(req: Request) -> Response:
    ctx = _require(req, "gap:handle")
    faq.update_task(int(req.params["tid"]), (req.json() or {}).get("status", "done"), ctx)
    return ok({})


# ================================================================ 系统
@route("GET", r"/api/system/config")
def system_config(req: Request) -> Response:
    _require(req, None)
    cfg = llm.public_config()
    return ok({"llm": cfg, "stats": ingest.stats(), "embedding": cfg.get("embedding"),
               "reindex_hint": llm.reindex_hint(),
               "thresholds": {"cluster": llm.thresholds()["cluster"],
                              "cache": llm.thresholds()["cache"],
                              "gap": llm.thresholds()["gap"],
                              "chunk_size": config.CHUNK_SIZE,
                              "overlap": config.CHUNK_OVERLAP}})


@route("PUT", r"/api/system/config", "sys:config")
def system_config_update(req: Request) -> Response:
    ctx = _require(req, "sys:config")
    cfg = llm.save_config(req.json())
    db.audit(ctx["id"], ctx["username"], "sys.config", "llm",
             f"provider={cfg.get('provider')} model={cfg.get('model')} "
             f"embedding={cfg.get('embedding_mode')}:{cfg.get('embedding_model')}")
    return ok({"llm": llm.public_config(), "reindex_hint": llm.reindex_hint()})


@route("POST", r"/api/system/config/test", "sys:config")
def system_config_test(req: Request) -> Response:
    _require(req, "sys:config")
    if req.json():
        llm.save_config(req.json())
    return ok(llm.test_connection())


@route("POST", r"/api/system/config/test-embedding", "sys:config")
def system_config_test_embedding(req: Request) -> Response:
    _require(req, "sys:config")
    if req.json():
        llm.save_config(req.json())
    return ok(llm.test_embedding())


@route("POST", r"/api/system/reindex", "sys:config")
def system_reindex(req: Request) -> Response:
    """重建全量向量索引（切换向量引擎后调用，可重复执行）。"""
    ctx = _require(req, "sys:config")
    try:
        res = reindex.run()
    except Exception as exc:  # noqa: BLE001
        db.audit(ctx["id"], ctx["username"], "sys.reindex.fail", "embedding", str(exc)[:300])
        raise ApiError(f"重建失败：{exc}", 500, "reindex_failed") from exc
    db.audit(ctx["id"], ctx["username"], "sys.reindex", "embedding",
             f"{res['mode']}:{res['model']} {res['chunks']} chunks / dim {res['dim']}")
    return ok(res)


@route("GET", r"/api/system/health")
def health(req: Request) -> Response:
    return ok({
        "status": "ok",
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "time": now_iso(),
        "db": str(config.DB_PATH.name),
        "ku": db.scalar("SELECT COUNT(*) FROM knowledge_units"),
        "chunks": db.scalar("SELECT COUNT(*) FROM chunks"),
        "engine": "remote" if llm.is_remote() else "local",
    })


# ---------------------------------------------------------------- 分发
def dispatch(req: Request) -> Response | StreamResponse:
    # 登录态可从 query 注入（用于截图/演示免手动登录）
    if req.query.get("token") and req.headers.get("authorization") is None:
        req.headers["authorization"] = "Bearer " + req.query["token"]
    allowed_methods = set()
    for method, pattern, fn, perm in ROUTES:
        m = pattern.match(req.path)
        if m:
            allowed_methods.add(method)
            if method != req.method:
                continue
            req.params = {k: v for k, v in m.groupdict().items()}
            try:
                # 路由级功能权限：统一在分发处拦截，避免某个接口漏写 @_require
                if perm:
                    _require(req, perm)
                return fn(req)
            except ApiError as exc:
                return fail(str(exc), exc.status, exc.code)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                return fail(f"服务异常：{exc}", 500, "internal_error")
    if allowed_methods:
        return fail(f"方法不允许：{req.method}", 405, "method_not_allowed")
    return fail(f"接口不存在：{req.path}", 404, "not_found")
