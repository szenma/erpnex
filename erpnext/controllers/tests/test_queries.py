import unittest
from functools import partial

import frappe

from erpnext.controllers import queries


def add_default_params(func, doctype):
	return partial(
		func, doctype=doctype, txt="", searchfield="name", start=0, page_len=20, filters=None
	)


class TestQueries(unittest.TestCase):

	# All tests are based on doctype/test_records.json

	def assert_nested_in(self, product, container):
		self.assertIn(product, [vals for tuples in container for vals in tuples])

	def test_employee_query(self):
		query = add_default_params(queries.employee_query, "Employee")

		self.assertGreaterEqual(len(query(txt="_Test Employee")), 3)
		self.assertGreaterEqual(len(query(txt="_Test Employee 1")), 1)

	def test_lead_query(self):
		query = add_default_params(queries.lead_query, "Lead")

		self.assertGreaterEqual(len(query(txt="_Test Lead")), 4)
		self.assertEqual(len(query(txt="_Test Lead 4")), 1)

	def test_customer_query(self):
		query = add_default_params(queries.customer_query, "Customer")

		self.assertGreaterEqual(len(query(txt="_Test Customer")), 7)
		self.assertGreaterEqual(len(query(txt="_Test Customer USD")), 1)

	def test_supplier_query(self):
		query = add_default_params(queries.supplier_query, "Supplier")

		self.assertGreaterEqual(len(query(txt="_Test Supplier")), 7)
		self.assertGreaterEqual(len(query(txt="_Test Supplier USD")), 1)

	def test_product_query(self):
		query = add_default_params(queries.product_query, "Product")

		self.assertGreaterEqual(len(query(txt="_Test Product")), 7)
		self.assertEqual(len(query(txt="_Test Product Home Desktop 100 3")), 1)

		fg_product = "_Test FG Product"
		stock_products = query(txt=fg_product, filters={"is_stock_product": 1})
		self.assert_nested_in("_Test FG Product", stock_products)

		bundled_stock_products = query(txt="_test product bundle product 5", filters={"is_stock_product": 1})
		self.assertEqual(len(bundled_stock_products), 0)

		# empty customer/supplier should be stripped of instead of failure
		query(txt="", filters={"customer": None})
		query(txt="", filters={"customer": ""})
		query(txt="", filters={"supplier": None})
		query(txt="", filters={"supplier": ""})

	def test_bom_qury(self):
		query = add_default_params(queries.bom, "BOM")

		self.assertGreaterEqual(len(query(txt="_Test Product Home Desktop Manufactured")), 1)

	def test_project_query(self):
		query = add_default_params(queries.get_project_name, "BOM")

		self.assertGreaterEqual(len(query(txt="_Test Project")), 1)

	def test_account_query(self):
		query = add_default_params(queries.get_account_list, "Account")

		debtor_accounts = query(txt="Debtors", filters={"company": "_Test Company"})
		self.assert_nested_in("Debtors - _TC", debtor_accounts)

	def test_income_account_query(self):
		query = add_default_params(queries.get_income_account, "Account")

		self.assertGreaterEqual(len(query(filters={"company": "_Test Company"})), 1)

	def test_expense_account_query(self):
		query = add_default_params(queries.get_expense_account, "Account")

		self.assertGreaterEqual(len(query(filters={"company": "_Test Company"})), 1)

	def test_warehouse_query(self):
		query = add_default_params(queries.warehouse_query, "Account")

		wh = query(filters=[["Bin", "product_code", "=", "_Test Product"]])
		self.assertGreaterEqual(len(wh), 1)

	def test_default_uoms(self):
		self.assertGreaterEqual(frappe.db.count("UOM", {"enabled": 1}), 10)
