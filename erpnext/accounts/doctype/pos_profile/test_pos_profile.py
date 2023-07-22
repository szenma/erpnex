# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors and Contributors
# See license.txt

import unittest

import frappe

from erpnext.accounts.doctype.pos_profile.pos_profile import get_child_nodes
from erpnext.stock.get_product_details import get_pos_profile

test_dependencies = ["Product"]


class TestPOSProfile(unittest.TestCase):
	def test_pos_profile(self):
		make_pos_profile()

		pos_profile = get_pos_profile("_Test Company") or {}
		if pos_profile:
			doc = frappe.get_doc("POS Profile", pos_profile.get("name"))
			doc.append("product_groups", {"product_group": "_Test Product Group"})
			doc.append("customer_groups", {"customer_group": "_Test Customer Group"})
			doc.save()
			products = get_products_list(doc, doc.company)
			customers = get_customers_list(doc)

			products_count = frappe.db.sql(
				""" select count(name) from tabProduct where product_group = '_Test Product Group'""", as_list=1
			)
			customers_count = frappe.db.sql(
				""" select count(name) from tabCustomer where customer_group = '_Test Customer Group'"""
			)

			self.assertEqual(len(products), products_count[0][0])
			self.assertEqual(len(customers), customers_count[0][0])

		frappe.db.sql("delete from `tabPOS Profile`")


def get_customers_list(pos_profile=None):
	if pos_profile is None:
		pos_profile = {}
	cond = "1=1"
	customer_groups = []
	if pos_profile.get("customer_groups"):
		# Get customers based on the customer groups defined in the POS profile
		for d in pos_profile.get("customer_groups"):
			customer_groups.extend(
				[d.get("name") for d in get_child_nodes("Customer Group", d.get("customer_group"))]
			)
		cond = "customer_group in (%s)" % (", ".join(["%s"] * len(customer_groups)))

	return (
		frappe.db.sql(
			""" select name, customer_name, customer_group,
		territory, customer_pos_id from tabCustomer where disabled = 0
		and {cond}""".format(
				cond=cond
			),
			tuple(customer_groups),
			as_dict=1,
		)
		or {}
	)


def get_products_list(pos_profile, company):
	cond = ""
	args_list = []
	if pos_profile.get("product_groups"):
		# Get products based on the product groups defined in the POS profile
		for d in pos_profile.get("product_groups"):
			args_list.extend([d.name for d in get_child_nodes("Product Group", d.product_group)])
		if args_list:
			cond = "and i.product_group in (%s)" % (", ".join(["%s"] * len(args_list)))

	return frappe.db.sql(
		"""
		select
			i.name, i.product_code, i.product_name, i.description, i.product_group, i.has_batch_no,
			i.has_serial_no, i.is_stock_product, i.brand, i.stock_uom, i.image,
			id.expense_account, id.selling_cost_center, id.default_warehouse,
			i.sales_uom, c.conversion_factor
		from
			`tabProduct` i
		left join `tabProduct Default` id on id.parent = i.name and id.company = %s
		left join `tabUOM Conversion Detail` c on i.name = c.parent and i.sales_uom = c.uom
		where
			i.disabled = 0 and i.has_variants = 0 and i.is_sales_product = 1 and i.is_fixed_asset = 0
			{cond}
		""".format(
			cond=cond
		),
		tuple([company] + args_list),
		as_dict=1,
	)


def make_pos_profile(**args):
	frappe.db.sql("delete from `tabPOS Payment Method`")
	frappe.db.sql("delete from `tabPOS Profile`")

	args = frappe._dict(args)

	pos_profile = frappe.get_doc(
		{
			"company": args.company or "_Test Company",
			"cost_center": args.cost_center or "_Test Cost Center - _TC",
			"currency": args.currency or "INR",
			"doctype": "POS Profile",
			"expense_account": args.expense_account or "_Test Account Cost for Goods Sold - _TC",
			"income_account": args.income_account or "Sales - _TC",
			"name": args.name or "_Test POS Profile",
			"naming_series": "_T-POS Profile-",
			"selling_price_list": args.selling_price_list or "_Test Price List",
			"territory": args.territory or "_Test Territory",
			"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
			"warehouse": args.warehouse or "_Test Warehouse - _TC",
			"write_off_account": args.write_off_account or "_Test Write Off - _TC",
			"write_off_cost_center": args.write_off_cost_center or "_Test Write Off Cost Center - _TC",
		}
	)

	mode_of_payment = frappe.get_doc("Mode of Payment", "Cash")
	company = args.company or "_Test Company"
	default_account = args.income_account or "Sales - _TC"

	if not frappe.db.get_value("Mode of Payment Account", {"company": company, "parent": "Cash"}):
		mode_of_payment.append("accounts", {"company": company, "default_account": default_account})
		mode_of_payment.save()

	pos_profile.append("payments", {"mode_of_payment": "Cash", "default": 1})

	if not frappe.db.exists("POS Profile", args.name or "_Test POS Profile"):
		pos_profile.insert()

	return pos_profile