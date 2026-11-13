"""初始化种子数据：组织架构、账号、示例知识库、演示日志与 FAQ。

执行时机：`python run.py --seed` 或首次启动时自动执行（data/ 为空时）。
"""
from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

from . import config, db, embed, faq, ingest, llm, permissions, rag
from .util import hash_password, jdumps, jloads, normalize_query, now_iso, tokenize

random.seed(20261112)

DEPTS = [
    ("星海数字科技", None),
    ("财务部", "星海数字科技"),
    ("人力资源部", "星海数字科技"),
    ("客服部", "星海数字科技"),
    ("信息技术部", "星海数字科技"),
    ("市场部", "星海数字科技"),
    ("销售部", "星海数字科技"),
    ("财务部 / 结算组", "财务部"),
]

ROLES = [
    ("admin", "系统管理员", "拥有全部功能与数据权限"),
    ("knowledge_admin", "知识管理员", "知识导入、权限配置、FAQ 审核与缺口处理"),
    ("management", "管理层", "查看经营与敏感经营数据"),
    ("user", "普通用户", "AI 问答与个人看板"),
]

USERS = [
    # username, name, dept, title, roles
    ("admin", "系统管理员", "信息技术部", "平台管理员", ["admin"]),
    ("kadmin", "王敏", "客服部", "知识运营专员", ["knowledge_admin"]),
    ("zhangsan", "张三", "财务部", "费用会计", ["user"]),
    ("lisi", "李四", "客服部", "客服专员", ["user"]),
    ("wangwu", "王芳", "人力资源部", "薪酬绩效主管", ["knowledge_admin"]),
    ("chenjing", "陈静", "人力资源部", "人力资源总监", ["management"]),
    ("zhaoliu", "赵六", "市场部", "市场策划", ["user"]),
    ("sunqi", "孙琪", "销售部", "渠道经理", ["user"]),
]

PUBLISHED_FAQS = [
    {
        "question": "年假可以跨年结转吗？",
        "answer": ("可以，但需在次年 3 月 31 日前休完。\n"
                   "1. 当年年假可结转至次年 3 月 31 日，逾期未休视为自动放弃；\n"
                   "2. 因公司原因导致无法休假的，经人力资源部确认后可折算为工资补偿，标准为日工资的 200%；\n"
                   "3. 调休需在 6 个月内使用完毕。\n\n来源：《员工手册（考勤与假期）》"),
        "ku_title": "员工手册（考勤与假期）",
    },
    {
        "question": "发票抬头填错可以重开吗？",
        "answer": ("可以重开，分两种情况处理：\n"
                   "1. 当月发现：联系供应商作废后重新开具正确抬头的发票；\n"
                   "2. 跨月发现：需供应商先红冲再重开，并提供情况说明，且不得超过开票日起 90 天；\n"
                   "3. 报销时发票抬头必须为公司全称并填写税号，个人抬头发票不予报销。\n\n来源：《发票开具与查验规范》"),
        "ku_title": "发票开具与查验规范",
    },
]

# 高频问题种子（用于 FAQ 聚类演示：生鲜退款 50 次问法）
FRESH_VARIANTS = [
    "生鲜食品破损如何申请退款", "生鲜食品破损了怎么申请退款", "生鲜食品破损怎么申请退款",
    "生鲜食品破损如何申请退款流程", "生鲜食品破损如何申请退款呢", "购买的生鲜食品破损如何申请退款",
    "生鲜食品破损可以申请退款吗", "生鲜食品破损怎么退款", "生鲜食品破损如何退款",
    "收到的生鲜食品破损如何申请退款", "生鲜食品破损了如何申请退款", "生鲜食品破损申请退款怎么操作",
    "生鲜食品破损如何申请退款啊", "生鲜食品破损怎样申请退款", "生鲜食品破损如何申请退货退款",
    "生鲜食品破损如何申请退款处理", "生鲜食品破损怎么申请退款呢", "生鲜食品破损如何申请全额退款",
    "生鲜食品到货破损如何申请退款", "生鲜食品破损如何申请退款需要什么材料",
    "生鲜食品破损如何申请退款多久到账", "生鲜食品破损如何申请退款客服",
    "生鲜食品有破损如何申请退款", "生鲜食品破损如何申请赔付", "生鲜食品破损了可以退款吗",
    "生鲜食品破损如何申请退款视频", "生鲜食品破损如何申请退款照片",
    "生鲜食品破损了如何申请退款呢", "生鲜食品破损如何申请退款步骤", "生鲜食品破损怎么申请退款流程",
    "生鲜食品破损如何申请退款呀", "生鲜食品破损如何申请退款吗", "生鲜食品破损如何申请退钱",
    "生鲜食品破损如何申请退款谢谢", "生鲜食品破损如何申请退款咨询", "生鲜食品破损如何申请退款求解答",
    "生鲜食品破损如何申请补偿", "生鲜食品破损如何申请售后", "生鲜食品破损如何申请售后退款",
    "生鲜食品破损怎么申请售后", "生鲜食品破损怎么申请退款补偿", "生鲜食品破损怎么申请退款流程呢",
    "生鲜食品破损可以申请售后吗", "生鲜食品破损可以退款吗", "生鲜食品破损如何退款呢",
    "生鲜食品破损如何申请退款申请", "生鲜食品破损如何申请退款操作", "生鲜食品破损如何申请退款说明",
    "生鲜食品破损如何申请退款渠道", "生鲜食品破损如何申请退款方式",
]

