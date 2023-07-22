# Copyright (c) 2017, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	frappe.reload_doc("stock", "doctype", "product_barcode")
	if frappe.get_all("Product Barcode", limit=1):
		return
	if "barcode" not in frappe.db.get_table_columns("Product"):
		return

	products_barcode = frappe.db.sql(
		"select name, barcode from tabProduct where barcode is not null", as_dict=True
	)
	frappe.reload_doc("stock", "doctype", "product")

	for product in products_barcode:
		barcode = product.barcode.strip()

		if barcode and "<" not in barcode:
			try:
				frappe.get_doc(
					{
						"idx": 0,
						"doctype": "Product Barcode",
						"barcode": barcode,
						"parenttype": "Product",
						"parent": product.name,
						"parentfield": "barcodes",
					}
				).insert()
			except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
				continue
