# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import unittest

import frappe

from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.get_product_details import get_product_details


class TestPricingRule(unittest.TestCase):
	def setUp(self):
		delete_existing_pricing_rules()
		setup_pricing_rule_data()

	def tearDown(self):
		delete_existing_pricing_rules()

	def test_pricing_rule_for_discount(self):
		from frappe import MandatoryError

		from erpnext.stock.get_product_details import get_product_details

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Product Code",
			"products": [{"product_code": "_Test Product"}],
			"currency": "USD",
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"discount_percentage": 10,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		args = frappe._dict(
			{
				"product_code": "_Test Product",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
			}
		)
		details = get_product_details(args)
		self.assertEqual(details.get("discount_percentage"), 10)

		prule = frappe.get_doc(test_record.copy())
		prule.priority = 1
		prule.applicable_for = "Customer"
		prule.title = "_Test Pricing Rule for Customer"
		self.assertRaises(MandatoryError, prule.insert)

		prule.customer = "_Test Customer"
		prule.discount_percentage = 20
		prule.insert()
		details = get_product_details(args)
		self.assertEqual(details.get("discount_percentage"), 20)

		prule = frappe.get_doc(test_record.copy())
		prule.apply_on = "Product Group"
		prule.products = []
		prule.append("product_groups", {"product_group": "All Product Groups"})
		prule.title = "_Test Pricing Rule for Product Group"
		prule.discount_percentage = 15
		prule.insert()

		args.customer = "_Test Customer 1"
		details = get_product_details(args)
		self.assertEqual(details.get("discount_percentage"), 10)

		prule = frappe.get_doc(test_record.copy())
		prule.applicable_for = "Campaign"
		prule.campaign = "_Test Campaign"
		prule.title = "_Test Pricing Rule for Campaign"
		prule.discount_percentage = 5
		prule.priority = 8
		prule.insert()

		args.campaign = "_Test Campaign"
		details = get_product_details(args)
		self.assertEqual(details.get("discount_percentage"), 5)

		frappe.db.sql("update `tabPricing Rule` set priority=NULL where campaign='_Test Campaign'")
		from erpnext.accounts.doctype.pricing_rule.utils import MultiplePricingRuleConflict

		self.assertRaises(MultiplePricingRuleConflict, get_product_details, args)

		args.product_code = "_Test Product 2"
		details = get_product_details(args)
		self.assertEqual(details.get("discount_percentage"), 15)

	def test_pricing_rule_for_margin(self):
		from frappe import MandatoryError

		from erpnext.stock.get_product_details import get_product_details

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Product Code",
			"products": [
				{
					"product_code": "_Test FG Product 2",
				}
			],
			"selling": 1,
			"currency": "USD",
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"margin_type": "Percentage",
			"margin_rate_or_amount": 10,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		product_price = frappe.get_doc(
			{
				"doctype": "Product Price",
				"price_list": "_Test Price List 2",
				"product_code": "_Test FG Product 2",
				"price_list_rate": 100,
			}
		)

		product_price.insert(ignore_permissions=True)

		args = frappe._dict(
			{
				"product_code": "_Test FG Product 2",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
			}
		)
		details = get_product_details(args)
		self.assertEqual(details.get("margin_type"), "Percentage")
		self.assertEqual(details.get("margin_rate_or_amount"), 10)

	def test_mixed_conditions_for_product_group(self):
		for product in ["Mixed Cond Product 1", "Mixed Cond Product 2"]:
			make_product(product, {"product_group": "Products"})
			make_product_price(product, "_Test Price List", 100)

		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule for Product Group",
			"apply_on": "Product Group",
			"product_groups": [
				{
					"product_group": "Products",
				},
				{
					"product_group": "_Test Product Group",
				},
			],
			"selling": 1,
			"mixed_conditions": 1,
			"currency": "USD",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 10,
			"applicable_for": "Customer Group",
			"customer_group": "All Customer Groups",
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		args = frappe._dict(
			{
				"product_code": "Mixed Cond Product 1",
				"product_group": "Products",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"customer_group": "_Test Customer Group",
				"name": None,
			}
		)
		details = get_product_details(args)
		self.assertEqual(details.get("discount_percentage"), 10)

	def test_pricing_rule_for_variants(self):
		from frappe import MandatoryError

		from erpnext.stock.get_product_details import get_product_details

		if not frappe.db.exists("Product", "Test Variant PRT"):
			frappe.get_doc(
				{
					"doctype": "Product",
					"product_code": "Test Variant PRT",
					"product_name": "Test Variant PRT",
					"description": "Test Variant PRT",
					"product_group": "_Test Product Group",
					"is_stock_product": 1,
					"variant_of": "_Test Variant Product",
					"default_warehouse": "_Test Warehouse - _TC",
					"stock_uom": "_Test UOM",
					"attributes": [{"attribute": "Test Size", "attribute_value": "Medium"}],
				}
			).insert()

		frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": "_Test Pricing Rule 1",
				"apply_on": "Product Code",
				"currency": "USD",
				"products": [
					{
						"product_code": "_Test Variant Product",
					}
				],
				"selling": 1,
				"rate_or_discount": "Discount Percentage",
				"rate": 0,
				"discount_percentage": 7.5,
				"company": "_Test Company",
			}
		).insert()

		args = frappe._dict(
			{
				"product_code": "Test Variant PRT",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Order",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
			}
		)

		details = get_product_details(args)
		self.assertEqual(details.get("discount_percentage"), 7.5)

		# add a new pricing rule for that product code, it should take priority
		frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": "_Test Pricing Rule 2",
				"apply_on": "Product Code",
				"products": [
					{
						"product_code": "Test Variant PRT",
					}
				],
				"currency": "USD",
				"selling": 1,
				"rate_or_discount": "Discount Percentage",
				"rate": 0,
				"discount_percentage": 17.5,
				"priority": 1,
				"company": "_Test Company",
			}
		).insert()

		details = get_product_details(args)
		self.assertEqual(details.get("discount_percentage"), 17.5)

	def test_pricing_rule_for_stock_qty(self):
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Product Code",
			"currency": "USD",
			"products": [
				{
					"product_code": "_Test Product",
				}
			],
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_qty": 5,
			"max_qty": 7,
			"discount_percentage": 17.5,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		if not frappe.db.get_value("UOM Conversion Detail", {"parent": "_Test Product", "uom": "box"}):
			product = frappe.get_doc("Product", "_Test Product")
			product.append("uoms", {"uom": "Box", "conversion_factor": 5})
			product.save(ignore_permissions=True)

		# With pricing rule
		so = make_sales_order(product_code="_Test Product", qty=1, uom="Box", do_not_submit=True)
		so.products[0].price_list_rate = 100
		so.submit()
		so = frappe.get_doc("Sales Order", so.name)
		self.assertEqual(so.products[0].discount_percentage, 17.5)
		self.assertEqual(so.products[0].rate, 82.5)

		# Without pricing rule
		so = make_sales_order(product_code="_Test Product", qty=2, uom="Box", do_not_submit=True)
		so.products[0].price_list_rate = 100
		so.submit()
		so = frappe.get_doc("Sales Order", so.name)
		self.assertEqual(so.products[0].discount_percentage, 0)
		self.assertEqual(so.products[0].rate, 100)

	def test_pricing_rule_with_margin_and_discount(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		make_pricing_rule(
			selling=1, margin_type="Percentage", margin_rate_or_amount=10, discount_percentage=10
		)
		si = create_sales_invoice(do_not_save=True)
		si.products[0].price_list_rate = 1000
		si.payment_schedule = []
		si.insert(ignore_permissions=True)

		product = si.products[0]
		self.assertEqual(product.margin_rate_or_amount, 10)
		self.assertEqual(product.rate_with_margin, 1100)
		self.assertEqual(product.discount_percentage, 10)
		self.assertEqual(product.discount_amount, 110)
		self.assertEqual(product.rate, 990)

	def test_pricing_rule_with_margin_and_discount_amount(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		make_pricing_rule(
			selling=1,
			margin_type="Percentage",
			margin_rate_or_amount=10,
			rate_or_discount="Discount Amount",
			discount_amount=110,
		)
		si = create_sales_invoice(do_not_save=True)
		si.products[0].price_list_rate = 1000
		si.payment_schedule = []
		si.insert(ignore_permissions=True)

		product = si.products[0]
		self.assertEqual(product.margin_rate_or_amount, 10)
		self.assertEqual(product.rate_with_margin, 1100)
		self.assertEqual(product.discount_amount, 110)
		self.assertEqual(product.rate, 990)

	def test_pricing_rule_for_product_discount_on_same_product(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Product Code",
			"currency": "USD",
			"products": [
				{
					"product_code": "_Test Product",
				}
			],
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_qty": 0,
			"max_qty": 7,
			"discount_percentage": 17.5,
			"price_or_product_discount": "Product",
			"same_product": 1,
			"free_qty": 1,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		# With pricing rule
		so = make_sales_order(product_code="_Test Product", qty=1)
		so.load_from_db()
		self.assertEqual(so.products[1].is_free_product, 1)
		self.assertEqual(so.products[1].product_code, "_Test Product")

	def test_pricing_rule_for_product_discount_on_different_product(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Product Code",
			"currency": "USD",
			"products": [
				{
					"product_code": "_Test Product",
				}
			],
			"selling": 1,
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_qty": 0,
			"max_qty": 7,
			"discount_percentage": 17.5,
			"price_or_product_discount": "Product",
			"same_product": 0,
			"free_product": "_Test Product 2",
			"free_qty": 1,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		# With pricing rule
		so = make_sales_order(product_code="_Test Product", qty=1)
		so.load_from_db()
		self.assertEqual(so.products[1].is_free_product, 1)
		self.assertEqual(so.products[1].product_code, "_Test Product 2")

	def test_cumulative_pricing_rule(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Cumulative Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Cumulative Pricing Rule",
			"apply_on": "Product Code",
			"currency": "USD",
			"products": [
				{
					"product_code": "_Test Product",
				}
			],
			"is_cumulative": 1,
			"selling": 1,
			"applicable_for": "Customer",
			"customer": "_Test Customer",
			"rate_or_discount": "Discount Percentage",
			"rate": 0,
			"min_amt": 0,
			"max_amt": 10000,
			"discount_percentage": 17.5,
			"price_or_product_discount": "Price",
			"company": "_Test Company",
			"valid_from": frappe.utils.nowdate(),
			"valid_upto": frappe.utils.nowdate(),
		}
		frappe.get_doc(test_record.copy()).insert()

		args = frappe._dict(
			{
				"product_code": "_Test Product",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "_Test Currency",
				"doctype": "Sales Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
				"transaction_date": frappe.utils.nowdate(),
			}
		)
		details = get_product_details(args)

		self.assertTrue(details)

	def test_pricing_rule_for_condition(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")

		make_pricing_rule(
			selling=1,
			margin_type="Percentage",
			condition="customer=='_Test Customer 1' and is_return==0",
			discount_percentage=10,
		)

		# Incorrect Customer and Correct is_return value
		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 2", is_return=0)
		si.products[0].price_list_rate = 1000
		si.submit()
		product = si.products[0]
		self.assertEqual(product.rate, 100)

		# Correct Customer and Incorrect is_return value
		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", is_return=1, qty=-1)
		si.products[0].price_list_rate = 1000
		si.submit()
		product = si.products[0]
		self.assertEqual(product.rate, 100)

		# Correct Customer and correct is_return value
		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", is_return=0)
		si.products[0].price_list_rate = 1000
		si.submit()
		product = si.products[0]
		self.assertEqual(product.rate, 900)

	def test_multiple_pricing_rules(self):
		make_pricing_rule(
			discount_percentage=20,
			selling=1,
			priority=1,
			apply_multiple_pricing_rules=1,
			title="_Test Pricing Rule 1",
		)
		make_pricing_rule(
			discount_percentage=10,
			selling=1,
			title="_Test Pricing Rule 2",
			priority=2,
			apply_multiple_pricing_rules=1,
		)
		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", qty=1)
		self.assertEqual(si.products[0].discount_percentage, 30)
		si.delete()

		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 2")

	def test_multiple_pricing_rules_with_apply_discount_on_discounted_rate(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")

		make_pricing_rule(
			discount_percentage=20,
			selling=1,
			priority=1,
			apply_multiple_pricing_rules=1,
			title="_Test Pricing Rule 1",
		)
		make_pricing_rule(
			discount_percentage=10,
			selling=1,
			priority=2,
			apply_discount_on_rate=1,
			title="_Test Pricing Rule 2",
			apply_multiple_pricing_rules=1,
		)

		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", qty=1)
		self.assertEqual(si.products[0].discount_percentage, 28)
		si.delete()

		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule 2")

	def test_product_price_with_pricing_rule(self):
		product = make_product("Water Flask")
		make_product_price("Water Flask", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Water Flask Rule",
			"apply_on": "Product Code",
			"products": [
				{
					"product_code": "Water Flask",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 0,
			"margin_type": "Percentage",
			"margin_rate_or_amount": 2,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(do_not_save=True, product_code="Water Flask")
		si.selling_price_list = "_Test Price List"
		si.save()

		# If rate in Rule is 0, give preference to Product Price if it exists
		self.assertEqual(si.products[0].price_list_rate, 100)
		self.assertEqual(si.products[0].margin_rate_or_amount, 2)
		self.assertEqual(si.products[0].rate_with_margin, 102)
		self.assertEqual(si.products[0].rate, 102)

		si.delete()
		rule.delete()
		frappe.get_doc("Product Price", {"product_code": "Water Flask"}).delete()
		product.delete()

	def test_product_price_with_blank_uom_pricing_rule(self):
		properties = {
			"product_code": "Product Blank UOM",
			"stock_uom": "Nos",
			"sales_uom": "Box",
			"uoms": [dict(uom="Box", conversion_factor=10)],
		}
		product = make_product(properties=properties)

		make_product_price("Product Blank UOM", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Product Blank UOM Rule",
			"apply_on": "Product Code",
			"products": [
				{
					"product_code": "Product Blank UOM",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 101,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(
			do_not_save=True, product_code="Product Blank UOM", uom="Box", conversion_factor=10
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		# If UOM is blank consider it as stock UOM and apply pricing_rule on all UOM.
		# rate is 101, Selling UOM is Box that have conversion_factor of 10 so 101 * 10 = 1010
		self.assertEqual(si.products[0].price_list_rate, 1010)
		self.assertEqual(si.products[0].rate, 1010)

		si.delete()

		si = create_sales_invoice(do_not_save=True, product_code="Product Blank UOM", uom="Nos")
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is blank so consider it as stock UOM and apply pricing_rule on all UOM.
		# rate is 101, Selling UOM is Nos that have conversion_factor of 1 so 101 * 1 = 101
		self.assertEqual(si.products[0].price_list_rate, 101)
		self.assertEqual(si.products[0].rate, 101)

		si.delete()
		rule.delete()
		frappe.get_doc("Product Price", {"product_code": "Product Blank UOM"}).delete()

		product.delete()

	def test_product_price_with_selling_uom_pricing_rule(self):
		properties = {
			"product_code": "Product UOM other than Stock",
			"stock_uom": "Nos",
			"sales_uom": "Box",
			"uoms": [dict(uom="Box", conversion_factor=10)],
		}
		product = make_product(properties=properties)

		make_product_price("Product UOM other than Stock", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Product UOM other than Stock Rule",
			"apply_on": "Product Code",
			"products": [
				{
					"product_code": "Product UOM other than Stock",
					"uom": "Box",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 101,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(
			do_not_save=True, product_code="Product UOM other than Stock", uom="Box", conversion_factor=10
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is Box so apply pricing_rule only on Box UOM.
		# Selling UOM is Box and as both UOM are same no need to multiply by conversion_factor.
		self.assertEqual(si.products[0].price_list_rate, 101)
		self.assertEqual(si.products[0].rate, 101)

		si.delete()

		si = create_sales_invoice(do_not_save=True, product_code="Product UOM other than Stock", uom="Nos")
		si.selling_price_list = "_Test Price List"
		si.save()

		# UOM is Box so pricing_rule won't apply as selling_uom is Nos.
		# As Pricing Rule is not applied price of 100 will be fetched from Product Price List.
		self.assertEqual(si.products[0].price_list_rate, 100)
		self.assertEqual(si.products[0].rate, 100)

		si.delete()
		rule.delete()
		frappe.get_doc("Product Price", {"product_code": "Product UOM other than Stock"}).delete()

		product.delete()

	def test_pricing_rule_for_different_currency(self):
		make_product("Test Sanitizer Product")

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Sanitizer Rule",
			"apply_on": "Product Code",
			"products": [
				{
					"product_code": "Test Sanitizer Product",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Rate",
			"rate": 0,
			"priority": 2,
			"margin_type": "Percentage",
			"margin_rate_or_amount": 0.0,
			"company": "_Test Company",
		}

		rule = frappe.get_doc(pricing_rule_record)
		rule.rate_or_discount = "Rate"
		rule.rate = 100.0
		rule.insert()

		rule1 = frappe.get_doc(pricing_rule_record)
		rule1.currency = "USD"
		rule1.rate_or_discount = "Rate"
		rule1.rate = 2.0
		rule1.priority = 1
		rule1.insert()

		args = frappe._dict(
			{
				"product_code": "Test Sanitizer Product",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "USD",
				"doctype": "Sales Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
				"transaction_date": frappe.utils.nowdate(),
			}
		)

		details = get_product_details(args)
		self.assertEqual(details.price_list_rate, 2.0)

		args = frappe._dict(
			{
				"product_code": "Test Sanitizer Product",
				"company": "_Test Company",
				"price_list": "_Test Price List",
				"currency": "INR",
				"doctype": "Sales Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"order_type": "Sales",
				"customer": "_Test Customer",
				"name": None,
				"transaction_date": frappe.utils.nowdate(),
			}
		)

		details = get_product_details(args)
		self.assertEqual(details.price_list_rate, 100.0)

	def test_pricing_rule_for_transaction(self):
		make_product("Water Flask 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		make_pricing_rule(
			selling=1,
			min_qty=5,
			price_or_product_discount="Product",
			apply_on="Transaction",
			free_product="Water Flask 1",
			free_qty=1,
			free_product_rate=10,
		)

		si = create_sales_invoice(qty=5, do_not_submit=True)
		self.assertEqual(len(si.products), 2)
		self.assertEqual(si.products[1].rate, 10)

		si1 = create_sales_invoice(qty=2, do_not_submit=True)
		self.assertEqual(len(si1.products), 1)

		for doc in [si, si1]:
			doc.delete()

	def test_remove_pricing_rule(self):
		product = make_product("Water Flask")
		make_product_price("Water Flask", "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Water Flask Rule",
			"apply_on": "Product Code",
			"price_or_product_discount": "Price",
			"products": [
				{
					"product_code": "Water Flask",
				}
			],
			"selling": 1,
			"currency": "INR",
			"rate_or_discount": "Discount Percentage",
			"discount_percentage": 20,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(do_not_save=True, product_code="Water Flask")
		si.selling_price_list = "_Test Price List"
		si.save()

		self.assertEqual(si.products[0].price_list_rate, 100)
		self.assertEqual(si.products[0].discount_percentage, 20)
		self.assertEqual(si.products[0].rate, 80)

		si.ignore_pricing_rule = 1
		si.save()

		self.assertEqual(si.products[0].discount_percentage, 0)
		self.assertEqual(si.products[0].rate, 100)

		si.delete()
		rule.delete()
		frappe.get_doc("Product Price", {"product_code": "Water Flask"}).delete()
		product.delete()

	def test_multiple_pricing_rules_with_min_qty(self):
		make_pricing_rule(
			discount_percentage=20,
			selling=1,
			priority=1,
			min_qty=4,
			apply_multiple_pricing_rules=1,
			title="_Test Pricing Rule with Min Qty - 1",
		)
		make_pricing_rule(
			discount_percentage=10,
			selling=1,
			priority=2,
			min_qty=4,
			apply_multiple_pricing_rules=1,
			title="_Test Pricing Rule with Min Qty - 2",
		)

		si = create_sales_invoice(do_not_submit=True, customer="_Test Customer 1", qty=1)
		product = si.products[0]
		product.stock_qty = 1
		si.save()
		self.assertFalse(product.discount_percentage)
		product.qty = 5
		product.stock_qty = 5
		si.save()
		self.assertEqual(product.discount_percentage, 30)
		si.delete()

		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule with Min Qty - 1")
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule with Min Qty - 2")

	def test_pricing_rule_for_other_products_cond_with_amount(self):
		product = make_product("Water Flask New")
		other_product = make_product("Other Water Flask New")
		make_product_price(product.name, "_Test Price List", 100)
		make_product_price(other_product.name, "_Test Price List", 100)

		pricing_rule_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Water Flask Rule",
			"apply_on": "Product Code",
			"apply_rule_on_other": "Product Code",
			"price_or_product_discount": "Price",
			"rate_or_discount": "Discount Percentage",
			"other_product_code": other_product.name,
			"products": [
				{
					"product_code": product.name,
				}
			],
			"selling": 1,
			"currency": "INR",
			"min_amt": 200,
			"discount_percentage": 10,
			"company": "_Test Company",
		}
		rule = frappe.get_doc(pricing_rule_record)
		rule.insert()

		si = create_sales_invoice(do_not_save=True, product_code=product.name)
		si.append(
			"products",
			{
				"product_code": other_product.name,
				"product_name": other_product.product_name,
				"description": other_product.description,
				"stock_uom": other_product.stock_uom,
				"uom": other_product.stock_uom,
				"cost_center": si.products[0].cost_center,
				"expense_account": si.products[0].expense_account,
				"warehouse": si.products[0].warehouse,
				"conversion_factor": 1,
				"qty": 1,
			},
		)
		si.selling_price_list = "_Test Price List"
		si.save()

		self.assertEqual(si.products[0].discount_percentage, 0)
		self.assertEqual(si.products[1].discount_percentage, 0)

		si.products[0].qty = 2
		si.save()

		self.assertEqual(si.products[0].discount_percentage, 0)
		self.assertEqual(si.products[0].stock_qty, 2)
		self.assertEqual(si.products[0].amount, 200)
		self.assertEqual(si.products[0].price_list_rate, 100)
		self.assertEqual(si.products[1].discount_percentage, 10)

		si.delete()
		rule.delete()

	def test_pricing_rule_for_product_free_product_rounded_qty_and_recursion(self):
		frappe.delete_doc_if_exists("Pricing Rule", "_Test Pricing Rule")
		test_record = {
			"doctype": "Pricing Rule",
			"title": "_Test Pricing Rule",
			"apply_on": "Product Code",
			"currency": "USD",
			"products": [
				{
					"product_code": "_Test Product",
				}
			],
			"selling": 1,
			"rate": 0,
			"min_qty": 3,
			"max_qty": 7,
			"price_or_product_discount": "Product",
			"same_product": 1,
			"free_qty": 1,
			"round_free_qty": 1,
			"is_recursive": 1,
			"recurse_for": 2,
			"company": "_Test Company",
		}
		frappe.get_doc(test_record.copy()).insert()

		# With pricing rule
		so = make_sales_order(product_code="_Test Product", qty=5)
		so.load_from_db()
		self.assertEqual(so.products[1].is_free_product, 1)
		self.assertEqual(so.products[1].product_code, "_Test Product")
		self.assertEqual(so.products[1].qty, 2)

		so = make_sales_order(product_code="_Test Product", qty=7)
		so.load_from_db()
		self.assertEqual(so.products[1].is_free_product, 1)
		self.assertEqual(so.products[1].product_code, "_Test Product")
		self.assertEqual(so.products[1].qty, 4)


test_dependencies = ["Campaign"]


def make_pricing_rule(**args):
	args = frappe._dict(args)

	doc = frappe.get_doc(
		{
			"doctype": "Pricing Rule",
			"title": args.title or "_Test Pricing Rule",
			"company": args.company or "_Test Company",
			"apply_on": args.apply_on or "Product Code",
			"applicable_for": args.applicable_for,
			"selling": args.selling or 0,
			"currency": "INR",
			"apply_discount_on_rate": args.apply_discount_on_rate or 0,
			"buying": args.buying or 0,
			"min_qty": args.min_qty or 0.0,
			"max_qty": args.max_qty or 0.0,
			"rate_or_discount": args.rate_or_discount or "Discount Percentage",
			"discount_percentage": args.discount_percentage or 0.0,
			"rate": args.rate or 0.0,
			"margin_rate_or_amount": args.margin_rate_or_amount or 0.0,
			"condition": args.condition or "",
			"priority": args.priority or 1,
			"discount_amount": args.discount_amount or 0.0,
			"apply_multiple_pricing_rules": args.apply_multiple_pricing_rules or 0,
		}
	)

	for field in [
		"free_product",
		"free_qty",
		"free_product_rate",
		"priority",
		"margin_type",
		"price_or_product_discount",
	]:
		if args.get(field):
			doc.set(field, args.get(field))

	apply_on = doc.apply_on.replace(" ", "_").lower()
	child_table = {"Product Code": "products", "Product Group": "product_groups", "Brand": "brands"}

	if doc.apply_on != "Transaction":
		doc.append(child_table.get(doc.apply_on), {apply_on: args.get(apply_on) or "_Test Product"})

	doc.insert(ignore_permissions=True)
	if args.get(apply_on) and apply_on != "product_code":
		doc.db_set(apply_on, args.get(apply_on))

	applicable_for = doc.applicable_for.replace(" ", "_").lower()
	if args.get(applicable_for):
		doc.db_set(applicable_for, args.get(applicable_for))

	return doc


def setup_pricing_rule_data():
	if not frappe.db.exists("Campaign", "_Test Campaign"):
		frappe.get_doc(
			{"doctype": "Campaign", "campaign_name": "_Test Campaign", "name": "_Test Campaign"}
		).insert()


def delete_existing_pricing_rules():
	for doctype in [
		"Pricing Rule",
		"Pricing Rule Product Code",
		"Pricing Rule Product Group",
		"Pricing Rule Brand",
	]:

		frappe.db.sql("delete from `tab{0}`".format(doctype))


def make_product_price(product, price_list_name, product_price):
	frappe.get_doc(
		{
			"doctype": "Product Price",
			"price_list": price_list_name,
			"product_code": product,
			"price_list_rate": product_price,
		}
	).insert(ignore_permissions=True, ignore_mandatory=True)