OTHER_QUERIES = [
    ("差旅住宿标准是多少？", 26, "global"),
    ("一线城市住宿标准是多少元", 12, "global"),
    ("年假可以跨年结转吗？", 24, "global"),
    ("年假没休完怎么办", 10, "global"),
    ("发票抬头填错可以重开吗？", 18, "global"),
    ("新员工入职需要哪些材料？", 16, "global"),
    ("入职需要体检报告吗", 8, "global"),
    ("会议室怎么预订", 9, "global"),
    ("差旅住宿超标怎么办", 14, "global"),
    ("销售提成怎么计算", 10, "denied"),
    ("高管薪酬怎么算", 7, "denied"),
    ("海外直邮保税仓清关延误怎么办", 6, "gap"),
    ("客服投诉怎么分级", 8, "global"),
    ("数据导出需要审批吗", 6, "global"),
    ("远程办公怎么申请VPN", 7, "global"),
    ("绩效考核结果什么时候公布", 6, "global"),
]


def _dept_id(name: str) -> int | None:
    row = db.query_one("SELECT id FROM depts WHERE name=?", (name,))
    return row["id"] if row else None


def init_org() -> None:
    for name, parent in DEPTS:
        if db.query_one("SELECT id FROM depts WHERE name=?", (name,)):
            continue
        db.execute("INSERT INTO depts(name,parent_id,sort,created_at) VALUES(?,?,?,?)",
                   (name, _dept_id(parent) if parent else None,
                    DEPTS.index((name, parent)), now_iso()))
    for code, name, remark in ROLES:
        row = db.query_one("SELECT id FROM roles WHERE code=?", (code,))
        if row:
            rid = row["id"]
        else:
            rid = db.execute("INSERT INTO roles(code,name,remark) VALUES(?,?,?)", (code, name, remark))
        db.execute("DELETE FROM role_perms WHERE role_id=?", (rid,))
        db.execute_many("INSERT INTO role_perms(role_id,perm) VALUES(?,?)",
                        [(rid, p) for p in permissions.ROLE_DEFAULT_PERMS.get(code, [])])
    for username, name, dept, title, roles in USERS:
        if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
            continue
        pwd, salt = hash_password("123456")
        uid = db.execute(
            "INSERT INTO users(username,name,password_hash,salt,dept_id,title,status,created_at,last_login) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (username, name, pwd, salt, _dept_id(dept), title, "active", now_iso(), now_iso()),
        )
        for code in roles:
            rid = db.query_one("SELECT id FROM roles WHERE code=?", (code,))
            if rid:
                db.execute("INSERT OR IGNORE INTO user_roles(user_id,role_id) VALUES(?,?)", (uid, rid["id"]))
    permissions.bump_version()


def import_docs() -> dict[str, int]:
    """按 manifest 导入示例文档并配置四维权限。"""
    manifest_path = config.SEED_DIR / "manifest.json"
    if not manifest_path.exists():
        print("  ! 未找到 seed/docs/manifest.json，跳过文档导入")
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ctx = {"id": db.query_one("SELECT id FROM users WHERE username='kadmin'")["id"],
           "username": "kadmin",
           "dept_id": _dept_id("客服部"),
           "dept_closure": permissions.dept_ancestors(_dept_id("客服部")),
           "role_ids": []}
    mapping: dict[str, int] = {}
    for item in manifest:
        slug = item["slug"]
        src = config.SEED_DIR / item["file"]
        if not src.exists():
            print(f"  ! 缺少文件 {item['file']}")
            continue
        dup = db.query_one("SELECT id FROM knowledge_units WHERE title=?", (item["title"],))
        if dup:
            mapping[slug] = dup["id"]
            continue
        stored = config.UPLOAD_DIR / f"seed_{src.name}"
        if not stored.exists():
            shutil.copy2(src, stored)
        try:
            ku_id = ingest.process_file(stored, src.name, ctx, category=item["category"],
                                        title=item["title"])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! 导入失败 {src.name}: {exc}")
            continue
        mapping[slug] = ku_id
        perms = item.get("perms") or {}
        depts = [_dept_id(d) for d in perms.get("departments", []) if _dept_id(d)]
        roles = [r["id"] for r in db.query("SELECT id,code FROM roles")
                 if r["code"] in (perms.get("roles") or [])]
        users = [u["id"] for u in db.query("SELECT id,username FROM users")
                 if u["username"] in (perms.get("users") or [])]
        permissions.set_ku_perms(ku_id, bool(perms.get("global")), depts, roles, users)
        # 权限配置不再是“仅创建者可见”，移除创建者的隐式个人授权，保持语义纯净
        db.execute("DELETE FROM ku_perms WHERE ku_id=? AND perm_type='user'", (ku_id,))
    permissions.bump_version()
    return mapping


