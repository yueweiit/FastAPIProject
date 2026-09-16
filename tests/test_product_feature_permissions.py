import inspect
import unittest
from types import SimpleNamespace

from auth import RequireAnyRole
from routers.finance_masters import (
    create_platform_sku_mappings,
    delete_platform_sku_mapping,
    update_platform_sku_mapping,
)
from routers.products import create_product, delete_product, update_product


class ProductFeaturePermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_existing_roles_can_use_shared_product_feature_permission(self):
        for role in ("admin", "operator", "viewer"):
            user = SimpleNamespace(role=role)
            self.assertIs(await RequireAnyRole(user), user)

    def test_product_and_platform_mapping_writes_allow_all_roles(self):
        endpoints = (
            create_product,
            update_product,
            delete_product,
            create_platform_sku_mappings,
            update_platform_sku_mapping,
            delete_platform_sku_mapping,
        )
        for endpoint in endpoints:
            dependency = inspect.signature(endpoint).parameters["user"].default.dependency
            self.assertIs(dependency, RequireAnyRole)


if __name__ == "__main__":
    unittest.main()
