# -*- coding: utf-8 -*-
# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import unittest

import frappe

from erpnext.controllers.product_variant import create_variant
from erpnext.e_commerce.doctype.e_commerce_settings.e_commerce_settings import (
	get_shopping_cart_settings,
)
from erpnext.e_commerce.doctype.e_commerce_settings.test_e_commerce_settings import (
	setup_e_commerce_settings,
)
from erpnext.e_commerce.doctype.website_product.website_product import make_website_product
from erpnext.e_commerce.shopping_cart.product_info import get_product_info_for_website
from erpnext.stock.doctype.product.product import DataValidationError
from erpnext.stock.doctype.product.test_product import make_product

WEBPRODUCT_DESK_TESTS = ("test_website_product_desk_product_sync", "test_publish_variant_and_template")
WEBPRODUCT_PRICE_TESTS = (
	"test_website_product_price_for_logged_in_user",
	"test_website_product_price_for_guest_user",
)


class TestWebsiteProduct(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		setup_e_commerce_settings(
			{
				"company": "_Test Company",
				"enabled": 1,
				"default_customer_group": "_Test Customer Group",
				"price_list": "_Test Price List India",
			}
		)

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()

	def setUp(self):
		if self._testMethodName in WEBPRODUCT_DESK_TESTS:
			make_product(
				"Test Web Product",
				{
					"has_variant": 1,
					"variant_based_on": "Product Attribute",
					"attributes": [{"attribute": "Test Size"}],
				},
			)
		elif self._testMethodName in WEBPRODUCT_PRICE_TESTS:
			create_user_and_customer_if_not_exists(
				"test_contact_customer@example.com", "_Test Contact For _Test Customer"
			)
			create_regular_web_product()
			make_web_product_price(product_code="Test Mobile Phone")

			# Note: When testing web product pricing rule logged-in user pricing rule must differ from guest pricing rule or test will falsely pass.
			# 	  This is because make_web_pricing_rule creates a pricing rule "selling": 1, without specifying "applicable_for". Therefor,
			# 	  when testing for logged-in user the test will get the previous pricing rule because "selling" is still true.
			#
			#     I've attempted to mitigate this by setting applicable_for=Customer, and customer=Guest however, this only results in PermissionError failing the test.
			make_web_pricing_rule(
				title="Test Pricing Rule for Test Mobile Phone", product_code="Test Mobile Phone", selling=1
			)
			make_web_pricing_rule(
				title="Test Pricing Rule for Test Mobile Phone (Customer)",
				product_code="Test Mobile Phone",
				selling=1,
				discount_percentage="25",
				applicable_for="Customer",
				customer="_Test Customer",
			)

	def test_index_creation(self):
		"Check if index is getting created in db."
		from erpnext.e_commerce.doctype.website_product.website_product import on_doctype_update

		on_doctype_update()

		indices = frappe.db.sql("show index from `tabWebsite Product`", as_dict=1)
		expected_columns = {"route", "product_group", "brand"}
		for index in indices:
			expected_columns.discard(index.get("Column_name"))

		if expected_columns:
			self.fail(f"Expected db index on these columns: {', '.join(expected_columns)}")

	def test_website_product_desk_product_sync(self):
		"Check creation/updation/deletion of Website Product and its impact on Product master."
		web_product = None
		product = make_product("Test Web Product")  # will return product if exists
		try:
			web_product = make_website_product(product, save=False)
			web_product.save()
		except Exception:
			self.fail(f"Error while creating website product for {product}")

		# check if website product was created
		self.assertTrue(bool(web_product))
		self.assertTrue(bool(web_product.route))

		product.reload()
		self.assertEqual(web_product.published, 1)
		self.assertEqual(product.published_in_website, 1)  # check if product was back updated
		self.assertEqual(web_product.product_group, product.product_group)

		# check if changing product data changes it in website product
		product.product_name = "Test Web Product 1"
		product.stock_uom = "Unit"
		product.save()
		web_product.reload()
		self.assertEqual(web_product.product_name, product.product_name)
		self.assertEqual(web_product.stock_uom, product.stock_uom)

		# check if disabling product unpublished website product
		product.disabled = 1
		product.save()
		web_product.reload()
		self.assertEqual(web_product.published, 0)

		# check if website product deletion, unpublishes desk product
		web_product.delete()
		product.reload()
		self.assertEqual(product.published_in_website, 0)

		product.delete()

	def test_publish_variant_and_template(self):
		"Check if template is published on publishing variant."
		# template "Test Web Product" created on setUp
		variant = create_variant("Test Web Product", {"Test Size": "Large"})
		variant.save()

		# check if template is not published
		self.assertIsNone(frappe.db.exists("Website Product", {"product_code": variant.variant_of}))

		variant_web_product = make_website_product(variant, save=False)
		variant_web_product.save()

		# check if template is published
		try:
			template_web_product = frappe.get_doc("Website Product", {"product_code": variant.variant_of})
		except frappe.DoesNotExistError:
			self.fail(f"Template of {variant.product_code}, {variant.variant_of} not published")

		# teardown
		variant_web_product.delete()
		template_web_product.delete()
		variant.delete()

	def test_impact_on_merging_products(self):
		"Check if merging products is blocked if old and new products both have website products"
		first_product = make_product("Test First Product")
		second_product = make_product("Test Second Product")

		first_web_product = make_website_product(first_product, save=False)
		first_web_product.save()
		second_web_product = make_website_product(second_product, save=False)
		second_web_product.save()

		with self.assertRaises(DataValidationError):
			frappe.rename_doc("Product", "Test First Product", "Test Second Product", merge=True)

		# tear down
		second_web_product.delete()
		first_web_product.delete()
		second_product.delete()
		first_product.delete()

	# Website Product Portal Tests Begin

	def test_website_product_breadcrumbs(self):
		"""
		Check if breadcrumbs include homepage, product listing navigation page,
		parent product group(s) and product group
		"""
		from erpnext.setup.doctype.product_group.product_group import get_parent_product_groups

		product_code = "Test Breadcrumb Product"
		product = make_product(
			product_code,
			{
				"product_group": "_Test Product Group B - 1",
			},
		)

		if not frappe.db.exists("Website Product", {"product_code": product_code}):
			web_product = make_website_product(product, save=False)
			web_product.save()
		else:
			web_product = frappe.get_cached_doc("Website Product", {"product_code": product_code})

		frappe.db.set_value("Product Group", "_Test Product Group B - 1", "show_in_website", 1)
		frappe.db.set_value("Product Group", "_Test Product Group B", "show_in_website", 1)

		breadcrumbs = get_parent_product_groups(product.product_group)

		settings = frappe.get_cached_doc("E Commerce Settings")
		if settings.enable_field_filters:
			base_breadcrumb = "Shop by Category"
		else:
			base_breadcrumb = "All Products"

		self.assertEqual(breadcrumbs[0]["name"], "Home")
		self.assertEqual(breadcrumbs[1]["name"], base_breadcrumb)
		self.assertEqual(breadcrumbs[2]["name"], "_Test Product Group B")  # parent product group
		self.assertEqual(breadcrumbs[3]["name"], "_Test Product Group B - 1")

		# tear down
		web_product.delete()
		product.delete()

	def test_website_product_price_for_logged_in_user(self):
		"Check if price details are fetched correctly while logged in."
		product_code = "Test Mobile Phone"

		# show price in e commerce settings
		setup_e_commerce_settings({"show_price": 1})

		# price and pricing rule added via setUp

		# login as customer with pricing rule
		frappe.set_user("test_contact_customer@example.com")

		# check if price and slashed price is fetched correctly
		frappe.local.shopping_cart_settings = None
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)
		self.assertTrue(bool(data.product_info["price"]))

		price_object = data.product_info["price"]
		self.assertEqual(price_object.get("discount_percent"), 25)
		self.assertEqual(price_object.get("price_list_rate"), 750)
		self.assertEqual(price_object.get("formatted_mrp"), "₹ 1,000.00")
		self.assertEqual(price_object.get("formatted_price"), "₹ 750.00")
		self.assertEqual(price_object.get("formatted_discount_percent"), "25%")

		# switch to admin and disable show price
		frappe.set_user("Administrator")
		setup_e_commerce_settings({"show_price": 0})

		# price should not be fetched for logged in user.
		frappe.set_user("test_contact_customer@example.com")
		frappe.local.shopping_cart_settings = None
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)
		self.assertFalse(bool(data.product_info["price"]))

		# tear down
		frappe.set_user("Administrator")

	def test_website_product_price_for_guest_user(self):
		"Check if price details are fetched correctly for guest user."
		product_code = "Test Mobile Phone"

		# show price for guest user in e commerce settings
		setup_e_commerce_settings({"show_price": 1, "hide_price_for_guest": 0})

		# price and pricing rule added via setUp

		# switch to guest user
		frappe.set_user("Guest")

		# price should be fetched
		frappe.local.shopping_cart_settings = None
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)
		self.assertTrue(bool(data.product_info["price"]))

		price_object = data.product_info["price"]
		self.assertEqual(price_object.get("discount_percent"), 10)
		self.assertEqual(price_object.get("price_list_rate"), 900)

		# hide price for guest user
		frappe.set_user("Administrator")
		setup_e_commerce_settings({"hide_price_for_guest": 1})
		frappe.set_user("Guest")

		# price should not be fetched
		frappe.local.shopping_cart_settings = None
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)
		self.assertFalse(bool(data.product_info["price"]))

		# tear down
		frappe.set_user("Administrator")

	def test_website_product_stock_when_out_of_stock(self):
		"""
		Check if stock details are fetched correctly for empty inventory when:
		1) Showing stock availability enabled:
		        - Warehouse unset
		        - Warehouse set
		2) Showing stock availability disabled
		"""
		product_code = "Test Mobile Phone"
		create_regular_web_product()
		setup_e_commerce_settings({"show_stock_availability": 1})

		frappe.local.shopping_cart_settings = None
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)

		# check if stock details are fetched and product not in stock without warehouse set
		self.assertFalse(bool(data.product_info["in_stock"]))
		self.assertFalse(bool(data.product_info["stock_qty"]))

		# set warehouse
		frappe.db.set_value(
			"Website Product", {"product_code": product_code}, "website_warehouse", "_Test Warehouse - _TC"
		)

		# check if stock details are fetched and product not in stock with warehouse set
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)
		self.assertFalse(bool(data.product_info["in_stock"]))
		self.assertEqual(data.product_info["stock_qty"][0][0], 0)

		# disable show stock availability
		setup_e_commerce_settings({"show_stock_availability": 0})
		frappe.local.shopping_cart_settings = None
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)

		# check if stock detail attributes are not fetched if stock availability is hidden
		self.assertIsNone(data.product_info.get("in_stock"))
		self.assertIsNone(data.product_info.get("stock_qty"))
		self.assertIsNone(data.product_info.get("show_stock_qty"))

		# tear down
		frappe.get_cached_doc("Website Product", {"product_code": "Test Mobile Phone"}).delete()

	def test_website_product_stock_when_in_stock(self):
		"""
		Check if stock details are fetched correctly for available inventory when:
		1) Showing stock availability enabled:
		        - Warehouse set
		        - Warehouse unset
		2) Showing stock availability disabled
		"""
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		product_code = "Test Mobile Phone"
		create_regular_web_product()
		setup_e_commerce_settings({"show_stock_availability": 1})
		frappe.local.shopping_cart_settings = None

		# set warehouse
		frappe.db.set_value(
			"Website Product", {"product_code": product_code}, "website_warehouse", "_Test Warehouse - _TC"
		)

		# stock up product
		stock_entry = make_stock_entry(
			product_code=product_code, target="_Test Warehouse - _TC", qty=2, rate=100
		)

		# check if stock details are fetched and product is in stock with warehouse set
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)
		self.assertTrue(bool(data.product_info["in_stock"]))
		self.assertEqual(data.product_info["stock_qty"][0][0], 2)

		# unset warehouse
		frappe.db.set_value("Website Product", {"product_code": product_code}, "website_warehouse", "")

		# check if stock details are fetched and product not in stock without warehouse set
		# (even though it has stock in some warehouse)
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)
		self.assertFalse(bool(data.product_info["in_stock"]))
		self.assertFalse(bool(data.product_info["stock_qty"]))

		# disable show stock availability
		setup_e_commerce_settings({"show_stock_availability": 0})
		frappe.local.shopping_cart_settings = None
		data = get_product_info_for_website(product_code, skip_quotation_creation=True)

		# check if stock detail attributes are not fetched if stock availability is hidden
		self.assertIsNone(data.product_info.get("in_stock"))
		self.assertIsNone(data.product_info.get("stock_qty"))
		self.assertIsNone(data.product_info.get("show_stock_qty"))

		# tear down
		stock_entry.cancel()
		frappe.get_cached_doc("Website Product", {"product_code": "Test Mobile Phone"}).delete()

	def test_recommended_product(self):
		"Check if added recommended products are fetched correctly."
		product_code = "Test Mobile Phone"
		web_product = create_regular_web_product(product_code)

		setup_e_commerce_settings({"enable_recommendations": 1, "show_price": 1})

		# create recommended web product and price for it
		recommended_web_product = create_regular_web_product("Test Mobile Phone 1")
		make_web_product_price(product_code="Test Mobile Phone 1")

		# add recommended product to first web product
		web_product.append("recommended_products", {"website_product": recommended_web_product.name})
		web_product.save()

		frappe.local.shopping_cart_settings = None
		e_commerce_settings = get_shopping_cart_settings()
		recommended_products = web_product.get_recommended_products(e_commerce_settings)

		# test results if show price is enabled
		self.assertEqual(len(recommended_products), 1)
		recomm_product = recommended_products[0]
		self.assertEqual(recomm_product.get("website_product_name"), "Test Mobile Phone 1")
		self.assertTrue(bool(recomm_product.get("price_info")))  # price fetched

		price_info = recomm_product.get("price_info")
		self.assertEqual(price_info.get("price_list_rate"), 1000)
		self.assertEqual(price_info.get("formatted_price"), "₹ 1,000.00")

		# test results if show price is disabled
		setup_e_commerce_settings({"show_price": 0})

		frappe.local.shopping_cart_settings = None
		e_commerce_settings = get_shopping_cart_settings()
		recommended_products = web_product.get_recommended_products(e_commerce_settings)

		self.assertEqual(len(recommended_products), 1)
		self.assertFalse(bool(recommended_products[0].get("price_info")))  # price not fetched

		# tear down
		web_product.delete()
		recommended_web_product.delete()
		frappe.get_cached_doc("Product", "Test Mobile Phone 1").delete()

	def test_recommended_product_for_guest_user(self):
		"Check if added recommended products are fetched correctly for guest user."
		product_code = "Test Mobile Phone"
		web_product = create_regular_web_product(product_code)

		# price visible to guests
		setup_e_commerce_settings(
			{"enable_recommendations": 1, "show_price": 1, "hide_price_for_guest": 0}
		)

		# create recommended web product and price for it
		recommended_web_product = create_regular_web_product("Test Mobile Phone 1")
		make_web_product_price(product_code="Test Mobile Phone 1")

		# add recommended product to first web product
		web_product.append("recommended_products", {"website_product": recommended_web_product.name})
		web_product.save()

		frappe.set_user("Guest")

		frappe.local.shopping_cart_settings = None
		e_commerce_settings = get_shopping_cart_settings()
		recommended_products = web_product.get_recommended_products(e_commerce_settings)

		# test results if show price is enabled
		self.assertEqual(len(recommended_products), 1)
		self.assertTrue(bool(recommended_products[0].get("price_info")))  # price fetched

		# price hidden from guests
		frappe.set_user("Administrator")
		setup_e_commerce_settings({"hide_price_for_guest": 1})
		frappe.set_user("Guest")

		frappe.local.shopping_cart_settings = None
		e_commerce_settings = get_shopping_cart_settings()
		recommended_products = web_product.get_recommended_products(e_commerce_settings)

		# test results if show price is enabled
		self.assertEqual(len(recommended_products), 1)
		self.assertFalse(bool(recommended_products[0].get("price_info")))  # price fetched

		# tear down
		frappe.set_user("Administrator")
		web_product.delete()
		recommended_web_product.delete()
		frappe.get_cached_doc("Product", "Test Mobile Phone 1").delete()