def publish_faqs(mapping: dict[str, int]) -> None:
    if db.scalar("SELECT COUNT(*) FROM faqs"):
        return
    cfg = llm.get_config()
    by_title = {r["title"]: r["id"] for r in db.query("SELECT id,title FROM knowledge_units")}
    for item in PUBLISHED_FAQS:
        kid = by_title.get(item["ku_title"])
        db.execute(
            """INSERT INTO faqs(question,question_norm,answer,related_ku,status,cache_enabled,hit_count,
                vec,published_by,published_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (item["question"], normalize_query(item["question"]), item["answer"],
             jdumps([kid] if kid else []), "online", 1, 12,
             embed.pack(embed.embed(item["question"], cfg, strict=True)),
             db.query_one("SELECT id FROM users WHERE username='kadmin'")["id"], now_iso(), now_iso()),
        )


def seed_logs(mapping: dict[str, int]) -> None:
    if db.scalar("SELECT COUNT(*) FROM query_logs"):
        return
    users = [dict(r) for r in db.query(
        "SELECT u.id,u.dept_id FROM users u WHERE u.username IN ('zhangsan','lisi','zhaoliu','sunqi','kadmin')")]
    if not users:
        return
    faq_rows = db.query("SELECT id,question FROM faqs")
    faq_ids = {r["question"]: r["id"] for r in faq_rows}
    sales_ku = mapping.get("sales-commission")
    comp_ku = mapping.get("exec-compensation")
    travel_ku = mapping.get("travel-reimburse")
    fresh_ku = mapping.get("return-policy")

    # 问题 → 期望命中的知识单元（让“高频引用知识”榜单贴近真实业务分布）
    TOPIC = {
        "差旅住宿标准是多少？": ["travel-reimburse"],
        "一线城市住宿标准是多少元": ["travel-reimburse"],
        "差旅住宿超标怎么办": ["travel-reimburse"],
        "年假可以跨年结转吗？": ["employee-handbook"],
        "年假没休完怎么办": ["employee-handbook"],
        "发票抬头填错可以重开吗？": ["invoice-rules"],
        "新员工入职需要哪些材料？": ["onboarding"],
        "入职需要体检报告吗": ["onboarding"],
        "会议室怎么预订": ["meeting-room"],
        "销售提成怎么计算": ["sales-commission"],
        "高管薪酬怎么算": ["exec-compensation"],
        "客服投诉怎么分级": ["complaint-level"],
        "数据导出需要审批吗": ["data-security"],
        "远程办公怎么申请VPN": ["vpn-remote"],
        "绩效考核结果什么时候公布": ["performance-review"],
    }

    rows = []
    base = __import__("datetime").datetime.now()

    def make_ts(day_offset: int) -> str:
        import datetime as _dt

        d = base - _dt.timedelta(days=day_offset)
        h = random.randint(8, 20)
        m = random.randint(0, 59)
        s = random.randint(0, 59)
        return d.replace(hour=h, minute=m, second=s).strftime("%Y-%m-%d %H:%M:%S")

    def add(user, query, day, *, hit_count, top1, allowed, denied, cache_hit=False, faq_id=None,
            answered=True):
        qn = normalize_query(query)
        total_ms = random.randint(40, 90) if cache_hit else random.randint(900, 2800)
        first_ms = 20 if cache_hit else random.randint(300, 900)
        tin = 0 if cache_hit else int(len(query) * 2 + random.randint(500, 1400))
        tout = 0 if cache_hit else random.randint(180, 520)
        rows.append((
            None, user["id"], user["dept_id"], query, qn, round(top1, 4), hit_count,
            jdumps(allowed), jdumps(denied), 1 if cache_hit else 0, faq_id,
            tin, tout, first_ms, total_ms, 1 if answered else 0, make_ts(day),
        ))

    # 50 次生鲜退款提问（分散在 14 天内）
    for i, q in enumerate(FRESH_VARIANTS):
        u = random.choice(users)
        add(u, q, random.randint(0, 13), hit_count=3, top1=random.uniform(0.55, 0.82),
            allowed=[fresh_ku] if fresh_ku else [], denied=[])

    # 其他问题
    for q, times, kind in OTHER_QUERIES:
        topic_ids = [mapping[s] for s in TOPIC.get(q, []) if s in mapping]
        for _ in range(times):
            u = random.choice(users)
            day = random.randint(0, 13)
            if kind == "denied":
                allowed = topic_ids[:1] or ([travel_ku] if travel_ku else [])
                denied = [x for x in topic_ids[1:] if x] or ([comp_ku] if comp_ku else [])
                add(u, q, day, hit_count=2, top1=random.uniform(0.6, 0.8),
                    allowed=allowed, denied=denied)
            elif kind == "gap":
                add(u, q, day, hit_count=0, top1=random.uniform(0.28, 0.55),
                    allowed=[], denied=[], answered=False)
            else:
                cache = q in faq_ids and random.random() < 0.55
                add(u, q, day, hit_count=3, top1=random.uniform(0.62, 0.9),
                    allowed=topic_ids or ([travel_ku] if travel_ku else []), denied=[],
                    cache_hit=cache, faq_id=faq_ids.get(q) if cache else None)

    db.execute_many(
        """INSERT INTO query_logs(session_id,user_id,dept_id,query,query_norm,top1_sim,hit_count,
            allowed_ids,denied_ids,cache_hit,faq_id,token_in,token_out,first_token_ms,total_ms,
            answered,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

    # 缺口池种子
    for q, freq, sim in [("海外直邮保税仓清关延误怎么办", 6, 0.41),
                         ("企业年会在什么时间举办", 3, 0.33),
                         ("供应商付款账期是多久", 2, 0.52)]:
        db.execute(
            """INSERT OR IGNORE INTO gaps(question,question_norm,dept_id,freq,top1_sim,
                suggested_category,status,created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?)""",
            (q, normalize_query(q), _dept_id("客服部"), freq, sim, "客服政策", "open", now_iso(), now_iso()),
        )


def seed_demo_sessions() -> None:
    """用真实问答链路生成演示会话（保证引用、鉴权轨迹与审计一致）。"""
    if db.scalar("SELECT COUNT(*) FROM sessions"):
        return
    zhangsan = db.query_one("SELECT id FROM users WHERE username='zhangsan'")
    ctx = permissions.build_ctx(zhangsan["id"])
    if not ctx:
        return
    sid = None
    for q in ("差旅住宿标准是多少？", "高管薪酬怎么算？"):
        res = rag.answer_sync(ctx, sid, q)
        sid = res.get("session_id") or sid
    # 第二条演示会话：命中已发布 FAQ 缓存，展示毫秒直出
    rag.answer_sync(ctx, None, "发票抬头填错可以重开吗？")
    lisi = db.query_one("SELECT id FROM users WHERE username='lisi'")
    if lisi:
        ctx2 = permissions.build_ctx(lisi["id"])
        rag.answer_sync(ctx2, None, "生鲜食品破损如何申请退款？")
    zhaoliu = db.query_one("SELECT id FROM users WHERE username='zhaoliu'")
    if zhaoliu:
        rag.answer_sync(permissions.build_ctx(zhaoliu["id"]), None, "海外直邮保税仓清关延误怎么办？")
    # 统一标题
    for s in db.query("SELECT id FROM sessions"):
        first = db.query_one("SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY id LIMIT 1",
                             (s["id"],))
        if first:
            db.execute("UPDATE sessions SET title=? WHERE id=?", (first["content"][:24], s["id"]))


def run(force: bool = False) -> None:
    db.init_db()
    if db.kv_get("seeded") == "1" and not force:
        return
    print("== 初始化演示数据 ==")
    init_org()
    print("  · 组织架构与账号就绪（8 个账号）")
    mapping = import_docs()
    print(f"  · 导入知识文档 {len(mapping)} 篇")
    db.kv_set("embedding_signature", embed.signature(llm.get_config()))
    publish_faqs(mapping)
    print(f"  · 已发布 FAQ {db.scalar('SELECT COUNT(*) FROM faqs')} 条")
    seed_logs(mapping)
    print(f"  · 演示问答日志 {db.scalar('SELECT COUNT(*) FROM query_logs')} 条")
    res = faq.mine(days=30)
    print(f"  · 聚类挖掘：{res['clusters']} 个问题簇，新增候选 FAQ {res['created']} 条")
    seed_demo_sessions()
    print(f"  · 演示会话 {db.scalar('SELECT COUNT(*) FROM sessions')} 个")
    db.kv_set("seeded", "1")
    print("== 初始化完成 ==")


if __name__ == "__main__":
    run(force="--force" in sys.argv)
