# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _


def execute(filters=None):
	if filters.from_date >= filters.to_date:
		frappe.msgprint(_("To Date must be greater than From Date"))

	data = []
	columns = get_columns(filters)
	get_data(data, filters)
	return columns, data


def get_columns(filters):
	return [
		{
			"label": _("Subcontract Order"),
			"fieldtype": "Link",
			"fieldname": "subcontract_order",
			"options": filters.order_type,
			"width": 150,
		},
		{"label": _("Date"), "fieldtype": "Date", "fieldname": "date", "hidden": 1, "width": 150},
		{
			"label": _("Supplier"),
			"fieldtype": "Link",
			"fieldname": "supplier",
			"options": "Supplier",
			"width": 150,
		},
		{
			"label": _("Finished Good Product Code"),
			"fieldtype": "Data",
			"fieldname": "fg_product_code",
			"width": 100,
		},
		{"label": _("Product name"), "fieldtype": "Data", "fieldname": "product_name", "width": 100},
		{
			"label": _("Required Quantity"),
			"fieldtype": "Float",
			"fieldname": "required_qty",
			"width": 100,
		},
		{
			"label": _("Received Quantity"),
			"fieldtype": "Float",
			"fieldname": "received_qty",
			"width": 100,
		},
		{"label": _("Pending Quantity"), "fieldtype": "Float", "fieldname": "pending_qty", "width": 100},
	]


def get_data(data, filters):
	orders = get_subcontract_orders(filters)
	orders_name = [order.name for order in orders]
	subcontracted_products = get_subcontract_order_supplied_product(filters.order_type, orders_name)
	for product in subcontracted_products:
		for order in orders:
			if order.name == product.parent and product.received_qty < product.qty:
				row = {
					"subcontract_order": product.parent,
					"date": order.transaction_date,
					"supplier": order.supplier,
					"fg_product_code": product.product_code,
					"product_name": product.product_name,
					"required_qty": product.qty,
					"received_qty": product.received_qty,
					"pending_qty": product.qty - product.received_qty,
				}
				data.append(row)


def get_subcontract_orders(filters):
	record_filters = [
		["supplier", "=", filters.supplier],
		["transaction_date", "<=", filters.to_date],
		["transaction_date", ">=", filters.from_date],
		["docstatus", "=", 1],
	]

	if filters.order_type == "Purchase Order":
		record_filters.append(["is_old_subcontracting_flow", "=", 1])

	return frappe.get_all(
		filters.order_type, filters=record_filters, fields=["name", "transaction_date", "supplier"]
	)


def get_subcontract_order_supplied_product(order_type, orders):
	return frappe.get_all(
		f"{order_type} Product",
		filters=[("parent", "IN", orders)],
		fields=["parent", "product_code", "product_name", "qty", "received_qty"],
	)
