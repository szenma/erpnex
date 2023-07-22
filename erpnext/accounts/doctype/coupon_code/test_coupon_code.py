# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import unittest

import frappe

from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order

test_dependencies = ["Product"]


def test_create_test_data():
	frappe.set_user("Administrator")
	# create test product
	if not frappe.db.exists("Product", "_Test Tesla Car"):
		product = frappe.get_doc(
			{
				"description": "_Test Tesla Car",
				"doctype": "Product",
				"has_batch_no": 0,
				"has_serial_no": 0,
				"inspection_required": 0,
				"is_stock_product": 1,
				"opening_stock": 100,
				"is_sub_contracted_product": 0,
				"product_code": "_Test Tesla Car",
				"product_group": "_Test Product Group",
				"product_name": "_Test Tesla Car",
				"apply_warehouse_wise_reorder_level": 0,
				"warehouse": "Stores - _TC",
				"valuation_rate": 5000,
				"standard_rate": 5000,
				"product_defaults": [
					{
						"company": "_Test Company",
						"default_warehouse": "Stores - _TC",
						"default_price_list": "_Test Price List",
						"expense_account": "Cost of Goods Sold - _TC",
						"buying_cost_center": "Main - _TC",
						"selling_cost_center": "Main - _TC",
						"income_account": "Sales - _TC",
					}
				],
			}
		)
		product.insert()
	# create test product price
	product_price = frappe.get_list(
		"Product Price",
		filters={"product_code": "_Test Tesla Car", "price_list": "_Test Price List"},
		fields=["name"],
	)
	if len(product_price) == 0:
		product_price = frappe.get_doc(
			{
				"doctype": "Product Price",
				"product_code": "_Test Tesla Car",
				"price_list": "_Test Price List",
				"price_list_rate": 5000,
			}
		)
		product_price.insert()
	# create test product pricing rule
	if not frappe.db.exists("Pricing Rule", {"title": "_Test Pricing Rule for _Test Product"}):
		product_pricing_rule = frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": "_Test Pricing Rule for _Test Product",
				"apply_on": "Product Code",
				"products": [{"product_code": "_Test Tesla Car"}],
				"warehouse": "Stores - _TC",
				"coupon_code_based": 1,
				"selling": 1,
				"rate_or_discount": "Discount Percentage",
				"discount_percentage": 30,
				"company": "_Test Company",
				"currency": "INR",
				"for_price_list": "_Test Price List",
			}
		)
		product_pricing_rule.insert()
	# create test product sales partner
	if not frappe.db.exists("Sales Partner", "_Test Coupon Partner"):
		sales_partner = frappe.get_doc(
			{
				"doctype": "Sales Partner",
				"partner_name": "_Test Coupon Partner",
				"commission_rate": 2,
				"referral_code": "COPART",
			}
		)
		sales_partner.insert()
	# create test product coupon code
	if not frappe.db.exists("Coupon Code", "SAVE30"):
		pricing_rule = frappe.db.get_value(
			"Pricing Rule", {"title": "_Test Pricing Rule for _Test Product"}, ["name"]
		)
		coupon_code = frappe.get_doc(
			{
				"doctype": "Coupon Code",
				"coupon_name": "SAVE30",
				"coupon_code": "SAVE30",
				"pricing_rule": pricing_rule,
				"valid_from": "2014-01-01",
				"maximum_use": 1,
				"used": 0,
			}
		)
		coupon_code.insert()


class TestCouponCode(unittest.TestCase):
	def setUp(self):
		test_create_test_data()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_sales_order_with_coupon_code(self):
		frappe.db.set_value("Coupon Code", "SAVE30", "used", 0)

		so = make_sales_order(
			company="_Test Company",
			warehouse="Stores - _TC",
			customer="_Test Customer",
			selling_price_list="_Test Price List",
			product_code="_Test Tesla Car",
			rate=5000,
			qty=1,
			do_not_submit=True,
		)

		self.assertEqual(so.products[0].rate, 5000)

		so.coupon_code = "SAVE30"
		so.sales_partner = "_Test Coupon Partner"
		so.save()

		# check product price after coupon code is applied
		self.assertEqual(so.products[0].rate, 3500)

		so.submit()
		self.assertEqual(frappe.db.get_value("Coupon Code", "SAVE30", "used"), 1)
