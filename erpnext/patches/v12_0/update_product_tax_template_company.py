import frappe


def execute():
	frappe.reload_doc("accounts", "doctype", "product_tax_template")

	product_tax_template_list = frappe.get_list("Product Tax Template")
	for template in product_tax_template_list:
		doc = frappe.get_doc("Product Tax Template", template.name)
		for tax in doc.taxes:
			doc.company = frappe.get_value("Account", tax.tax_type, "company")
			break
		doc.save()
