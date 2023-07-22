# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import copy

import frappe
from frappe import _
from frappe.query_builder.functions import IfNull
from frappe.utils import date_diff, flt, getdate


def execute(filters=None):
	if not filters:
		return [], []

	validate_filters(filters)

	columns = get_columns(filters)
	data = get_data(filters)

	if not data:
		return [], [], None, []

	data, chart_data = prepare_data(data, filters)

	return columns, data, None, chart_data


def validate_filters(filters):
	from_date, to_date = filters.get("from_date"), filters.get("to_date")

	if not from_date and to_date:
		frappe.throw(_("From and To Dates are required."))
	elif date_diff(to_date, from_date) < 0:
		frappe.throw(_("To Date cannot be before From Date."))


def get_data(filters):
	po = frappe.qb.DocType("Purchase Order")
	po_product = frappe.qb.DocType("Purchase Order Product")
	pi_product = frappe.qb.DocType("Purchase Invoice Product")

	query = (
		frappe.qb.from_(po)
		.from_(po_product)
		.left_join(pi_product)
		.on(pi_product.po_detail == po_product.name)
		.select(
			po.transaction_date.as_("date"),
			po_product.schedule_date.as_("required_date"),
			po_product.project,
			po.name.as_("purchase_order"),
			po.status,
			po.supplier,
			po_product.product_code,
			po_product.qty,
			po_product.received_qty,
			(po_product.qty - po_product.received_qty).as_("pending_qty"),
			IfNull(pi_product.qty, 0).as_("billed_qty"),
			po_product.base_amount.as_("amount"),
			(po_product.received_qty * po_product.base_rate).as_("received_qty_amount"),
			(po_product.billed_amt * IfNull(po.conversion_rate, 1)).as_("billed_amount"),
			(po_product.base_amount - (po_product.billed_amt * IfNull(po.conversion_rate, 1))).as_(
				"pending_amount"
			),
			po.set_warehouse.as_("warehouse"),
			po.company,
			po_product.name,
		)
		.where(
			(po_product.parent == po.name) & (po.status.notin(("Stopped", "Closed"))) & (po.docstatus == 1)
		)
		.groupby(po_product.name)
		.orderby(po.transaction_date)
	)

	for field in ("company", "name"):
		if filters.get(field):
			query = query.where(po[field] == filters.get(field))

	if filters.get("from_date") and filters.get("to_date"):
		query = query.where(
			po.transaction_date.between(filters.get("from_date"), filters.get("to_date"))
		)

	if filters.get("status"):
		query = query.where(po.status.isin(filters.get("status")))

	if filters.get("project"):
		query = query.where(po_product.project == filters.get("project"))

	data = query.run(as_dict=True)

	return data


def prepare_data(data, filters):
	completed, pending = 0, 0
	pending_field = "pending_amount"
	completed_field = "billed_amount"

	if filters.get("group_by_po"):
		purchase_order_map = {}

	for row in data:
		# sum data for chart
		completed += row[completed_field]
		pending += row[pending_field]

		# prepare data for report view
		row["qty_to_bill"] = flt(row["qty"]) - flt(row["billed_qty"])

		if filters.get("group_by_po"):
			po_name = row["purchase_order"]

			if not po_name in purchase_order_map:
				# create an entry
				row_copy = copy.deepcopy(row)
				purchase_order_map[po_name] = row_copy
			else:
				# update existing entry
				po_row = purchase_order_map[po_name]
				po_row["required_date"] = min(getdate(po_row["required_date"]), getdate(row["required_date"]))

				# sum numeric columns
				fields = [
					"qty",
					"received_qty",
					"pending_qty",
					"billed_qty",
					"qty_to_bill",
					"amount",
					"received_qty_amount",
					"billed_amount",
					"pending_amount",
				]
				for field in fields:
					po_row[field] = flt(row[field]) + flt(po_row[field])

	chart_data = prepare_chart_data(pending, completed)

	if filters.get("group_by_po"):
		data = []
		for po in purchase_order_map:
			data.append(purchase_order_map[po])
		return data, chart_data

	return data, chart_data


def prepare_chart_data(pending, completed):
	labels = ["Amount to Bill", "Billed Amount"]

	return {
		"data": {"labels": labels, "datasets": [{"values": [pending, completed]}]},
		"type": "donut",
		"height": 300,
	}


def get_columns(filters):
	columns = [
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 90},
		{"label": _("Required By"), "fieldname": "required_date", "fieldtype": "Date", "width": 90},
		{
			"label": _("Purchase Order"),
			"fieldname": "purchase_order",
			"fieldtype": "Link",
			"options": "Purchase Order",
			"width": 160,
		},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
		{
			"label": _("Supplier"),
			"fieldname": "supplier",
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 130,
		},
		{
			"label": _("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 130,
		},
	]

	if not filters.get("group_by_po"):
		columns.append(
			{
				"label": _("Product Code"),
				"fieldname": "product_code",
				"fieldtype": "Link",
				"options": "Product",
				"width": 100,
			}
		)

	columns.extend(
		[
			{
				"label": _("Qty"),
				"fieldname": "qty",
				"fieldtype": "Float",
				"width": 120,
				"convertible": "qty",
			},
			{
				"label": _("Received Qty"),
				"fieldname": "received_qty",
				"fieldtype": "Float",
				"width": 120,
				"convertible": "qty",
			},
			{
				"label": _("Pending Qty"),
				"fieldname": "pending_qty",
				"fieldtype": "Float",
				"width": 80,
				"convertible": "qty",
			},
			{
				"label": _("Billed Qty"),
				"fieldname": "billed_qty",
				"fieldtype": "Float",
				"width": 80,
				"convertible": "qty",
			},
			{
				"label": _("Qty to Bill"),
				"fieldname": "qty_to_bill",
				"fieldtype": "Float",
				"width": 80,
				"convertible": "qty",
			},
			{
				"label": _("Amount"),
				"fieldname": "amount",
				"fieldtype": "Currency",
				"width": 110,
				"options": "Company:company:default_currency",
				"convertible": "rate",
			},
			{
				"label": _("Billed Amount"),
				"fieldname": "billed_amount",
				"fieldtype": "Currency",
				"width": 110,
				"options": "Company:company:default_currency",
				"convertible": "rate",
			},
			{
				"label": _("Pending Amount"),
				"fieldname": "pending_amount",
				"fieldtype": "Currency",
				"width": 130,
				"options": "Company:company:default_currency",
				"convertible": "rate",
			},
			{
				"label": _("Received Qty Amount"),
				"fieldname": "received_qty_amount",
				"fieldtype": "Currency",
				"width": 130,
				"options": "Company:company:default_currency",
				"convertible": "rate",
			},
			{
				"label": _("Warehouse"),
				"fieldname": "warehouse",
				"fieldtype": "Link",
				"options": "Warehouse",
				"width": 100,
			},
			{
				"label": _("Company"),
				"fieldname": "company",
				"fieldtype": "Link",
				"options": "Company",
				"width": 100,
			},
		]
	)

	return columns
