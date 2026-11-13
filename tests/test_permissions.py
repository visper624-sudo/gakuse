"""四维数据权限鉴权引擎单元测试（对应验收第 3、4 条）。"""
from __future__ import annotations

import unittest

import helpers
from app import db, permissions


class PermissionEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helpers.reset_db()
        cls.org = helpers.make_org()
        cls.staff = helpers.staff_ctx(cls.org)
        cls.zhang = helpers.ctx_of(cls.org["zhang"])
        cls.li = helpers.ctx_of(cls.org["li"])

    def setUp(self):
        db.execute("DELETE FROM chunks")
        db.execute("DELETE FROM chunk_terms")
        db.execute("DELETE FROM ku_perms")
        db.execute("DELETE FROM knowledge_units")
        permissions.bump_version()

    def _doc(self, name, spec):
        return helpers.make_doc(name, f"{name} 内容：差旅住宿标准与报销流程说明，用于权限测试。", self.staff, spec)

    def test_default_deny(self):
        ku = self._doc("默认无权限文档", None)   # None → 保留创建者个人权限
        db.execute("DELETE FROM ku_perms")       # 手工清空，模拟“未配置任何权限实体”
        permissions.bump_version()
        self.assertFalse(permissions.can_access(self.zhang, ku))
        self.assertNotIn(ku, permissions.visible_ku_ids(self.zhang))

    def test_global_permission(self):
        ku = self._doc("全员公开文档", {"global": True})
        self.assertTrue(permissions.can_access(self.zhang, ku))
        self.assertTrue(permissions.can_access(self.li, ku))

    def test_department_permission_includes_sub_dept(self):
        ku = self._doc("部门共享文档", {"departments": [self.org["fin"]]})
        self.assertTrue(permissions.can_access(self.zhang, ku), "财务部本部门应可访问")
        self.assertTrue(permissions.can_access(self.li, ku), "子部门（结算组）应可访问")
        self.assertFalse(permissions.can_access(helpers.ctx_of(self.org["wang"]), ku), "其他部门不可访问")

    def test_role_permission(self):
        ku = self._doc("角色文档", {"roles": [self.org["role_kadmin"]]})
        self.assertTrue(permissions.can_access(helpers.staff_ctx(self.org), ku))
        self.assertFalse(permissions.can_access(self.zhang, ku))

    def test_user_permission(self):
        ku = self._doc("个人文档", {"users": [self.org["zhang"]]})
        self.assertTrue(permissions.can_access(self.zhang, ku))
        self.assertFalse(permissions.can_access(self.li, ku))

    def test_or_semantics_across_entities(self):
        ku = self._doc("混合权限文档", {
            "departments": [self.org["hr"]],
            "roles": [self.org["role_user"]],
            "users": [self.org["li"]],
        })
        self.assertTrue(permissions.can_access(self.zhang, ku), "命中角色即放行")
        self.assertTrue(permissions.can_access(self.li, ku), "命中个人即放行")

    def test_split_candidates_keeps_order(self):
        a = self._doc("A公开", {"global": True})
        b = self._doc("B受限", {"users": [self.org["wang"]]})
        allowed, denied = permissions.split_candidates(self.zhang, [a, b, a])
        self.assertEqual(allowed, [a, a])
        self.assertEqual(denied, [b])

    def test_cache_invalidation_on_change(self):
        ku = self._doc("缓存验证文档", {"users": [self.org["wang"]]})
        self.assertFalse(permissions.can_access(self.zhang, ku))
        before = permissions.perm_version()
        permissions.set_ku_perms(ku, False, depts=[self.org["fin"]], roles=[], users=[])
        self.assertGreater(permissions.perm_version(), before)
        self.assertTrue(permissions.can_access(self.zhang, ku), "权限变更后缓存应立即失效")


if __name__ == "__main__":
    unittest.main()
