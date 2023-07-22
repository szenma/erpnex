# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.utils import _create_bin


class TestBin(FrappeTestCase):
	def test_concurrent_inserts(self):
		"""Ensure no duplicates are possible in case of concurrent inserts"""
		product_code = "_TestConcurrentBin"
		make_product(product_code)
		warehouse = "_Test Warehouse - _TC"

		bin1 = frappe.get_doc(doctype="Bin", product_code=product_code, warehouse=warehouse)
		bin1.insert()

		bin2 = frappe.get_doc(doctype="Bin", product_code=product_code, warehouse=warehouse)
		with self.assertRaises(frappe.UniqueValidationError):
			bin2.insert()

		# util method should handle it
		bin = _create_bin(product_code, warehouse)
		self.assertEqual(bin.product_code, product_code)

		frappe.db.rollback()

	def test_index_exists(self):
		indexes = frappe.db.sql("show index from tabBin where Non_unique = 0", as_dict=1)
		if not any(index.get("Key_name") == "unique_product_warehouse" for index in indexes):
			self.fail(f"Expected unique index on product-warehouse")
