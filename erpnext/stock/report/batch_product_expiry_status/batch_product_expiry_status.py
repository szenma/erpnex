# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import IfNull
from frappe.utils import cint, getdate


def execute(filters=None):
	if not filters:
		filters = {}

	float_precision = cint(frappe.db.get_default("float_precision")) or 3

	columns = get_columns(filters)
	product_map = get_product_details(filters)
	iwb_map = get_product_warehouse_batch_map(filters, float_precision)

	data = []
	for product in sorted(iwb_map):
		for wh in sorted(iwb_map[product]):
			for batch in sorted(iwb_map[product][wh]):
				qty_dict = iwb_map[product][wh][batch]

				data.append(
					[
						product,
						product_map[product]["product_name"],
						product_map[product]["description"],
						wh,
						batch,
						frappe.db.get_value("Batch", batch, "expiry_date"),
						qty_dict.expiry_status,
					]
				)

	return columns, data


def get_columns(filters):
	"""return columns based on filters"""

	columns = (
		[_("Product") + ":Link/Product:100"]
		+ [_("Product Name") + "::150"]
		+ [_("Description") + "::150"]
		+ [_("Warehouse") + ":Link/Warehouse:100"]
		+ [_("Batch") + ":Link/Batch:100"]
		+ [_("Expires On") + ":Date:90"]
		+ [_("Expiry (In Days)") + ":Int:120"]
	)

	return columns


def get_stock_ledger_entries(filters):
	if not filters.get("from_date"):
		frappe.throw(_("'From Date' is required"))

	if not filters.get("to_date"):
		frappe.throw(_("'To Date' is required"))

	sle = frappe.qb.DocType("Stock Ledger Entry")
	query = (
		frappe.qb.from_(sle)
		.select(sle.product_code, sle.batch_no, sle.warehouse, sle.posting_date, sle.actual_qty)
		.where(
			(sle.is_cancelled == 0)
			& (sle.docstatus < 2)
			& (IfNull(sle.batch_no, "") != "")
			& (sle.posting_date <= filters["to_date"])
		)
		.orderby(sle.product_code, sle.warehouse)
	)

	return query.run(as_dict=True)


def get_product_warehouse_batch_map(filters, float_precision):
	sle = get_stock_ledger_entries(filters)
	iwb_map = {}

	from_date = getdate(filters["from_date"])
	to_date = getdate(filters["to_date"])

	for d in sle:
		iwb_map.setdefault(d.product_code, {}).setdefault(d.warehouse, {}).setdefault(
			d.batch_no, frappe._dict({"expires_on": None, "expiry_status": None})
		)

		qty_dict = iwb_map[d.product_code][d.warehouse][d.batch_no]

		expiry_date_unicode = frappe.db.get_value("Batch", d.batch_no, "expiry_date")
		qty_dict.expires_on = expiry_date_unicode

		exp_date = frappe.utils.data.getdate(expiry_date_unicode)
		qty_dict.expires_on = exp_date

		expires_in_days = (exp_date - frappe.utils.datetime.date.today()).days

		if expires_in_days > 0:
			qty_dict.expiry_status = expires_in_days
		else:
			qty_dict.expiry_status = 0

	return iwb_map


def get_product_details(filters):
	product_map = {}
	for d in (frappe.qb.from_("Product").select("name", "product_name", "description")).run(as_dict=True):
		product_map.setdefault(d.name, d)

	return product_map
