# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.setup.utils import get_exchange_rate


def execute(filters=None):
	if not filters:
		return [], []

	columns = get_columns(filters)
	supplier_quotation_data = get_data(filters)

	data, chart_data = prepare_data(supplier_quotation_data, filters)
	message = get_message()

	return columns, data, message, chart_data


def get_data(filters):
	sq = frappe.qb.DocType("Supplier Quotation")
	sq_product = frappe.qb.DocType("Supplier Quotation Product")

	query = (
		frappe.qb.from_(sq_product)
		.from_(sq)
		.select(
			sq_product.parent,
			sq_product.product_code,
			sq_product.qty,
			sq_product.stock_qty,
			sq_product.amount,
			sq_product.uom,
			sq_product.stock_uom,
			sq_product.request_for_quotation,
			sq_product.lead_time_days,
			sq.supplier.as_("supplier_name"),
			sq.valid_till,
		)
		.where(
			(sq_product.parent == sq.name)
			& (sq_product.docstatus < 2)
			& (sq.company == filters.get("company"))
			& (sq.transaction_date.between(filters.get("from_date"), filters.get("to_date")))
		)
		.orderby(sq.transaction_date, sq_product.product_code)
	)

	if filters.get("product_code"):
		query = query.where(sq_product.product_code == filters.get("product_code"))

	if filters.get("supplier_quotation"):
		query = query.where(sq_product.parent.isin(filters.get("supplier_quotation")))

	if filters.get("request_for_quotation"):
		query = query.where(sq_product.request_for_quotation == filters.get("request_for_quotation"))

	if filters.get("supplier"):
		query = query.where(sq.supplier.isin(filters.get("supplier")))

	if not filters.get("include_expired"):
		query = query.where(sq.status != "Expired")

	supplier_quotation_data = query.run(as_dict=True)

	return supplier_quotation_data


def prepare_data(supplier_quotation_data, filters):
	out, groups, qty_list, suppliers, chart_data = [], [], [], [], []
	group_wise_map = defaultdict(list)
	supplier_qty_price_map = {}

	group_by_field = (
		"supplier_name" if filters.get("group_by") == "Group by Supplier" else "product_code"
	)
	company_currency = frappe.db.get_default("currency")
	float_precision = cint(frappe.db.get_default("float_precision")) or 2

	for data in supplier_quotation_data:
		group = data.get(group_by_field)  # get product or supplier value for this row

		supplier_currency = frappe.db.get_value(
			"Supplier", data.get("supplier_name"), "default_currency"
		)

		if supplier_currency:
			exchange_rate = get_exchange_rate(supplier_currency, company_currency)
		else:
			exchange_rate = 1

		row = {
			"product_code": ""
			if group_by_field == "product_code"
			else data.get("product_code"),  # leave blank if group by field
			"supplier_name": "" if group_by_field == "supplier_name" else data.get("supplier_name"),
			"quotation": data.get("parent"),
			"qty": data.get("qty"),
			"price": flt(data.get("amount") * exchange_rate, float_precision),
			"uom": data.get("uom"),
			"stock_uom": data.get("stock_uom"),
			"request_for_quotation": data.get("request_for_quotation"),
			"valid_till": data.get("valid_till"),
			"lead_time_days": data.get("lead_time_days"),
		}
		row["price_per_unit"] = flt(row["price"]) / (flt(data.get("stock_qty")) or 1)

		# map for report view of form {'supplier1'/'product1':[{},{},...]}
		group_wise_map[group].append(row)

		# map for chart preparation of the form {'supplier1': {'qty': 'price'}}
		supplier = data.get("supplier_name")
		if filters.get("product_code"):
			if not supplier in supplier_qty_price_map:
				supplier_qty_price_map[supplier] = {}
			supplier_qty_price_map[supplier][row["qty"]] = row["price"]

		groups.append(group)
		suppliers.append(supplier)
		qty_list.append(data.get("qty"))

	groups = list(set(groups))
	suppliers = list(set(suppliers))
	qty_list = list(set(qty_list))

	highlight_min_price = group_by_field == "product_code" or filters.get("product_code")

	# final data format for report view
	for group in groups:
		group_entries = group_wise_map[group]  # all entries pertaining to product/supplier
		group_entries[0].update({group_by_field: group})  # Add product/supplier name in first group row

		if highlight_min_price:
			prices = [group_entry["price_per_unit"] for group_entry in group_entries]
			min_price = min(prices)

		for entry in group_entries:
			if highlight_min_price and entry["price_per_unit"] == min_price:
				entry["min"] = 1
			out.append(entry)

	if filters.get("product_code"):
		# render chart only for one product comparison
		chart_data = prepare_chart_data(suppliers, qty_list, supplier_qty_price_map)

	return out, chart_data


def prepare_chart_data(suppliers, qty_list, supplier_qty_price_map):
	data_points_map = {}
	qty_list.sort()

	# create qty wise values map of the form {'qty1':[value1, value2]}
	for supplier in suppliers:
		entry = supplier_qty_price_map[supplier]
		for qty in qty_list:
			if not qty in data_points_map:
				data_points_map[qty] = []
			if qty in entry:
				data_points_map[qty].append(entry[qty])
			else:
				data_points_map[qty].append(None)

	dataset = []
	currency_symbol = frappe.db.get_value("Currency", frappe.db.get_default("currency"), "symbol")
	for qty in qty_list:
		datapoints = {
			"name": currency_symbol + " (Qty " + str(qty) + " )",
			"values": data_points_map[qty],
		}
		dataset.append(datapoints)

	chart_data = {"data": {"labels": suppliers, "datasets": dataset}, "type": "bar"}

	return chart_data


def get_columns(filters):
	group_by_columns = [
		{
			"fieldname": "supplier_name",
			"label": _("Supplier"),
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 150,
		},
		{
			"fieldname": "product_code",
			"label": _("Product"),
			"fieldtype": "Link",
			"options": "Product",
			"width": 150,
		},
	]

	columns = [
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 90},
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 80},
		{
			"fieldname": "price",
			"label": _("Price"),
			"fieldtype": "Currency",
			"options": "Company:company:default_currency",
			"width": 110,
		},
		{
			"fieldname": "stock_uom",
			"label": _("Stock UOM"),
			"fieldtype": "Link",
			"options": "UOM",
			"width": 90,
		},
		{
			"fieldname": "price_per_unit",
			"label": _("Price per Unit (Stock UOM)"),
			"fieldtype": "Currency",
			"options": "Company:company:default_currency",
			"width": 120,
		},
		{
			"fieldname": "quotation",
			"label": _("Supplier Quotation"),
			"fieldtype": "Link",
			"options": "Supplier Quotation",
			"width": 200,
		},
		{"fieldname": "valid_till", "label": _("Valid Till"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "lead_time_days",
			"label": _("Lead Time (Days)"),
			"fieldtype": "Int",
			"width": 100,
		},
		{
			"fieldname": "request_for_quotation",
			"label": _("Request for Quotation"),
			"fieldtype": "Link",
			"options": "Request for Quotation",
			"width": 150,
		},
	]

	if filters.get("group_by") == "Group by Product":
		group_by_columns.reverse()

	columns[0:0] = group_by_columns  # add positioned group by columns to the report
	return columns


def get_message():
	return """<span class="indicator">
		Valid till : &nbsp;&nbsp;
		</span>
		<span class="indicator orange">
		Expires in a week or less
		</span>
		&nbsp;&nbsp;
		<span class="indicator red">
		Expires today / Already Expired
		</span>"""
