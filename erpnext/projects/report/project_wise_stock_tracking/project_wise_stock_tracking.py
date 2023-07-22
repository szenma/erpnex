# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns()
	proj_details = get_project_details()
	pr_product_map = get_purchased_products_cost()
	se_product_map = get_issued_products_cost()
	dn_product_map = get_delivered_products_cost()

	data = []
	for project in proj_details:
		data.append(
			[
				project.name,
				pr_product_map.get(project.name, 0),
				se_product_map.get(project.name, 0),
				dn_product_map.get(project.name, 0),
				project.project_name,
				project.status,
				project.company,
				project.customer,
				project.estimated_costing,
				project.expected_start_date,
				project.expected_end_date,
			]
		)

	return columns, data


def get_columns():
	return [
		_("Project Id") + ":Link/Project:140",
		_("Cost of Purchased Products") + ":Currency:160",
		_("Cost of Issued Products") + ":Currency:160",
		_("Cost of Delivered Products") + ":Currency:160",
		_("Project Name") + "::120",
		_("Project Status") + "::120",
		_("Company") + ":Link/Company:100",
		_("Customer") + ":Link/Customer:140",
		_("Project Value") + ":Currency:120",
		_("Project Start Date") + ":Date:120",
		_("Completion Date") + ":Date:120",
	]


def get_project_details():
	return frappe.db.sql(
		""" select name, project_name, status, company, customer, estimated_costing,
		expected_start_date, expected_end_date from tabProject where docstatus < 2""",
		as_dict=1,
	)


def get_purchased_products_cost():
	pr_products = frappe.db.sql(
		"""select project, sum(base_net_amount) as amount
		from `tabPurchase Receipt Product` where ifnull(project, '') != ''
		and docstatus = 1 group by project""",
		as_dict=1,
	)

	pr_product_map = {}
	for product in pr_products:
		pr_product_map.setdefault(product.project, product.amount)

	return pr_product_map


def get_issued_products_cost():
	se_products = frappe.db.sql(
		"""select se.project, sum(se_product.amount) as amount
		from `tabStock Entry` se, `tabStock Entry Detail` se_product
		where se.name = se_product.parent and se.docstatus = 1 and ifnull(se_product.t_warehouse, '') = ''
		and ifnull(se.project, '') != '' group by se.project""",
		as_dict=1,
	)

	se_product_map = {}
	for product in se_products:
		se_product_map.setdefault(product.project, product.amount)

	return se_product_map


def get_delivered_products_cost():
	dn_products = frappe.db.sql(
		"""select dn.project, sum(dn_product.base_net_amount) as amount
		from `tabDelivery Note` dn, `tabDelivery Note Product` dn_product
		where dn.name = dn_product.parent and dn.docstatus = 1 and ifnull(dn.project, '') != ''
		group by dn.project""",
		as_dict=1,
	)

	si_products = frappe.db.sql(
		"""select si.project, sum(si_product.base_net_amount) as amount
		from `tabSales Invoice` si, `tabSales Invoice Product` si_product
		where si.name = si_product.parent and si.docstatus = 1 and si.update_stock = 1
		and si.is_pos = 1 and ifnull(si.project, '') != ''
		group by si.project""",
		as_dict=1,
	)

	dn_product_map = {}
	for product in dn_products:
		dn_product_map.setdefault(product.project, product.amount)

	for product in si_products:
		dn_product_map.setdefault(product.project, product.amount)

	return dn_product_map
