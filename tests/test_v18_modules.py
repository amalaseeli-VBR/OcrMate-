import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from docmate_v18 import accounts, dynamics
from docmate_v18.purchase_invoices import get_supplier_parser


class AccountsTests(unittest.TestCase):
    def test_legacy_login_contract_is_preserved(self):
        self.assertTrue(accounts.validate_user_login(" USER@docmate.store ", "user"))
        self.assertTrue(accounts.validate_user_login("krishna@docmate.store", "vbr2025#"))
        self.assertFalse(accounts.validate_user_login("user@docmate.store", "wrong"))
        self.assertTrue(accounts.is_admin_user("tillsupport@visualbusinessretail.com"))


class DynamicsTests(unittest.TestCase):
    def test_sql_login_connection_string(self):
        value = dynamics.build_connection_string(
            {"server": "db", "port": "1433", "database": "DocMate", "username": "u", "password": "p"}
        )
        self.assertIn("SERVER=db,1433;", value)
        self.assertIn("DATABASE=DocMate;", value)
        self.assertIn("UID=u;PWD=p;", value)

    def test_credentials_default_server(self):
        with TemporaryDirectory() as directory:
            Path(directory, "Dynamics_db_cred.yaml").write_text("database: test\n", encoding="utf-8")
            result = dynamics.read_credentials(
                Path(directory), lambda text: {"database": "test"} if text else {}
            )
        self.assertEqual(result["server"], ".")


class PurchaseInvoiceTests(unittest.TestCase):
    def test_supplier_registry_is_case_insensitive(self):
        self.assertIsNotNone(get_supplier_parser(" booker "))
        self.assertIsNotNone(get_supplier_parser("dhamecha"))
        self.assertIsNone(get_supplier_parser("unknown"))


if __name__ == "__main__":
    unittest.main()
