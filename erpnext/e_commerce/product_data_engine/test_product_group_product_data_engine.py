# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import unittest

import frappe

from erpnext.e_commerce.api import get_product_filter_data
from erpnext.e_commerce.doctype.website_product.test_website_product import create_regular_web_product

test_dependencies = ["Product", "Product Group"]


class TestProductGroupProductDataEngine(unittest.TestCase):
	"Test Products & Sub-Category Querying for Product Listing on Product Group Page."

	def setUp(self):
		product_codes = [
			("Test Mobile A", "_Test Product Group B"),
			("Test Mobile B", "_Test Product Group B"),
			("Test Mobile C", "_Test Product Group B - 1"),
			("Test Mobile D", "_Test Product Group B - 1"),
			("Test Mobile E", "_Test Product Group B - 2"),
		]
		for product in product_codes:
			product_code = product[0]
			product_args = {"product_group": product[1]}
			if not frappe.db.exists("Website Product", {"product_code": product_code}):
				create_regular_web_product(product_code, product_args=product_args)

		frappe.db.set_value("Product Group", "_Test Product Group B - 1", "show_in_website", 1)
		frappe.db.set_value("Product Group", "_Test Product Group B - 2", "show_in_website", 1)

	def tearDown(self):
		frappe.db.rollback()

	def test_product_listing_in_product_group(self):
		"Test if only products belonging to the Product Group are fetched."
		result = get_product_filter_data(
			query_args={
				"field_filters": {},
				"attribute_filters": {},
				"start": 0,
				"product_group": "_Test Product Group B",
			}
		)

		products = result.get("products")
		product_codes = [product.get("product_code") for product in products]

		self.assertEqual(len(products), 2)
		self.assertIn("Test Mobile A", product_codes)
		self.assertNotIn("Test Mobile C", product_codes)

	def test_products_in_multiple_product_groups(self):
		"""Test if product is visible on multiple product group pages barring its own."""
		website_product = frappe.get_doc("Website Product", {"product_code": "Test Mobile E"})

		# show product belonging to '_Test Product Group B - 2' in '_Test Product Group B - 1' as well
		website_product.append("website_product_groups", {"product_group": "_Test Product Group B - 1"})
		website_product.save()

		result = get_product_filter_data(
			query_args={
				"field_filters": {},
				"attribute_filters": {},
				"start": 0,
				"product_group": "_Test Product Group B - 1",
			}
		)

		products = result.get("products")
		product_codes = [product.get("product_code") for product in products]

		self.assertEqual(len(products), 3)
		self.assertIn("Test Mobile E", product_codes)  # visible in other product groups
		self.assertIn("Test Mobile C", product_codes)
		self.assertIn("Test Mobile D", product_codes)

		result = get_product_filter_data(
			query_args={
				"field_filters": {},
				"attribute_filters": {},
				"start": 0,
				"product_group": "_Test Product Group B - 2",
			}
		)

		products = result.get("products")

		self.assertEqual(len(products), 1)
		self.assertEqual(products[0].get("product_code"), "Test Mobile E")  # visible in own product group

	def test_product_group_with_sub_groups(self):
		"Test Valid Sub Product Groups in Product Group Page."
		frappe.db.set_value("Product Group", "_Test Product Group B - 2", "show_in_website", 0)

		result = get_product_filter_data(
			query_args={
				"field_filters": {},
				"attribute_filters": {},
				"start": 0,
				"product_group": "_Test Product Group B",
			}
		)

		self.assertTrue(bool(result.get("sub_categories")))

		child_groups = [d.name for d in result.get("sub_categories")]
		# check if child group is fetched if shown in website
		self.assertIn("_Test Product Group B - 1", child_groups)

		frappe.db.set_value("Product Group", "_Test Product Group B - 2", "show_in_website", 1)
		result = get_product_filter_data(
			query_args={
				"field_filters": {},
				"attribute_filters": {},
				"start": 0,
				"product_group": "_Test Product Group B",
			}
		)
		child_groups = [d.name for d in result.get("sub_categories")]

		# check if child group is fetched if shown in website
		self.assertIn("_Test Product Group B - 1", child_groups)
		self.assertIn("_Test Product Group B - 2", child_groups)

	def test_product_group_page_with_descendants_included(self):
		"""
		Test if 'include_descendants' pulls Products belonging to descendant Product Groups (Level 2 & 3).
		> _Test Product Group B [Level 1]
		        > _Test Product Group B - 1 [Level 2]
		                > _Test Product Group B - 1 - 1 [Level 3]
		"""
		frappe.get_doc(
			{  # create Level 3 nested child group
				"doctype": "Product Group",
				"is_group": 1,
				"product_group_name": "_Test Product Group B - 1 - 1",
				"parent_product_group": "_Test Product Group B - 1",
			}
		).insert()

		create_regular_web_product(  # create an product belonging to level 3 product group
			"Test Mobile F", product_args={"product_group": "_Test Product Group B - 1 - 1"}
		)

		frappe.db.set_value("Product Group", "_Test Product Group B - 1 - 1", "show_in_website", 1)

		# enable 'include descendants' in Level 1
		frappe.db.set_value("Product Group", "_Test Product Group B", "include_descendants", 1)

		result = get_product_filter_data(
			query_args={
				"field_filters": {},
				"attribute_filters": {},
				"start": 0,
				"product_group": "_Test Product Group B",
			}
		)

		products = result.get("products")
		product_codes = [product.get("product_code") for product in products]

		# check if all sub groups' products are pulled
		self.assertEqual(len(products), 6)
		self.assertIn("Test Mobile A", product_codes)
		self.assertIn("Test Mobile C", product_codes)
		self.assertIn("Test Mobile E", product_codes)
		self.assertIn("Test Mobile F", product_codes)
