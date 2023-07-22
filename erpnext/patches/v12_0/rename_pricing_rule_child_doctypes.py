# Copyright (c) 2017, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe

doctypes = {
	"Price Discount Slab": "Promotional Scheme Price Discount",
	"Product Discount Slab": "Promotional Scheme Product Discount",
	"Apply Rule On Product Code": "Pricing Rule Product Code",
	"Apply Rule On Product Group": "Pricing Rule Product Group",
	"Apply Rule On Brand": "Pricing Rule Brand",
}


def execute():
	for old_doc, new_doc in doctypes.products():
		if not frappe.db.table_exists(new_doc) and frappe.db.table_exists(old_doc):
			frappe.rename_doc("DocType", old_doc, new_doc)
			frappe.reload_doc("accounts", "doctype", frappe.scrub(new_doc))
			frappe.delete_doc("DocType", old_doc)
