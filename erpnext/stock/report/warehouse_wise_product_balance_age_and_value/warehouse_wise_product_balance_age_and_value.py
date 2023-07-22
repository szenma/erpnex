# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

# Copyright (c) 2013, Tristar Enterprises and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import Count
from frappe.utils import cint, flt, getdate

from erpnext.stock.report.stock_ageing.stock_ageing import FIFOSlots, get_average_age
from erpnext.stock.report.stock_analytics.stock_analytics import (
	get_product_details,
	get_products,
	get_stock_ledger_entries,
)
from erpnext.stock.report.stock_balance.stock_balance import filter_products_with_no_transactions
from erpnext.stock.utils import is_reposting_product_valuation_in_progress


def execute(filters=None):
	is_reposting_product_valuation_in_progress()
	if not filters:
		filters = {}

	validate_filters(filters)

	columns = get_columns(filters)

	products = get_products(filters)
	sle = get_stock_ledger_entries(filters, products)

	product_map = get_product_details(products, sle)
	iwb_map = get_product_warehouse_map(filters, sle)
	warehouse_list = get_warehouse_list(filters)
	product_ageing = FIFOSlots(filters).generate()
	data = []
	product_balance = {}
	product_value = {}

	for (company, product, warehouse) in sorted(iwb_map):
		if not product_map.get(product):
			continue

		row = []
		qty_dict = iwb_map[(company, product, warehouse)]
		product_balance.setdefault((product, product_map[product]["product_group"]), [])
		total_stock_value = 0.00
		for wh in warehouse_list:
			row += [qty_dict.bal_qty] if wh.name == warehouse else [0.00]
			total_stock_value += qty_dict.bal_val if wh.name == warehouse else 0.00

		product_balance[(product, product_map[product]["product_group"])].append(row)
		product_value.setdefault((product, product_map[product]["product_group"]), [])
		product_value[(product, product_map[product]["product_group"])].append(total_stock_value)

	# sum bal_qty by product
	for (product, product_group), wh_balance in product_balance.products():
		if not product_ageing.get(product):
			continue

		total_stock_value = sum(product_value[(product, product_group)])
		row = [product, product_map[product]["product_name"], product_group, total_stock_value]

		fifo_queue = product_ageing[product]["fifo_queue"]
		average_age = 0.00
		if fifo_queue:
			average_age = get_average_age(fifo_queue, filters["to_date"])

		row += [average_age]

		bal_qty = [sum(bal_qty) for bal_qty in zip(*wh_balance)]
		total_qty = sum(bal_qty)
		if len(warehouse_list) > 1:
			row += [total_qty]
		row += bal_qty

		if total_qty > 0:
			data.append(row)
		elif not filters.get("filter_total_zero_qty"):
			data.append(row)
	add_warehouse_column(columns, warehouse_list)
	return columns, data


def get_columns(filters):
	"""return columns"""

	columns = [
		_("Product") + ":Link/Product:150",
		_("Product Name") + ":Link/Product:150",
		_("Product Group") + "::120",
		_("Value") + ":Currency:120",
		_("Age") + ":Float:120",
	]
	return columns


def validate_filters(filters):
	if not (filters.get("product_code") or filters.get("warehouse")):
		sle_count = flt(frappe.qb.from_("Stock Ledger Entry").select(Count("name")).run()[0][0])
		if sle_count > 500000:
			frappe.throw(_("Please set filter based on Product or Warehouse"))
	if not filters.get("company"):
		filters["company"] = frappe.defaults.get_user_default("Company")


def get_warehouse_list(filters):
	from frappe.core.doctype.user_permission.user_permission import get_permitted_documents

	wh = frappe.qb.DocType("Warehouse")
	query = frappe.qb.from_(wh).select(wh.name).where(wh.is_group == 0)

	user_permitted_warehouse = get_permitted_documents("Warehouse")
	if user_permitted_warehouse:
		query = query.where(wh.name.isin(set(user_permitted_warehouse)))
	elif filters.get("warehouse"):
		query = query.where(wh.name == filters.get("warehouse"))

	return query.run(as_dict=True)


def add_warehouse_column(columns, warehouse_list):
	if len(warehouse_list) > 1:
		columns += [_("Total Qty") + ":Int:120"]

	for wh in warehouse_list:
		columns += [_(wh.name) + ":Int:100"]


def get_product_warehouse_map(filters, sle):
	iwb_map = {}
	from_date = getdate(filters.get("from_date"))
	to_date = getdate(filters.get("to_date"))
	float_precision = cint(frappe.db.get_default("float_precision")) or 3

	for d in sle:
		group_by_key = get_group_by_key(d)
		if group_by_key not in iwb_map:
			iwb_map[group_by_key] = frappe._dict(
				{
					"opening_qty": 0.0,
					"opening_val": 0.0,
					"in_qty": 0.0,
					"in_val": 0.0,
					"out_qty": 0.0,
					"out_val": 0.0,
					"bal_qty": 0.0,
					"bal_val": 0.0,
					"val_rate": 0.0,
				}
			)

		qty_dict = iwb_map[group_by_key]
		if d.voucher_type == "Stock Reconciliation" and not d.batch_no:
			qty_diff = flt(d.qty_after_transaction) - flt(qty_dict.bal_qty)
		else:
			qty_diff = flt(d.actual_qty)

		value_diff = flt(d.stock_value_difference)

		if d.posting_date < from_date:
			qty_dict.opening_qty += qty_diff
			qty_dict.opening_val += value_diff

		elif d.posting_date >= from_date and d.posting_date <= to_date:
			if flt(qty_diff, float_precision) >= 0:
				qty_dict.in_qty += qty_diff
				qty_dict.in_val += value_diff
			else:
				qty_dict.out_qty += abs(qty_diff)
				qty_dict.out_val += abs(value_diff)

		qty_dict.val_rate = d.valuation_rate
		qty_dict.bal_qty += qty_diff
		qty_dict.bal_val += value_diff

	iwb_map = filter_products_with_no_transactions(iwb_map, float_precision)

	return iwb_map


def get_group_by_key(row) -> tuple:
	return (row.company, row.product_code, row.warehouse)
