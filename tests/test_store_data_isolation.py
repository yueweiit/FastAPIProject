import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from routers.reports import _resolve_report_store
from routers.sales import _require_bound_store, _sale_store_scope


class StoreDataIsolationTests(unittest.IsolatedAsyncioTestCase):
    def test_non_admin_requires_bound_store_for_sensitive_data(self):
        for role in ("operator", "viewer"):
            with self.assertRaises(HTTPException) as context:
                _require_bound_store(SimpleNamespace(role=role, store_id=None))
            self.assertEqual(context.exception.status_code, 403)

    def test_non_admin_sale_scope_uses_store_not_user(self):
        scope = _sale_store_scope(
            SimpleNamespace(id=99, role="viewer", store_id=7)
        )

        self.assertIsNotNone(scope)
        self.assertIn("sales.store_id", str(scope))
        self.assertIn("users.store_id", str(scope))

    async def test_viewer_cannot_request_another_store_report(self):
        user = SimpleNamespace(role="viewer", store_id=7)

        with self.assertRaises(HTTPException) as context:
            await _resolve_report_store(None, user, 8)

        self.assertEqual(context.exception.status_code, 403)

    async def test_operator_cannot_request_another_store_report(self):
        user = SimpleNamespace(role="operator", store_id=7)

        with self.assertRaises(HTTPException) as context:
            await _resolve_report_store(None, user, 8)

        self.assertEqual(context.exception.status_code, 403)

