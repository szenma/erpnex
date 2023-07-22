# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe.exceptions import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import floor

from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
from erpnext.manufacturing.report.bom_stock_report.bom_stock_report import (
	get_bom_stock as bom_stock_report,
)
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry


class TestBomStockReport(FrappeTestCase):
	def setUp(self):
		self.warehouse = "_Test Warehouse - _TC"
		self.fg_product, self.rm_products = create_products()
		make_stock_entry(target=self.warehouse, product_code=self.rm_products[0], qty=20, basic_rate=100)
		make_stock_entry(target=self.warehouse, product_code=self.rm_products[1], qty=40, basic_rate=200)
		self.bom = make_bom(product=self.fg_product, quantity=1, raw_materials=self.rm_products, rm_qty=10)

	def test_bom_stock_report(self):
		# Test 1: When `qty_to_produce` is 0.
		filters = frappe._dict(
			{
				"bom": self.bom.name,
				"warehouse": "Stores - _TC",
				"qty_to_produce": 0,
			}
		)
		self.assertRaises(ValidationError, bom_stock_report, filters)

		# Test 2: When stock is not available.
		data = bom_stock_report(
			frappe._dict(
				{
					"bom": self.bom.name,
					"warehouse": "Stores - _TC",
					"qty_to_produce": 1,
				}
			)
		)
		expected_data = get_expected_data(self.bom, "Stores - _TC", 1)
		self.assertSetEqual(set(tuple(x) for x in data), set(tuple(x) for x in expected_data))

		# Test 3: When stock is available.
		data = bom_stock_report(
			frappe._dict(
				{
					"bom": self.bom.name,
					"warehouse": self.warehouse,
					"qty_to_produce": 1,
				}
			)
		)
		expected_data = get_expected_data(self.bom, self.warehouse, 1)
		self.assertSetEqual(set(tuple(x) for x in data), set(tuple(x) for x in expected_data))


def create_products():
	fg_product = make_product(properties={"is_stock_product": 1}).name
	rm_product1 = make_product(
		properties={
			"is_stock_product": 1,
			"standard_rate": 100,
			"opening_stock": 100,
			"last_purchase_rate": 100,
		}
	).name
	rm_product2 = make_product(
		properties={
			"is_stock_product": 1,
			"standard_rate": 200,
			"opening_stock": 200,
			"last_purchase_rate": 200,
		}
	).name

	return fg_product, [rm_product1, rm_product2]


def get_expected_data(bom, warehouse, qty_to_produce, show_exploded_view=False):
	expected_data = []

	for product in bom.get("exploded_products") if show_exploded_view else bom.get("products"):
		in_stock_qty = frappe.get_cached_value(
			"Bin", {"product_code": product.product_code, "warehouse": warehouse}, "actual_qty"
		)

		expected_data.append(
			[
				product.product_code,
				product.description,
				product.stock_qty,
				product.stock_uom,
				product.stock_qty * qty_to_produce / bom.quantity,
				in_stock_qty,
				floor(in_stock_qty / (product.stock_qty * qty_to_produce / bom.quantity))
				if in_stock_qty
				else None,
			]
		)

	return expected_data
