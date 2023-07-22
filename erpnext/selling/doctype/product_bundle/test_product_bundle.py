# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe

test_records = frappe.get_test_records("Product Bundle")


def make_product_bundle(parent, products, qty=None):
	if frappe.db.exists("Product Bundle", parent):
		return frappe.get_doc("Product Bundle", parent)

	product_bundle = frappe.get_doc({"doctype": "Product Bundle", "new_product_code": parent})

	for product in products:
		product_bundle.append("products", {"product_code": product, "qty": qty or 1})

	product_bundle.insert()

	return product_bundle
