import json

import frappe
from frappe.test_runner import make_test_records
from frappe.tests.utils import FrappeTestCase

from erpnext.stock.get_product_details import get_product_details

test_ignore = ["BOM"]
test_dependencies = ["Customer", "Supplier", "Product", "Price List", "Product Price"]


class TestGetProductDetail(FrappeTestCase):
	def setUp(self):
		make_test_records("Price List")
		super().setUp()

	def test_get_product_detail_purchase_order(self):

		args = frappe._dict(
			{
				"product_code": "_Test Product",
				"company": "_Test Company",
				"customer": "_Test Customer",
				"conversion_rate": 1.0,
				"price_list_currency": "USD",
				"plc_conversion_rate": 1.0,
				"doctype": "Purchase Order",
				"name": None,
				"supplier": "_Test Supplier",
				"transaction_date": None,
				"conversion_rate": 1.0,
				"price_list": "_Test Buying Price List",
				"is_subcontracted": 0,
				"ignore_pricing_rule": 1,
				"qty": 1,
			}
		)
		details = get_product_details(args)
		self.assertEqual(details.get("price_list_rate"), 100)
