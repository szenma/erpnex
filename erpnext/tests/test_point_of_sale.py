# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

import unittest

import frappe

from erpnext.accounts.doctype.pos_profile.test_pos_profile import make_pos_profile
from erpnext.selling.page.point_of_sale.point_of_sale import get_products
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry


class TestPointOfSale(unittest.TestCase):
	@classmethod
	def setUpClass(cls) -> None:
		frappe.db.savepoint("before_test_point_of_sale")

	@classmethod
	def tearDownClass(cls) -> None:
		frappe.db.rollback(save_point="before_test_point_of_sale")

	def test_product_search(self):
		"""
		Test Stock and Service Product Search.
		"""

		pos_profile = make_pos_profile(name="Test POS Profile for Search")
		product1 = make_product("Test Search Stock Product", {"is_stock_product": 1})
		make_stock_entry(
			product_code="Test Search Stock Product",
			qty=10,
			to_warehouse="_Test Warehouse - _TC",
			rate=500,
		)

		result = get_products(
			start=0,
			page_length=20,
			price_list=None,
			product_group=product1.product_group,
			pos_profile=pos_profile.name,
			search_term="Test Search Stock Product",
		)
		filtered_products = result.get("products")

		self.assertEqual(len(filtered_products), 1)
		self.assertEqual(filtered_products[0]["product_code"], product1.product_code)
		self.assertEqual(filtered_products[0]["actual_qty"], 10)

		product2 = make_product("Test Search Service Product", {"is_stock_product": 0})
		result = get_products(
			start=0,
			page_length=20,
			price_list=None,
			product_group=product2.product_group,
			pos_profile=pos_profile.name,
			search_term="Test Search Service Product",
		)
		filtered_products = result.get("products")

		self.assertEqual(len(filtered_products), 1)
		self.assertEqual(filtered_products[0]["product_code"], product2.product_code)
