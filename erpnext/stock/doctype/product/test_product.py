# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.test_runner import make_test_objects
from frappe.tests.utils import FrappeTestCase, change_settings
from frappe.utils import add_days, today

from erpnext.controllers.product_variant import (
	InvalidProductAttributeValueError,
	ProductVariantExistsError,
	create_variant,
	get_variant,
)
from erpnext.stock.doctype.product.product import (
	DataValidationError,
	InvalidBarcode,
	StockExistsForTemplate,
	get_product_attribute,
	get_timeline_data,
	get_uom_conv_factor,
	validate_is_stock_product,
)
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.get_product_details import get_product_details

test_ignore = ["BOM"]
test_dependencies = ["Warehouse", "Product Group", "Product Tax Template", "Brand", "Product Attribute"]


def make_product(product_code=None, properties=None, uoms=None):
	if not product_code:
		product_code = frappe.generate_hash(length=16)

	if frappe.db.exists("Product", product_code):
		return frappe.get_doc("Product", product_code)

	product = frappe.get_doc(
		{
			"doctype": "Product",
			"product_code": product_code,
			"product_name": product_code,
			"description": product_code,
			"product_group": "Products",
		}
	)

	if properties:
		product.update(properties)

	if product.is_stock_product:
		for product_default in [doc for doc in product.get("product_defaults") if not doc.default_warehouse]:
			product_default.default_warehouse = "_Test Warehouse - _TC"
			product_default.company = "_Test Company"

	if uoms:
		for uom in uoms:
			product.append("uoms", uom)

	product.insert()

	return product


