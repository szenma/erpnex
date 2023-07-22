# Copyright (c) 2018, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	"""
	default supplier was not set in the product defaults for multi company instance,
	        this patch will set the default supplier

	"""
	if not frappe.db.has_column("Product", "default_supplier"):
		return

	frappe.reload_doc("stock", "doctype", "product_default")
	frappe.reload_doc("stock", "doctype", "product")

	companies = frappe.get_all("Company")
	if len(companies) > 1:
		frappe.db.sql(
			""" UPDATE `tabProduct Default`, `tabProduct`
			SET `tabProduct Default`.default_supplier = `tabProduct`.default_supplier
			WHERE
				`tabProduct Default`.parent = `tabProduct`.name and `tabProduct Default`.default_supplier is null
				and `tabProduct`.default_supplier is not null and `tabProduct`.default_supplier != '' """
		)
