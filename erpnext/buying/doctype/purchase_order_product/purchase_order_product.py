# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe.model.document import Document


class PurchaseOrderProduct(Document):
	pass


def on_doctype_update():
	frappe.db.add_index("Purchase Order Product", ["product_code", "warehouse"])
