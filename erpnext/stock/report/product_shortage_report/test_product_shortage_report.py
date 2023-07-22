# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.report.product_shortage_report.product_shortage_report import (
	execute as product_shortage_report,
)


class TestProductShortageReport(FrappeTestCase):
	def test_product_shortage_report(self):
		product = make_product().name
		so = make_sales_order(product_code=product)

		reserved_qty, projected_qty = frappe.db.get_value(
			"Bin",
			{
				"product_code": product,
				"warehouse": so.products[0].warehouse,
			},
			["reserved_qty", "projected_qty"],
		)
		self.assertEqual(reserved_qty, so.products[0].qty)
		self.assertEqual(projected_qty, -(so.products[0].qty))

		filters = {
			"company": so.company,
		}
		report_data = product_shortage_report(filters)[1]
		product_code_list = [row.get("product_code") for row in report_data]
		self.assertIn(product, product_code_list)

		filters = {
			"company": so.company,
			"warehouse": [so.products[0].warehouse],
		}
		report_data = product_shortage_report(filters)[1]
		product_code_list = [row.get("product_code") for row in report_data]
		self.assertIn(product, product_code_list)

		filters = {
			"company": so.company,
			"warehouse": ["Work In Progress - _TC"],
		}
		report_data = product_shortage_report(filters)[1]
		product_code_list = [row.get("product_code") for row in report_data]
		self.assertNotIn(product, product_code_list)
