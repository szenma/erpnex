# Copyright (c) 2018, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	frappe.reload_doc("stock", "doctype", "product")
	frappe.db.sql(
		""" update `tabProduct` set include_product_in_manufacturing = 1
		where ifnull(is_stock_product, 0) = 1"""
	)

	for doctype in ["BOM Product", "Work Order Product", "BOM Explosion Product"]:
		frappe.reload_doc("manufacturing", "doctype", frappe.scrub(doctype))

		frappe.db.sql(
			""" update `tab{0}` child, tabProduct product
			set
				child.include_product_in_manufacturing = 1
			where
				child.product_code = product.name and ifnull(product.is_stock_product, 0) = 1
		""".format(
				doctype
			)
		)
