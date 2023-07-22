# Copyright (c) 2019, Frappe and Contributors
# License: GNU General Public License v3. See license.txt

import frappe


def execute():
	if frappe.db.table_exists("Supplier Product Group"):
		frappe.reload_doc("selling", "doctype", "party_specific_product")
		sig = frappe.db.get_all("Supplier Product Group", fields=["name", "supplier", "product_group"])
		for product in sig:
			psi = frappe.new_doc("Party Specific Product")
			psi.party_type = "Supplier"
			psi.party = product.supplier
			psi.restrict_based_on = "Product Group"
			psi.based_on_value = product.product_group
			psi.insert()
