# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	frappe.reload_doc("stock", "doctype", "product_price")

	frappe.db.sql(
		""" update `tabProduct Price`, `tabProduct`
		set
			`tabProduct Price`.brand = `tabProduct`.brand
		where
			`tabProduct Price`.product_code = `tabProduct`.name
			and `tabProduct`.brand is not null and `tabProduct`.brand != ''"""
	)