def create_regular_web_product(product_code=None, product_args=None, web_args=None):
	"Create Regular Product and Website Product."
	product_code = product_code or "Test Mobile Phone"
	product = make_product(product_code, properties=product_args)

	if not frappe.db.exists("Website Product", {"product_code": product_code}):
		web_product = make_website_product(product, save=False)
		if web_args:
			web_product.update(web_args)
		web_product.save()
	else:
		web_product = frappe.get_cached_doc("Website Product", {"product_code": product_code})

	return web_product


def make_web_product_price(**kwargs):
	product_code = kwargs.get("product_code")
	if not product_code:
		return

	if not frappe.db.exists("Product Price", {"product_code": product_code}):
		product_price = frappe.get_doc(
			{
				"doctype": "Product Price",
				"product_code": product_code,
				"price_list": kwargs.get("price_list") or "_Test Price List India",
				"price_list_rate": kwargs.get("price_list_rate") or 1000,
			}
		)
		product_price.insert()
	else:
		product_price = frappe.get_cached_doc("Product Price", {"product_code": product_code})

	return product_price


def make_web_pricing_rule(**kwargs):
	title = kwargs.get("title")
	if not title:
		return

	if not frappe.db.exists("Pricing Rule", title):
		pricing_rule = frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": title,
				"apply_on": kwargs.get("apply_on") or "Product Code",
				"products": [{"product_code": kwargs.get("product_code")}],
				"selling": kwargs.get("selling") or 0,
				"buying": kwargs.get("buying") or 0,
				"rate_or_discount": kwargs.get("rate_or_discount") or "Discount Percentage",
				"discount_percentage": kwargs.get("discount_percentage") or 10,
				"company": kwargs.get("company") or "_Test Company",
				"currency": kwargs.get("currency") or "INR",
				"for_price_list": kwargs.get("price_list") or "_Test Price List India",
				"applicable_for": kwargs.get("applicable_for") or "",
				"customer": kwargs.get("customer") or "",
			}
		)
		pricing_rule.insert()
	else:
		pricing_rule = frappe.get_doc("Pricing Rule", {"title": title})

	return pricing_rule


def create_user_and_customer_if_not_exists(email, first_name=None):
	if frappe.db.exists("User", email):
		return

	frappe.get_doc(
		{
			"doctype": "User",
			"user_type": "Website User",
			"email": email,
			"send_welcome_email": 0,
			"first_name": first_name or email.split("@")[0],
		}
	).insert(ignore_permissions=True)

	contact = frappe.get_last_doc("Contact", filters={"email_id": email})
	link = contact.append("links", {})
	link.link_doctype = "Customer"
	link.link_name = "_Test Customer"
	link.link_title = "_Test Customer"
	contact.save()


test_dependencies = ["Price List", "Product Price", "Customer", "Contact", "Product"]
