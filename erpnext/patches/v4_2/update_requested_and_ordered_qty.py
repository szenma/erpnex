# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	from erpnext.stock.stock_balance import get_indented_qty, get_ordered_qty, update_bin_qty

	count = 0
	for product_code, warehouse in frappe.db.sql(
		"""select distinct product_code, warehouse from
		(select product_code, warehouse from tabBin
		union
		select product_code, warehouse from `tabStock Ledger Entry`) a"""
	):
		try:
			if not (product_code and warehouse):
				continue
			count += 1
			update_bin_qty(
				product_code,
				warehouse,
				{
					"indented_qty": get_indented_qty(product_code, warehouse),
					"ordered_qty": get_ordered_qty(product_code, warehouse),
				},
			)
			if count % 200 == 0:
				frappe.db.commit()
		except Exception:
			frappe.db.rollback()
