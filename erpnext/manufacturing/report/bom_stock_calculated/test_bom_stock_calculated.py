# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.tests.utils import FrappeTestCase

from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
from erpnext.manufacturing.report.bom_stock_calculated.bom_stock_calculated import (
	execute as bom_stock_calculated_report,
)
from erpnext.stock.doctype.product.test_product import make_product


class TestBOMStockCalculated(FrappeTestCase):
	def setUp(self):
		self.fg_product, self.rm_products = create_products()
		self.boms = create_boms(self.fg_product, self.rm_products)

	def test_bom_stock_calculated(self):
		qty_to_make = 10

		# Case 1: When Product(s) Qty and Stock Qty are equal.
		data = bom_stock_calculated_report(
			filters={
				"qty_to_make": qty_to_make,
				"bom": self.boms[0].name,
			}
		)[1]
		expected_data = get_expected_data(self.boms[0], qty_to_make)
		self.assertSetEqual(set(tuple(x) for x in data), set(tuple(x) for x in expected_data))

		# Case 2: When Product(s) Qty and Stock Qty are different and BOM Qty is 1.
		data = bom_stock_calculated_report(
			filters={
				"qty_to_make": qty_to_make,
				"bom": self.boms[1].name,
			}
		)[1]
		expected_data = get_expected_data(self.boms[1], qty_to_make)
		self.assertSetEqual(set(tuple(x) for x in data), set(tuple(x) for x in expected_data))

		# Case 3: When Product(s) Qty and Stock Qty are different and BOM Qty is greater than 1.
		data = bom_stock_calculated_report(
			filters={
				"qty_to_make": qty_to_make,
				"bom": self.boms[2].name,
			}
		)[1]
		expected_data = get_expected_data(self.boms[2], qty_to_make)
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


def create_boms(fg_product, rm_products):
	def update_bom_products(bom, uom, conversion_factor):
		for product in bom.products:
			product.uom = uom
			product.conversion_factor = conversion_factor

		return bom

	bom1 = make_bom(product=fg_product, quantity=1, raw_materials=rm_products, rm_qty=10)

	bom2 = make_bom(product=fg_product, quantity=1, raw_materials=rm_products, rm_qty=10, do_not_submit=True)
	bom2 = update_bom_products(bom2, "Box", 10)
	bom2.save()
	bom2.submit()

	bom3 = make_bom(product=fg_product, quantity=2, raw_materials=rm_products, rm_qty=10, do_not_submit=True)
	bom3 = update_bom_products(bom3, "Box", 10)
	bom3.save()
	bom3.submit()

	return [bom1, bom2, bom3]


def get_expected_data(bom, qty_to_make):
	expected_data = []

	for idx in range(len(bom.products)):
		expected_data.append(
			[
				bom.products[idx].product_code,
				bom.products[idx].product_code,
				"",
				"",
				float(bom.products[idx].stock_qty / bom.quantity),
				float(100 * (idx + 1)),
				float(qty_to_make * (bom.products[idx].stock_qty / bom.quantity)),
				float((100 * (idx + 1)) - (qty_to_make * (bom.products[idx].stock_qty / bom.quantity))),
				float(100 * (idx + 1)),
			]
		)

	return expected_data
