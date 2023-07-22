import frappe


def execute():
	homepage = frappe.get_doc("Homepage")

	for row in homepage.products:
		web_product = frappe.db.get_value("Website Product", {"product_code": row.product_code}, "name")
		if not web_product:
			continue

		row.product_code = web_product

	homepage.flags.ignore_mandatory = True
	homepage.save()
