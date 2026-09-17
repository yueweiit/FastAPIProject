import unittest

from main import app


class AccountingPeriodRemovalTests(unittest.TestCase):
    def test_accounting_period_routes_are_not_exposed(self):
        paths = {route.path for route in app.routes}
        self.assertNotIn("/finance-masters/accounting-periods", paths)
        self.assertFalse(any("accounting-periods" in path for path in paths))


if __name__ == "__main__":
    unittest.main()
