import inspect
import unittest

from auth import RequireAdmin
from main import app
from routers.auth import create_user


class UserCreationPermissionTests(unittest.TestCase):
    def test_user_creation_requires_admin(self):
        dependency = inspect.signature(create_user).parameters["current_user"].default.dependency
        self.assertIs(dependency, RequireAdmin)

    def test_public_registration_route_is_removed(self):
        routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertNotIn(("POST", "/auth/register"), routes)
        self.assertIn(("POST", "/auth/users"), routes)


if __name__ == "__main__":
    unittest.main()