class TestProduct(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.flags.attribute_values = None

	def get_product(self, idx):
		product_code = test_records[idx].get("product_code")
		if not frappe.db.exists("Product", product_code):
			product = frappe.copy_doc(test_records[idx])
			product.insert()
		else:
			product = frappe.get_doc("Product", product_code)
		return product

	def test_get_product_details(self):
		# delete modified product price record and make as per test_records
		frappe.db.sql("""delete from `tabProduct Price`""")
		frappe.db.sql("""delete from `tabBin`""")

		to_check = {
			"product_code": "_Test Product",
			"product_name": "_Test Product",
			"description": "_Test Product 1",
			"warehouse": "_Test Warehouse - _TC",
			"income_account": "Sales - _TC",
			"expense_account": "_Test Account Cost for Goods Sold - _TC",
			"cost_center": "_Test Cost Center - _TC",
			"qty": 1.0,
			"price_list_rate": 100.0,
			"base_price_list_rate": 0.0,
			"discount_percentage": 0.0,
			"rate": 0.0,
			"base_rate": 0.0,
			"amount": 0.0,
			"base_amount": 0.0,
			"batch_no": None,
			"uom": "_Test UOM",
			"conversion_factor": 1.0,
			"reserved_qty": 1,
			"actual_qty": 5,
			"projected_qty": 14,
		}

		make_test_objects("Product Price")
		make_test_objects(
			"Bin",
			[
				{
					"product_code": "_Test Product",
					"warehouse": "_Test Warehouse - _TC",
					"reserved_qty": 1,
					"actual_qty": 5,
					"ordered_qty": 10,
					"projected_qty": 14,
				}
			],
		)

		company = "_Test Company"
		currency = frappe.get_cached_value("Company", company, "default_currency")

		details = get_product_details(
			{
				"product_code": "_Test Product",
				"company": company,
				"price_list": "_Test Price List",
				"currency": currency,
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": currency,
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"conversion_factor": 1,
				"price_list_uom_dependant": 1,
				"ignore_pricing_rule": 1,
			}
		)

		for key, value in to_check.products():
			self.assertEqual(value, details.get(key), key)

	def test_product_tax_template(self):
		expected_product_tax_template = [
			{
				"product_code": "_Test Product With Product Tax Template",
				"tax_category": "",
				"product_tax_template": "_Test Account Excise Duty @ 10 - _TC",
			},
			{
				"product_code": "_Test Product With Product Tax Template",
				"tax_category": "_Test Tax Category 1",
				"product_tax_template": "_Test Account Excise Duty @ 12 - _TC",
			},
			{
				"product_code": "_Test Product With Product Tax Template",
				"tax_category": "_Test Tax Category 2",
				"product_tax_template": None,
			},
			{
				"product_code": "_Test Product Inherit Group Product Tax Template 1",
				"tax_category": "",
				"product_tax_template": "_Test Account Excise Duty @ 10 - _TC",
			},
			{
				"product_code": "_Test Product Inherit Group Product Tax Template 1",
				"tax_category": "_Test Tax Category 1",
				"product_tax_template": "_Test Account Excise Duty @ 12 - _TC",
			},
			{
				"product_code": "_Test Product Inherit Group Product Tax Template 1",
				"tax_category": "_Test Tax Category 2",
				"product_tax_template": None,
			},
			{
				"product_code": "_Test Product Inherit Group Product Tax Template 2",
				"tax_category": "",
				"product_tax_template": "_Test Account Excise Duty @ 15 - _TC",
			},
			{
				"product_code": "_Test Product Inherit Group Product Tax Template 2",
				"tax_category": "_Test Tax Category 1",
				"product_tax_template": "_Test Account Excise Duty @ 12 - _TC",
			},
			{
				"product_code": "_Test Product Inherit Group Product Tax Template 2",
				"tax_category": "_Test Tax Category 2",
				"product_tax_template": None,
			},
			{
				"product_code": "_Test Product Override Group Product Tax Template",
				"tax_category": "",
				"product_tax_template": "_Test Account Excise Duty @ 20 - _TC",
			},
			{
				"product_code": "_Test Product Override Group Product Tax Template",
				"tax_category": "_Test Tax Category 1",
				"product_tax_template": "_Test Product Tax Template 1 - _TC",
			},
			{
				"product_code": "_Test Product Override Group Product Tax Template",
				"tax_category": "_Test Tax Category 2",
				"product_tax_template": None,
			},
		]

		expected_product_tax_map = {
			None: {},
			"_Test Account Excise Duty @ 10 - _TC": {"_Test Account Excise Duty - _TC": 10},
			"_Test Account Excise Duty @ 12 - _TC": {"_Test Account Excise Duty - _TC": 12},
			"_Test Account Excise Duty @ 15 - _TC": {"_Test Account Excise Duty - _TC": 15},
			"_Test Account Excise Duty @ 20 - _TC": {"_Test Account Excise Duty - _TC": 20},
			"_Test Product Tax Template 1 - _TC": {
				"_Test Account Excise Duty - _TC": 5,
				"_Test Account Education Cess - _TC": 10,
				"_Test Account S&H Education Cess - _TC": 15,
			},
		}

		for data in expected_product_tax_template:
			details = get_product_details(
				{
					"product_code": data["product_code"],
					"tax_category": data["tax_category"],
					"company": "_Test Company",
					"price_list": "_Test Price List",
					"currency": "_Test Currency",
					"doctype": "Sales Order",
					"conversion_rate": 1,
					"price_list_currency": "_Test Currency",
					"plc_conversion_rate": 1,
					"order_type": "Sales",
					"customer": "_Test Customer",
					"conversion_factor": 1,
					"price_list_uom_dependant": 1,
					"ignore_pricing_rule": 1,
				}
			)

			self.assertEqual(details.product_tax_template, data["product_tax_template"])
			self.assertEqual(
				json.loads(details.product_tax_rate), expected_product_tax_map[details.product_tax_template]
			)

	def test_product_defaults(self):
		frappe.delete_doc_if_exists("Product", "Test Product With Defaults", force=1)
		make_product(
			"Test Product With Defaults",
			{
				"product_group": "_Test Product Group",
				"brand": "_Test Brand With Product Defaults",
				"product_defaults": [
					{
						"company": "_Test Company",
						"default_warehouse": "_Test Warehouse 2 - _TC",  # no override
						"expense_account": "_Test Account Stock Expenses - _TC",  # override brand default
						"buying_cost_center": "_Test Write Off Cost Center - _TC",  # override product group default
					}
				],
			},
		)

		sales_product_check = {
			"product_code": "Test Product With Defaults",
			"warehouse": "_Test Warehouse 2 - _TC",  # from product
			"income_account": "_Test Account Sales - _TC",  # from brand
			"expense_account": "_Test Account Stock Expenses - _TC",  # from product
			"cost_center": "_Test Cost Center 2 - _TC",  # from product group
		}
		sales_product_details = get_product_details(
			{
				"product_code": "Test Product With Defaults",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"customer": "_Test Customer",
			}
		)
		for key, value in sales_product_check.products():
			self.assertEqual(value, sales_product_details.get(key))

		purchase_product_check = {
			"product_code": "Test Product With Defaults",
			"warehouse": "_Test Warehouse 2 - _TC",  # from product
			"expense_account": "_Test Account Stock Expenses - _TC",  # from product
			"income_account": "_Test Account Sales - _TC",  # from brand
			"cost_center": "_Test Write Off Cost Center - _TC",  # from product
		}
		purchase_product_details = get_product_details(
			{
				"product_code": "Test Product With Defaults",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Purchase Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"supplier": "_Test Supplier",
			}
		)
		for key, value in purchase_product_check.products():
			self.assertEqual(value, purchase_product_details.get(key))

	def test_product_default_validations(self):

		with self.assertRaises(frappe.ValidationError) as ve:
			make_product(
				"Bad Product defaults",
				{
					"product_group": "_Test Product Group",
					"product_defaults": [
						{
							"company": "_Test Company 1",
							"default_warehouse": "_Test Warehouse - _TC",
							"expense_account": "Stock In Hand - _TC",
							"buying_cost_center": "_Test Cost Center - _TC",
							"selling_cost_center": "_Test Cost Center - _TC",
						}
					],
				},
			)

		self.assertTrue(
			"belong to company" in str(ve.exception).lower(),
			msg="Mismatching company entities in product defaults should not be allowed.",
		)

	def test_product_attribute_change_after_variant(self):
		frappe.delete_doc_if_exists("Product", "_Test Variant Product-L", force=1)

		variant = create_variant("_Test Variant Product", {"Test Size": "Large"})
		variant.save()

		attribute = frappe.get_doc("Product Attribute", "Test Size")
		attribute.product_attribute_values = []

		# reset flags
		frappe.flags.attribute_values = None

		self.assertRaises(InvalidProductAttributeValueError, attribute.save)
		frappe.db.rollback()

	def test_make_product_variant(self):
		frappe.delete_doc_if_exists("Product", "_Test Variant Product-L", force=1)

		variant = create_variant("_Test Variant Product", {"Test Size": "Large"})
		variant.save()

		# doing it again should raise error
		variant = create_variant("_Test Variant Product", {"Test Size": "Large"})
		variant.product_code = "_Test Variant Product-L-duplicate"
		self.assertRaises(ProductVariantExistsError, variant.save)

	def test_copy_fields_from_template_to_variants(self):
		frappe.delete_doc_if_exists("Product", "_Test Variant Product-XL", force=1)

		fields = [{"field_name": "product_group"}, {"field_name": "is_stock_product"}]
		allow_fields = [d.get("field_name") for d in fields]
		set_product_variant_settings(fields)

		if not frappe.db.get_value(
			"Product Attribute Value", {"parent": "Test Size", "attribute_value": "Extra Large"}, "name"
		):
			product_attribute = frappe.get_doc("Product Attribute", "Test Size")
			product_attribute.append("product_attribute_values", {"attribute_value": "Extra Large", "abbr": "XL"})
			product_attribute.save()

		template = frappe.get_doc("Product", "_Test Variant Product")
		template.product_group = "_Test Product Group D"
		template.save()

		variant = create_variant("_Test Variant Product", {"Test Size": "Extra Large"})
		variant.product_code = "_Test Variant Product-XL"
		variant.product_name = "_Test Variant Product-XL"
		variant.save()

		variant = frappe.get_doc("Product", "_Test Variant Product-XL")
		for fieldname in allow_fields:
			self.assertEqual(template.get(fieldname), variant.get(fieldname))

		template = frappe.get_doc("Product", "_Test Variant Product")
		template.product_group = "_Test Product Group Desktops"
		template.save()

	def test_make_product_variant_with_numeric_values(self):
		# cleanup
		for d in frappe.db.get_all("Product", filters={"variant_of": "_Test Numeric Template Product"}):
			frappe.delete_doc_if_exists("Product", d.name)

		frappe.delete_doc_if_exists("Product", "_Test Numeric Template Product")
		frappe.delete_doc_if_exists("Product Attribute", "Test Product Length")

		frappe.db.sql(
			"""delete from `tabProduct Variant Attribute`
			where attribute='Test Product Length' """
		)

		frappe.flags.attribute_values = None

		# make product attribute
		frappe.get_doc(
			{
				"doctype": "Product Attribute",
				"attribute_name": "Test Product Length",
				"numeric_values": 1,
				"from_range": 0.0,
				"to_range": 100.0,
				"increment": 0.5,
			}
		).insert()

		# make template product
		make_product(
			"_Test Numeric Template Product",
			{
				"attributes": [
					{"attribute": "Test Size"},
					{
						"attribute": "Test Product Length",
						"numeric_values": 1,
						"from_range": 0.0,
						"to_range": 100.0,
						"increment": 0.5,
					},
				],
				"product_defaults": [{"default_warehouse": "_Test Warehouse - _TC", "company": "_Test Company"}],
				"has_variants": 1,
			},
		)

		variant = create_variant(
			"_Test Numeric Template Product", {"Test Size": "Large", "Test Product Length": 1.1}
		)
		self.assertEqual(variant.product_code, "_Test Numeric Template Product-L-1.1")
		variant.product_code = "_Test Numeric Variant-L-1.1"
		variant.product_name = "_Test Numeric Variant Large 1.1m"
		self.assertRaises(InvalidProductAttributeValueError, variant.save)

		variant = create_variant(
			"_Test Numeric Template Product", {"Test Size": "Large", "Test Product Length": 1.5}
		)
		self.assertEqual(variant.product_code, "_Test Numeric Template Product-L-1.5")
		variant.product_code = "_Test Numeric Variant-L-1.5"
		variant.product_name = "_Test Numeric Variant Large 1.5m"
		variant.save()

	def test_product_merging(self):
		old = create_product(frappe.generate_hash(length=20)).name
		new = create_product(frappe.generate_hash(length=20)).name

		make_stock_entry(product_code=old, target="_Test Warehouse - _TC", qty=1, rate=100)
		make_stock_entry(product_code=old, target="_Test Warehouse 1 - _TC", qty=1, rate=100)
		make_stock_entry(product_code=new, target="_Test Warehouse 1 - _TC", qty=1, rate=100)

		frappe.rename_doc("Product", old, new, merge=True)

		self.assertFalse(frappe.db.exists("Product", old))

		self.assertTrue(
			frappe.db.get_value("Bin", {"product_code": new, "warehouse": "_Test Warehouse - _TC"})
		)
		self.assertTrue(
			frappe.db.get_value("Bin", {"product_code": new, "warehouse": "_Test Warehouse 1 - _TC"})
		)

	def test_product_merging_with_product_bundle(self):
		from erpnext.selling.doctype.product_bundle.test_product_bundle import make_product_bundle

		create_product("Test Product Bundle Product 1", is_stock_product=False)
		create_product("Test Product Bundle Product 2", is_stock_product=False)
		create_product("Test Product inside Bundle")
		bundle_products = ["Test Product inside Bundle"]

		# make bundles for both products
		bundle1 = make_product_bundle("Test Product Bundle Product 1", bundle_products, qty=2)
		make_product_bundle("Test Product Bundle Product 2", bundle_products, qty=2)

		with self.assertRaises(DataValidationError):
			frappe.rename_doc("Product", "Test Product Bundle Product 1", "Test Product Bundle Product 2", merge=True)

		bundle1.delete()
		frappe.rename_doc("Product", "Test Product Bundle Product 1", "Test Product Bundle Product 2", merge=True)

		self.assertFalse(frappe.db.exists("Product", "Test Product Bundle Product 1"))

	def test_uom_conversion_factor(self):
		if frappe.db.exists("Product", "Test Product UOM"):
			frappe.delete_doc("Product", "Test Product UOM")

		product_doc = make_product(
			"Test Product UOM", {"stock_uom": "Gram", "uoms": [dict(uom="Carat"), dict(uom="Kg")]}
		)

		for d in product_doc.uoms:
			value = get_uom_conv_factor(d.uom, product_doc.stock_uom)
			d.conversion_factor = value

		self.assertEqual(product_doc.uoms[0].uom, "Carat")
		self.assertEqual(product_doc.uoms[0].conversion_factor, 0.2)
		self.assertEqual(product_doc.uoms[1].uom, "Kg")
		self.assertEqual(product_doc.uoms[1].conversion_factor, 1000)

	def test_uom_conv_intermediate(self):
		factor = get_uom_conv_factor("Pound", "Gram")
		self.assertAlmostEqual(factor, 453.592, 3)

	def test_uom_conv_base_case(self):
		factor = get_uom_conv_factor("m", "m")
		self.assertEqual(factor, 1.0)

	def test_product_variant_by_manufacturer(self):
		fields = [{"field_name": "description"}, {"field_name": "variant_based_on"}]
		set_product_variant_settings(fields)

		if frappe.db.exists("Product", "_Test Variant Mfg"):
			frappe.delete_doc("Product", "_Test Variant Mfg")
		if frappe.db.exists("Product", "_Test Variant Mfg-1"):
			frappe.delete_doc("Product", "_Test Variant Mfg-1")
		if frappe.db.exists("Manufacturer", "MSG1"):
			frappe.delete_doc("Manufacturer", "MSG1")

		template = frappe.get_doc(
			dict(
				doctype="Product",
				product_code="_Test Variant Mfg",
				has_variant=1,
				product_group="Products",
				variant_based_on="Manufacturer",
			)
		).insert()

		manufacturer = frappe.get_doc(dict(doctype="Manufacturer", short_name="MSG1")).insert()

		variant = get_variant(template.name, manufacturer=manufacturer.name)
		self.assertEqual(variant.product_code, "_Test Variant Mfg-1")
		self.assertEqual(variant.description, "_Test Variant Mfg")
		self.assertEqual(variant.manufacturer, "MSG1")
		variant.insert()

		variant = get_variant(template.name, manufacturer=manufacturer.name, manufacturer_part_no="007")
		self.assertEqual(variant.product_code, "_Test Variant Mfg-2")
		self.assertEqual(variant.description, "_Test Variant Mfg")
		self.assertEqual(variant.manufacturer, "MSG1")
		self.assertEqual(variant.manufacturer_part_no, "007")

	def test_stock_exists_against_template_product(self):
		stock_product = frappe.get_all("Stock Ledger Entry", fields=["product_code"], limit=1)
		if stock_product:
			product_code = stock_product[0].product_code

			product_doc = frappe.get_doc("Product", product_code)
			product_doc.has_variants = 1
			self.assertRaises(StockExistsForTemplate, product_doc.save)

	def test_add_product_barcode(self):
		# Clean up
		frappe.db.sql("""delete from `tabProduct Barcode`""")
		product_code = "Test Product Barcode"
		if frappe.db.exists("Product", product_code):
			frappe.delete_doc("Product", product_code)

		# Create new product and add barcodes
		barcode_properties_list = [
			{"barcode": "0012345678905", "barcode_type": "EAN"},
			{"barcode": "012345678905", "barcode_type": "UAN"},
			{
				"barcode": "ARBITRARY_TEXT",
			},
		]
		create_product(product_code)
		for barcode_properties in barcode_properties_list:
			product_doc = frappe.get_doc("Product", product_code)
			new_barcode = product_doc.append("barcodes")
			new_barcode.update(barcode_properties)
			product_doc.save()

		# Check values saved correctly
		barcodes = frappe.get_all(
			"Product Barcode", fields=["barcode", "barcode_type"], filters={"parent": product_code}
		)

		for barcode_properties in barcode_properties_list:
			barcode_to_find = barcode_properties["barcode"]
			matching_barcodes = [x for x in barcodes if x["barcode"] == barcode_to_find]
		self.assertEqual(len(matching_barcodes), 1)
		details = matching_barcodes[0]

		for key, value in barcode_properties.products():
			self.assertEqual(value, details.get(key))

		# Add barcode again - should cause DuplicateEntryError
		product_doc = frappe.get_doc("Product", product_code)
		new_barcode = product_doc.append("barcodes")
		new_barcode.update(barcode_properties_list[0])
		self.assertRaises(frappe.UniqueValidationError, product_doc.save)

		# Add invalid barcode - should cause InvalidBarcode
		product_doc = frappe.get_doc("Product", product_code)
		new_barcode = product_doc.append("barcodes")
		new_barcode.barcode = "9999999999999"
		new_barcode.barcode_type = "EAN"
		self.assertRaises(InvalidBarcode, product_doc.save)

	def test_heatmap_data(self):
		import time

		data = get_timeline_data("Product", "_Test Product")
		self.assertTrue(isinstance(data, dict))

		now = time.time()
		one_year_ago = now - 366 * 24 * 60 * 60

		for timestamp, count in data.products():
			self.assertIsInstance(timestamp, int)
			self.assertTrue(one_year_ago <= timestamp <= now)
			self.assertIsInstance(count, int)
			self.assertTrue(count >= 0)

	def test_index_creation(self):
		"check if index is getting created in db"

		indices = frappe.db.sql("show index from tabProduct", as_dict=1)
		expected_columns = {"product_code", "product_name", "product_group"}
		for index in indices:
			expected_columns.discard(index.get("Column_name"))

		if expected_columns:
			self.fail(f"Expected db index on these columns: {', '.join(expected_columns)}")

	def test_attribute_completions(self):
		expected_attrs = {"Small", "Extra Small", "Extra Large", "Large", "2XL", "Medium"}

		attrs = get_product_attribute("Test Size")
		received_attrs = {attr.attribute_value for attr in attrs}
		self.assertEqual(received_attrs, expected_attrs)

		attrs = get_product_attribute("Test Size", attribute_value="extra")
		received_attrs = {attr.attribute_value for attr in attrs}
		self.assertEqual(received_attrs, {"Extra Small", "Extra Large"})

	def test_check_stock_uom_with_bin(self):
		# this product has opening stock and stock_uom set in test_records.
		product = frappe.get_doc("Product", "_Test Product")
		product.stock_uom = "Gram"
		self.assertRaises(frappe.ValidationError, product.save)

	def test_check_stock_uom_with_bin_no_sle(self):
		from erpnext.stock.stock_balance import update_bin_qty

		product = create_product("_Product with bin qty")
		product.stock_uom = "Gram"
		product.save()

		update_bin_qty(product.product_code, "_Test Warehouse - _TC", {"reserved_qty": 10})

		product.stock_uom = "Kilometer"
		self.assertRaises(frappe.ValidationError, product.save)

		update_bin_qty(product.product_code, "_Test Warehouse - _TC", {"reserved_qty": 0})

		product.load_from_db()
		product.stock_uom = "Kilometer"
		try:
			product.save()
		except frappe.ValidationError as e:
			self.fail(f"UoM change not allowed even though no SLE / BIN with positive qty exists: {e}")

	def test_erasure_of_old_conversions(self):
		product = create_product("_product change uom")
		product.stock_uom = "Gram"
		product.append("uoms", frappe._dict(uom="Box", conversion_factor=2))
		product.save()
		product.reload()
		product.stock_uom = "Nos"
		product.save()
		self.assertEqual(len(product.uoms), 1)

	def test_validate_stock_product(self):
		self.assertRaises(frappe.ValidationError, validate_is_stock_product, "_Test Non Stock Product")

		try:
			validate_is_stock_product("_Test Product")
		except frappe.ValidationError as e:
			self.fail(f"stock product considered non-stock product: {e}")

	@change_settings("Stock Settings", {"product_naming_by": "Naming Series"})
	def test_autoname_series(self):
		product = frappe.new_doc("Product")
		product.product_group = "All Product Groups"
		product.save()  # if product code saved without product_code then series worked

	@change_settings("Stock Settings", {"allow_negative_stock": 0})
	def test_product_wise_negative_stock(self):
		"""When global settings are disabled check that product that allows
		negative stock can still consume material in all known stock
		transactions that consume inventory."""
		from erpnext.stock.stock_ledger import is_negative_stock_allowed

		product = make_product("_TestNegativeProductSetting", {"allow_negative_stock": 1, "valuation_rate": 100})
		self.assertTrue(is_negative_stock_allowed(product_code=product.name))

		self.consume_product_code_with_differet_stock_transactions(product_code=product.name)

	@change_settings("Stock Settings", {"allow_negative_stock": 0})
	def test_backdated_negative_stock(self):
		"""same as test above but backdated entries"""
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		product = make_product("_TestNegativeProductSetting", {"allow_negative_stock": 1, "valuation_rate": 100})

		# create a future entry so all new entries are backdated
		make_stock_entry(
			qty=1, product_code=product.name, target="_Test Warehouse - _TC", posting_date=add_days(today(), 5)
		)
		self.consume_product_code_with_differet_stock_transactions(product_code=product.name)

	@change_settings("Stock Settings", {"sample_retention_warehouse": "_Test Warehouse - _TC"})
	def test_retain_sample(self):
		product = make_product(
			"_TestRetainSample", {"has_batch_no": 1, "retain_sample": 1, "sample_quantity": 1}
		)

		self.assertEqual(product.has_batch_no, 1)
		self.assertEqual(product.retain_sample, 1)
		self.assertEqual(product.sample_quantity, 1)

		product.has_batch_no = None
		product.save()
		self.assertEqual(product.retain_sample, False)
		self.assertEqual(product.sample_quantity, 0)
		product.delete()

	def consume_product_code_with_differet_stock_transactions(
		self, product_code, warehouse="_Test Warehouse - _TC"
	):
		from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
		from erpnext.stock.doctype.delivery_note.test_delivery_note import create_delivery_note
		from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		typical_args = {"product_code": product_code, "warehouse": warehouse}

		create_delivery_note(**typical_args)
		create_sales_invoice(update_stock=1, **typical_args)
		make_stock_entry(product_code=product_code, source=warehouse, qty=1, purpose="Material Issue")
		make_stock_entry(product_code=product_code, source=warehouse, target="Stores - _TC", qty=1)
		# standalone return
		make_purchase_receipt(is_return=True, qty=-1, **typical_args)

	def test_product_dashboard(self):
		from erpnext.stock.dashboard.product_dashboard import get_data

		self.assertTrue(get_data(product_code="_Test Product"))
		self.assertTrue(get_data(warehouse="_Test Warehouse - _TC"))
		self.assertTrue(get_data(product_group="All Product Groups"))

	def test_empty_description(self):
		product = make_product(properties={"description": "<p></p>"})
		self.assertEqual(product.description, product.product_name)
		product.description = ""
		product.save()
		self.assertEqual(product.description, product.product_name)

	def test_product_type_field_change(self):
		"""Check if critical fields like `is_stock_product`, `has_batch_no` are not changed if transactions exist."""
		from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
		from erpnext.stock.doctype.delivery_note.test_delivery_note import create_delivery_note
		from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		transaction_creators = [
			lambda i: make_purchase_receipt(product_code=i),
			lambda i: make_purchase_invoice(product_code=i, update_stock=1),
			lambda i: make_stock_entry(product_code=i, qty=1, target="_Test Warehouse - _TC"),
			lambda i: create_delivery_note(product_code=i),
		]

		properties = {"has_batch_no": 0, "allow_negative_stock": 1, "valuation_rate": 10}
		for transaction_creator in transaction_creators:
			product = make_product(properties=properties)
			transaction = transaction_creator(product.name)
			product.has_batch_no = 1
			self.assertRaises(frappe.ValidationError, product.save)

			transaction.cancel()
			# should be allowed now
			product.reload()
			product.has_batch_no = 1
			product.save()

	def test_customer_codes_length(self):
		"""Check if product code with special characters are allowed."""
		product = make_product(properties={"product_code": "Test Product Code With Special Characters"})
		for row in range(3):
			product.append("customer_products", {"ref_code": frappe.generate_hash("", 120)})
		product.save()
		self.assertTrue(len(product.customer_code) > 140)

	def test_update_is_stock_product(self):
		# Step - 1: Create an Product with Maintain Stock enabled
		product = make_product(properties={"is_stock_product": 1})

		# Step - 2: Disable Maintain Stock
		product.is_stock_product = 0
		product.save()
		product.reload()
		self.assertEqual(product.is_stock_product, 0)

		# Step - 3: Create Product Bundle
		pb = frappe.new_doc("Product Bundle")
		pb.new_product_code = product.name
		pb.flags.ignore_mandatory = True
		pb.save()

		# Step - 4: Try to enable Maintain Stock, should throw a validation error
		product.is_stock_product = 1
		self.assertRaises(frappe.ValidationError, product.save)
		product.reload()

		# Step - 5: Delete Product Bundle
		pb.delete()

		# Step - 6: Again try to enable Maintain Stock
		product.is_stock_product = 1
		product.save()
		product.reload()
		self.assertEqual(product.is_stock_product, 1)

	def test_serach_fields_for_product(self):
		from erpnext.controllers.queries import product_query

		make_property_setter("Product", None, "search_fields", "product_name", "Data", for_doctype="Doctype")

		product = make_product(properties={"product_name": "Test Product", "description": "Test Description"})
		data = product_query(
			"Product", "Test Product", "", 0, 20, filters={"product_name": "Test Product"}, as_dict=True
		)
		self.assertEqual(data[0].name, product.name)
		self.assertEqual(data[0].product_name, product.product_name)
		self.assertTrue("description" not in data[0])

		make_property_setter(
			"Product", None, "search_fields", "product_name, description", "Data", for_doctype="Doctype"
		)
		data = product_query(
			"Product", "Test Product", "", 0, 20, filters={"product_name": "Test Product"}, as_dict=True
		)
		self.assertEqual(data[0].name, product.name)
		self.assertEqual(data[0].product_name, product.product_name)
		self.assertEqual(data[0].description, product.description)
		self.assertTrue("description" in data[0])


def set_product_variant_settings(fields):
	doc = frappe.get_doc("Product Variant Settings")
	doc.set("fields", fields)
	doc.save()


def make_product_variant():
	if not frappe.db.exists("Product", "_Test Variant Product-S"):
		variant = create_variant("_Test Variant Product", """{"Test Size": "Small"}""")
		variant.product_code = "_Test Variant Product-S"
		variant.product_name = "_Test Variant Product-S"
		variant.save()


test_records = frappe.get_test_records("Product")


def create_product(
	product_code,
	is_stock_product=1,
	valuation_rate=0,
	stock_uom="Nos",
	warehouse="_Test Warehouse - _TC",
	is_customer_provided_product=None,
	customer=None,
	is_purchase_product=None,
	opening_stock=0,
	is_fixed_asset=0,
	asset_category=None,
	company="_Test Company",
):
	if not frappe.db.exists("Product", product_code):
		product = frappe.new_doc("Product")
		product.product_code = product_code
		product.product_name = product_code
		product.description = product_code
		product.product_group = "All Product Groups"
		product.stock_uom = stock_uom
		product.is_stock_product = is_stock_product
		product.is_fixed_asset = is_fixed_asset
		product.asset_category = asset_category
		product.opening_stock = opening_stock
		product.valuation_rate = valuation_rate
		product.is_purchase_product = is_purchase_product
		product.is_customer_provided_product = is_customer_provided_product
		product.customer = customer or ""
		product.append("product_defaults", {"default_warehouse": warehouse, "company": company})
		product.save()
	else:
		product = frappe.get_doc("Product", product_code)
	return product
