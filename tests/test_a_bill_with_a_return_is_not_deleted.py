"""A bill that has a sales return is not deleted (its stock would come back twice)."""
import sqlite3
import unittest

from core import db_setup
from core import desktop_sales_service as svc


class ABillWithAReturn(unittest.TestCase):
    def test_delete_is_refused_until_the_return_goes(self):
        conn = sqlite3.connect(":memory:")
        db_setup.initialise(conn)
        conn.execute("INSERT INTO sales_returns (return_no, sale_id, refund_amount, deleted) VALUES ('SR1-1', 42, 10, 0)")
        conn.commit()
        res = svc.delete_saved_sale(conn, {"sale_id": 42})
        self.assertFalse(res["ok"])
        self.assertIn("Delete the return first", res["error"])
        conn.execute("UPDATE sales_returns SET deleted=1")
        res2 = svc.delete_saved_sale(conn, {"sale_id": 42})
        self.assertNotIn("Delete the return first", str(res2.get("error", "")))


if __name__ == "__main__":
    unittest.main()
