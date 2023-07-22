# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Abs, Sum
from frappe.utils import flt, getdate


def execute(filters=None):
	if not filters:
		filters = {}
	float_precision = frappe.db.get_default("float_precision")

	avg_daily_outgoing = 0
	diff = ((getdate(filters.get("to_date")) - getdate(filters.get("from_date"))).days) + 1
	if diff <= 0:
		frappe.throw(_("'From Date' must be after 'To Date'"))

	columns = get_columns()
	products = get_product_info(filters)
	consumed_product_map = get_consumed_products(filters)
	delivered_product_map = get_delivered_products(filters)

	data = []
	for product in products:
		total_outgoing = flt(consumed_product_map.get(product.name, 0)) + flt(
			delivered_product_map.get(product.name, 0)
		)
		avg_daily_outgoing = flt(total_outgoing / diff, float_precision)
		reorder_level = (avg_daily_outgoing * flt(product.lead_time_days)) + flt(product.safety_stock)

		data.append(
			[
				product.name,
				product.product_name,
				product.product_group,
				product.brand,
				product.description,
				product.safety_stock,
				product.lead_time_days,
				consumed_product_map.get(product.name, 0),
				delivered_product_map.get(product.name, 0),
				total_outgoing,
				avg_daily_outgoing,
				reorder_level,
			]
		)

	return columns, data


def get_columns():
	return [
		_("Product") + ":Link/Product:120",
		_("Product Name") + ":Data:120",
		_("Product Group") + ":Link/Product Group:100",
		_("Brand") + ":Link/Brand:100",
		_("Description") + "::160",
		_("Safety Stock") + ":Float:160",
		_("Lead Time Days") + ":Float:120",
		_("Consumed") + ":Float:120",
		_("Delivered") + ":Float:120",
		_("Total Outgoing") + ":Float:120",
		_("Avg Daily Outgoing") + ":Float:160",
		_("Reorder Level") + ":Float:120",
	]


def get_product_info(filters):
	from erpnext.stock.report.stock_ledger.stock_ledger import get_product_group_condition

	product = frappe.qb.DocType("Product")
	query = (
		frappe.qb.from_(product)
		.select(
			product.name,
			product.product_name,
			product.description,
			product.brand,
			product.product_group,
			product.safety_stock,
			product.lead_time_days,
		)
		.where((product.is_stock_product == 1) & (product.disabled == 0))
	)

	if brand := filters.get("brand"):
		query = query.where(product.brand == brand)

	if conditions := get_product_group_condition(filters.get("product_group"), product):
		query = query.where(conditions)

	return query.run(as_dict=True)


def get_consumed_products(filters):
	purpose_to_exclude = [
		"Material Transfer for Manufacture",
		"Material Transfer",
		"Send to Subcontractor",
	]

	se = frappe.qb.DocType("Stock Entry")
	sle = frappe.qb.DocType("Stock Ledger Entry")
	query = (
		frappe.qb.from_(sle)
		.left_join(se)
		.on(sle.voucher_no == se.name)
		.select(sle.product_code, Abs(Sum(sle.actual_qty)).as_("consumed_qty"))
		.where(
			(sle.actual_qty < 0)
			& (sle.is_cancelled == 0)
			& (sle.voucher_type.notin(["Delivery Note", "Sales Invoice"]))
			& ((se.purpose.isnull()) | (se.purpose.notin(purpose_to_exclude)))
		)
		.groupby(sle.product_code)
	)
	query = get_filtered_query(filters, sle, query)

	consumed_products = query.run(as_dict=True)

	consumed_products_map = {product.product_code: product.consumed_qty for product in consumed_products}
	return consumed_products_map


def get_delivered_products(filters):
	parent = frappe.qb.DocType("Delivery Note")
	child = frappe.qb.DocType("Delivery Note Product")
	query = (
		frappe.qb.from_(parent)
		.from_(child)
		.select(child.product_code, Sum(child.stock_qty).as_("dn_qty"))
		.where((parent.name == child.parent) & (parent.docstatus == 1))
		.groupby(child.product_code)
	)
	query = get_filtered_query(filters, parent, query)

	dn_products = query.run(as_dict=True)

	parent = frappe.qb.DocType("Sales Invoice")
	child = frappe.qb.DocType("Sales Invoice Product")
	query = (
		frappe.qb.from_(parent)
		.from_(child)
		.select(child.product_code, Sum(child.stock_qty).as_("si_qty"))
		.where((parent.name == child.parent) & (parent.docstatus == 1) & (parent.update_stock == 1))
		.groupby(child.product_code)
	)
	query = get_filtered_query(filters, parent, query)

	si_products = query.run(as_dict=True)

	dn_product_map = {}
	for product in dn_products:
		dn_product_map.setdefault(product.product_code, product.dn_qty)

	for product in si_products:
		dn_product_map.setdefault(product.product_code, product.si_qty)

	return dn_product_map


def get_filtered_query(filters, table, query):
	if filters.get("from_date") and filters.get("to_date"):
		query = query.where(table.posting_date.between(filters["from_date"], filters["to_date"]))
	else:
		frappe.throw(_("From and To dates are required"))

	return query
