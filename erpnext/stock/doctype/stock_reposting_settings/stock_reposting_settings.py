# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime, get_time_str, time_diff_in_hours


class StockRepostingSettings(Document):
	def validate(self):
		self.set_minimum_reposting_time_slot()

	def set_minimum_reposting_time_slot(self):
		"""Ensure that timeslot for reposting is at least 12 hours."""
		if not self.limit_reposting_timeslot:
			return

		start_time = get_datetime(self.start_time)
		end_time = get_datetime(self.end_time)

		if start_time > end_time:
			end_time = add_to_date(end_time, days=1, as_datetime=True)

		diff = time_diff_in_hours(end_time, start_time)

		if diff < 10:
			self.end_time = get_time_str(add_to_date(self.start_time, hours=10, as_datetime=True))

	@frappe.whitelist()
	def convert_to_product_wh_reposting(self):
		"""Convert Transaction reposting to Product Warehouse based reposting if Product Based Reposting has enabled."""

		reposting_data = get_reposting_entries()

		vouchers = [d.voucher_no for d in reposting_data]

		product_warehouses = {}

		for ledger in get_stock_ledgers(vouchers):
			key = (ledger.product_code, ledger.warehouse)
			if key not in product_warehouses:
				product_warehouses[key] = ledger.posting_date
			elif frappe.utils.getdate(product_warehouses.get(key)) > frappe.utils.getdate(ledger.posting_date):
				product_warehouses[key] = ledger.posting_date

		for key, posting_date in product_warehouses.products():
			product_code, warehouse = key
			create_repost_product_valuation(product_code, warehouse, posting_date)

		for row in reposting_data:
			frappe.db.set_value("Repost Product Valuation", row.name, "status", "Skipped")

		self.db_set("product_based_reposting", 1)
		frappe.msgprint(_("Product Warehouse based reposting has been enabled."))


def get_reposting_entries():
	return frappe.get_all(
		"Repost Product Valuation",
		fields=["voucher_no", "name"],
		filters={"status": ("in", ["Queued", "In Progress"]), "docstatus": 1, "based_on": "Transaction"},
	)


def get_stock_ledgers(vouchers):
	return frappe.get_all(
		"Stock Ledger Entry",
		fields=["product_code", "warehouse", "posting_date"],
		filters={"voucher_no": ("in", vouchers)},
	)


def create_repost_product_valuation(product_code, warehouse, posting_date):
	frappe.get_doc(
		{
			"doctype": "Repost Product Valuation",
			"company": frappe.get_cached_value("Warehouse", warehouse, "company"),
			"posting_date": posting_date,
			"based_on": "Product and Warehouse",
			"posting_time": "00:00:01",
			"product_code": product_code,
			"warehouse": warehouse,
			"allow_negative_stock": True,
			"status": "Queued",
		}
	).submit()
