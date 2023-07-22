# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe

from erpnext.stock.stock_balance import get_reserved_qty, update_bin_qty


def execute():
	for doctype in ("Sales Order Product", "Bin"):
		frappe.reload_doctype(doctype)

	repost_for = frappe.db.sql(
		"""
		select
			distinct product_code, warehouse
		from
			(
				(
					select distinct product_code, warehouse
								from `tabSales Order Product` where docstatus=1
				) UNION (
					select distinct product_code, warehouse
					from `tabPacked Product` where docstatus=1 and parenttype='Sales Order'
				)
			) so_product
		where
			exists(select name from tabProduct where name=so_product.product_code and ifnull(is_stock_product, 0)=1)
	"""
	)

	for product_code, warehouse in repost_for:
		if not (product_code and warehouse):
			continue
		update_bin_qty(product_code, warehouse, {"reserved_qty": get_reserved_qty(product_code, warehouse)})

	frappe.db.sql(
		"""delete from tabBin
		where exists(
			select name from tabProduct where name=tabBin.product_code and ifnull(is_stock_product, 0) = 0
		)
	"""
	)
