# Copyright (c) 2019, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	doctypes = [
		"Bank Statement Settings",
		"Bank Statement Settings Product",
		"Bank Statement Transaction Entry",
		"Bank Statement Transaction Invoice Product",
		"Bank Statement Transaction Payment Product",
		"Bank Statement Transaction Settings Product",
		"Bank Statement Transaction Settings",
	]

	for doctype in doctypes:
		frappe.delete_doc("DocType", doctype, force=1)

	frappe.delete_doc("Page", "bank-reconciliation", force=1)

	frappe.reload_doc("accounts", "doctype", "bank_transaction")

	rename_field("Bank Transaction", "debit", "deposit")
	rename_field("Bank Transaction", "credit", "withdrawal")
