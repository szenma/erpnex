# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import unittest

import frappe

from erpnext.e_commerce.doctype.e_commerce_settings.test_e_commerce_settings import (
	setup_e_commerce_settings,
)
from erpnext.e_commerce.doctype.website_product.test_website_product import create_regular_web_product
from erpnext.e_commerce.product_data_engine.filters import ProductFiltersBuilder
from erpnext.e_commerce.product_data_engine.query import ProductQuery

test_dependencies = ["Product", "Product Group"]


class TestProductDataEngine(unittest.TestCase):
	"Test Products Querying and Filters for Product Listing."

	@classmethod
	def setUpClass(cls):
		product_codes = [
			("Test 11I Laptop", "Products"),  # rank 1
			("Test 12I Laptop", "Products"),  # rank 2
			("Test 13I Laptop", "Products"),  # rank 3
			("Test 14I Laptop", "Raw Material"),  # rank 4
			("Test 15I Laptop", "Raw Material"),  # rank 5
			("Test 16I Laptop", "Raw Material"),  # rank 6
			("Test 17I Laptop", "Products"),  # rank 7
		]
		for index, product in enumerate(product_codes, start=1):
			product_code = product[0]
			product_args = {"product_group": product[1]}
			web_args = {"ranking": index}
			if not frappe.db.exists("Website Product", {"product_code": product_code}):
				create_regular_web_product(product_code, product_args=product_args, web_args=web_args)

		setup_e_commerce_settings(
			{
				"products_per_page": 4,
				"enable_field_filters": 1,
				"filter_fields": [{"fieldname": "product_group"}],
				"enable_attribute_filters": 1,
				"filter_attributes": [{"attribute": "Test Size"}],
				"company": "_Test Company",
				"enabled": 1,
				"default_customer_group": "_Test Customer Group",
				"price_list": "_Test Price List India",
			}
		)
		frappe.local.shopping_cart_settings = None

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()

	def test_product_list_ordering_and_paging(self):
		"Test if website products appear by ranking on different pages."
		engine = ProductQuery()
		result = engine.query(attributes={}, fields={}, search_term=None, start=0, product_group=None)
		products = result.get("products")

		self.assertIsNotNone(products)
		self.assertEqual(len(products), 4)
		self.assertGreater(result.get("products_count"), 4)

		# check if products appear as per ranking set in setUpClass
		self.assertEqual(products[0].get("product_code"), "Test 17I Laptop")
		self.assertEqual(products[1].get("product_code"), "Test 16I Laptop")
		self.assertEqual(products[2].get("product_code"), "Test 15I Laptop")
		self.assertEqual(products[3].get("product_code"), "Test 14I Laptop")

		# check next page
		result = engine.query(attributes={}, fields={}, search_term=None, start=4, product_group=None)
		products = result.get("products")

		# check if products appear as per ranking set in setUpClass on next page
		self.assertEqual(products[0].get("product_code"), "Test 13I Laptop")
		self.assertEqual(products[1].get("product_code"), "Test 12I Laptop")
		self.assertEqual(products[2].get("product_code"), "Test 11I Laptop")

	def test_change_product_ranking(self):
		"Test if product on second page appear on first if ranking is changed."
		product_code = "Test 12I Laptop"
		old_ranking = frappe.db.get_value("Website Product", {"product_code": product_code}, "ranking")

		# low rank, appears on second page
		self.assertEqual(old_ranking, 2)

		# set ranking as highest rank
		frappe.db.set_value("Website Product", {"product_code": product_code}, "ranking", 10)

		engine = ProductQuery()
		result = engine.query(attributes={}, fields={}, search_term=None, start=0, product_group=None)
		products = result.get("products")

		# check if product is the first product on the first page
		self.assertEqual(products[0].get("product_code"), product_code)
		self.assertEqual(products[1].get("product_code"), "Test 17I Laptop")

		# tear down
		frappe.db.set_value("Website Product", {"product_code": product_code}, "ranking", old_ranking)

	def test_product_list_field_filter_builder(self):
		"Test if field filters are fetched correctly."
		frappe.db.set_value("Product Group", "Raw Material", "show_in_website", 0)

		filter_engine = ProductFiltersBuilder()
		field_filters = filter_engine.get_field_filters()

		# Web Products belonging to 'Products' and 'Raw Material' are available
		# but only 'Products' has 'show_in_website' enabled
		product_group_filters = field_filters[0]
		docfield = product_group_filters[0]
		valid_product_groups = product_group_filters[1]

		self.assertEqual(docfield.options, "Product Group")
		self.assertIn("Products", valid_product_groups)
		self.assertNotIn("Raw Material", valid_product_groups)

		frappe.db.set_value("Product Group", "Raw Material", "show_in_website", 1)
		field_filters = filter_engine.get_field_filters()

		#'Products' and 'Raw Materials' both have 'show_in_website' enabled
		product_group_filters = field_filters[0]
		docfield = product_group_filters[0]
		valid_product_groups = product_group_filters[1]

		self.assertEqual(docfield.options, "Product Group")
		self.assertIn("Products", valid_product_groups)
		self.assertIn("Raw Material", valid_product_groups)

	def test_product_list_with_field_filter(self):
		"Test if field filters are applied correctly."
		field_filters = {"product_group": "Raw Material"}

		engine = ProductQuery()
		result = engine.query(
			attributes={}, fields=field_filters, search_term=None, start=0, product_group=None
		)
		products = result.get("products")

		# check if only 'Raw Material' are fetched in the right order
		self.assertEqual(len(products), 3)
		self.assertEqual(products[0].get("product_code"), "Test 16I Laptop")
		self.assertEqual(products[1].get("product_code"), "Test 15I Laptop")

	# def test_product_list_with_field_filter_table_multiselect(self):
	# 	TODO
	# 	pass

	def test_product_list_attribute_filter_builder(self):
		"Test if attribute filters are fetched correctly."
		create_variant_web_product()

		filter_engine = ProductFiltersBuilder()
		attribute_filter = filter_engine.get_attribute_filters()[0]
		attribute_values = attribute_filter.product_attribute_values

		self.assertEqual(attribute_filter.name, "Test Size")
		self.assertGreater(len(attribute_values), 0)
		self.assertIn("Large", attribute_values)

	def test_product_list_with_attribute_filter(self):
		"Test if attribute filters are applied correctly."
		create_variant_web_product()

		attribute_filters = {"Test Size": ["Large"]}
		engine = ProductQuery()
		result = engine.query(
			attributes=attribute_filters, fields={}, search_term=None, start=0, product_group=None
		)
		products = result.get("products")

		# check if only products with Test Size 'Large' are fetched
		self.assertEqual(len(products), 1)
		self.assertEqual(products[0].get("product_code"), "Test Web Product-L")

	def test_product_list_discount_filter_builder(self):
		"Test if discount filters are fetched correctly."
		from erpnext.e_commerce.doctype.website_product.test_website_product import (
			make_web_product_price,
			make_web_pricing_rule,
		)

		product_code = "Test 12I Laptop"
		make_web_product_price(product_code=product_code)
		make_web_pricing_rule(title=f"Test Pricing Rule for {product_code}", product_code=product_code, selling=1)

		setup_e_commerce_settings({"show_price": 1})
		frappe.local.shopping_cart_settings = None

		engine = ProductQuery()
		result = engine.query(attributes={}, fields={}, search_term=None, start=4, product_group=None)
		self.assertTrue(bool(result.get("discounts")))

		filter_engine = ProductFiltersBuilder()
		discount_filters = filter_engine.get_discount_filters(result["discounts"])

		self.assertEqual(len(discount_filters[0]), 2)
		self.assertEqual(discount_filters[0][0], 10)
		self.assertEqual(discount_filters[0][1], "10% and below")

	def test_product_list_with_discount_filters(self):
		"Test if discount filters are applied correctly."
		from erpnext.e_commerce.doctype.website_product.test_website_product import (
			make_web_product_price,
			make_web_pricing_rule,
		)

		field_filters = {"discount": [10]}

		make_web_product_price(product_code="Test 12I Laptop")
		make_web_pricing_rule(
			title="Test Pricing Rule for Test 12I Laptop",  # 10% discount
			product_code="Test 12I Laptop",
			selling=1,
		)
		make_web_product_price(product_code="Test 13I Laptop")
		make_web_pricing_rule(
			title="Test Pricing Rule for Test 13I Laptop",  # 15% discount
			product_code="Test 13I Laptop",
			discount_percentage=15,
			selling=1,
		)

		setup_e_commerce_settings({"show_price": 1})
		frappe.local.shopping_cart_settings = None

		engine = ProductQuery()
		result = engine.query(
			attributes={}, fields=field_filters, search_term=None, start=0, product_group=None
		)
		products = result.get("products")

		# check if only product with 10% and below discount are fetched
		self.assertEqual(len(products), 1)
		self.assertEqual(products[0].get("product_code"), "Test 12I Laptop")

	def test_product_list_with_api(self):
		"Test products listing using API."
		from erpnext.e_commerce.api import get_product_filter_data

		create_variant_web_product()

		result = get_product_filter_data(
			query_args={
				"field_filters": {"product_group": "Products"},
				"attribute_filters": {"Test Size": ["Large"]},
				"start": 0,
			}
		)

		products = result.get("products")

		self.assertEqual(len(products), 1)
		self.assertEqual(products[0].get("product_code"), "Test Web Product-L")

	def test_product_list_with_variants(self):
		"Test if variants are hideen on hiding variants in settings."
		create_variant_web_product()

		setup_e_commerce_settings({"enable_attribute_filters": 0, "hide_variants": 1})
		frappe.local.shopping_cart_settings = None

		attribute_filters = {"Test Size": ["Large"]}
		engine = ProductQuery()
		result = engine.query(
			attributes=attribute_filters, fields={}, search_term=None, start=0, product_group=None
		)
		products = result.get("products")

		# check if any variants are fetched even though published variant exists
		self.assertEqual(len(products), 0)

		# tear down
		setup_e_commerce_settings({"enable_attribute_filters": 1, "hide_variants": 0})

	def test_custom_field_as_filter(self):
		"Test if custom field functions as filter correctly."
		from frappe.custom.doctype.custom_field.custom_field import create_custom_field

		create_custom_field(
			"Website Product",
			dict(
				owner="Administrator",
				fieldname="supplier",
				label="Supplier",
				fieldtype="Link",
				options="Supplier",
				insert_after="on_backorder",
			),
		)

		frappe.db.set_value(
			"Website Product", {"product_code": "Test 11I Laptop"}, "supplier", "_Test Supplier"
		)
		frappe.db.set_value(
			"Website Product", {"product_code": "Test 12I Laptop"}, "supplier", "_Test Supplier 1"
		)

		settings = frappe.get_doc("E Commerce Settings")
		settings.append("filter_fields", {"fieldname": "supplier"})
		settings.save()

		filter_engine = ProductFiltersBuilder()
		field_filters = filter_engine.get_field_filters()
		custom_filter = field_filters[1]
		filter_values = custom_filter[1]

		self.assertEqual(custom_filter[0].options, "Supplier")
		self.assertEqual(len(filter_values), 2)
		self.assertIn("_Test Supplier", filter_values)

		# test if custom filter works in query
		field_filters = {"supplier": "_Test Supplier 1"}
		engine = ProductQuery()
		result = engine.query(
			attributes={}, fields=field_filters, search_term=None, start=0, product_group=None
		)
		products = result.get("products")

		# check if only 'Raw Material' are fetched in the right order
		self.assertEqual(len(products), 1)
		self.assertEqual(products[0].get("product_code"), "Test 12I Laptop")


def create_variant_web_product():
	"Create Variant and Template Website Products."
	from erpnext.controllers.product_variant import create_variant
	from erpnext.e_commerce.doctype.website_product.website_product import make_website_product
	from erpnext.stock.doctype.product.test_product import make_product

	make_product(
		"Test Web Product",
		{
			"has_variant": 1,
			"variant_based_on": "Product Attribute",
			"attributes": [{"attribute": "Test Size"}],
		},
	)
	if not frappe.db.exists("Product", "Test Web Product-L"):
		variant = create_variant("Test Web Product", {"Test Size": "Large"})
		variant.save()

	if not frappe.db.exists("Website Product", {"variant_of": "Test Web Product"}):
		make_website_product(variant, save=True)
