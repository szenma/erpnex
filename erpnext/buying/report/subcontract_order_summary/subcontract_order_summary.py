# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _


def execute(filters=None):
	columns, data = [], []
	columns = get_columns(filters)
	data = get_data(filters)

	return columns, data


def get_data(report_filters):
	data = []
	orders = get_subcontracted_orders(report_filters)

	if orders:
		supplied_products = get_supplied_products(orders, report_filters)
		order_details = prepare_subcontracted_data(orders, supplied_products)
		get_subcontracted_data(order_details, data)

	return data


def get_subcontracted_orders(report_filters):
	fields = [
		f"`tab{report_filters.order_type} Product`.`parent` as order_id",
		f"`tab{report_filters.order_type} Product`.`product_code`",
		f"`tab{report_filters.order_type} Product`.`product_name`",
		f"`tab{report_filters.order_type} Product`.`qty`",
		f"`tab{report_filters.order_type} Product`.`name`",
		f"`tab{report_filters.order_type} Product`.`received_qty`",
		f"`tab{report_filters.order_type}`.`status`",
	]

	filters = get_filters(report_filters)

	return frappe.get_all(report_filters.order_type, fields=fields, filters=filters) or []


def get_filters(report_filters):
	filters = [
		[report_filters.order_type, "docstatus", "=", 1],
		[
			report_filters.order_type,
			"transaction_date",
			"between",
			(report_filters.from_date, report_filters.to_date),
		],
	]

	if report_filters.order_type == "Purchase Order":
		filters.append(["Purchase Order", "is_old_subcontracting_flow", "=", 1])

	for field in ["name", "company"]:
		if report_filters.get(field):
			filters.append([report_filters.order_type, field, "=", report_filters.get(field)])

	return filters


def get_supplied_products(orders, report_filters):
	if not orders:
		return []

	fields = [
		"parent",
		"main_product_code",
		"rm_product_code",
		"required_qty",
		"supplied_qty",
		"returned_qty",
		"total_supplied_qty",
		"consumed_qty",
		"reference_name",
	]

	filters = {"parent": ("in", [d.order_id for d in orders]), "docstatus": 1}

	supplied_products = {}
	supplied_products_table = (
		"Purchase Order Product Supplied"
		if report_filters.order_type == "Purchase Order"
		else "Subcontracting Order Supplied Product"
	)
	for row in frappe.get_all(supplied_products_table, fields=fields, filters=filters):
		new_key = (row.parent, row.reference_name, row.main_product_code)

		supplied_products.setdefault(new_key, []).append(row)

	return supplied_products


def prepare_subcontracted_data(orders, supplied_products):
	order_details = {}
	for row in orders:
		key = (row.order_id, row.name, row.product_code)
		if key not in order_details:
			order_details.setdefault(key, frappe._dict({"order_product": row, "supplied_products": []}))

		details = order_details[key]

		if supplied_products.get(key):
			for supplied_product in supplied_products[key]:
				details["supplied_products"].append(supplied_product)

	return order_details


def get_subcontracted_data(order_details, data):
	for key, details in order_details.products():
		res = details.order_product
		for index, row in enumerate(details.supplied_products):
			if index != 0:
				res = {}

			res.update(row)
			data.append(res)


def get_columns(filters):
	return [
		{
			"label": _("Subcontract Order"),
			"fieldname": "order_id",
			"fieldtype": "Link",
			"options": filters.order_type,
			"width": 100,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 80},
		{
			"label": _("Subcontracted Product"),
			"fieldname": "product_code",
			"fieldtype": "Link",
			"options": "Product",
			"width": 160,
		},
		{"label": _("Order Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 90},
		{"label": _("Received Qty"), "fieldname": "received_qty", "fieldtype": "Float", "width": 110},
		{
			"label": _("Supplied Product"),
			"fieldname": "rm_product_code",
			"fieldtype": "Link",
			"options": "Product",
			"width": 160,
		},
		{"label": _("Required Qty"), "fieldname": "required_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Supplied Qty"), "fieldname": "supplied_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Consumed Qty"), "fieldname": "consumed_qty", "fieldtype": "Float", "width": 120},
		{"label": _("Returned Qty"), "fieldname": "returned_qty", "fieldtype": "Float", "width": 110},
	]
