# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.utils import flt


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = True
	context.doc = frappe.get_doc(frappe.form_dict.doctype, frappe.form_dict.name)
	if hasattr(context.doc, "set_indicator"):
		context.doc.set_indicator()

	context.parents = frappe.form_dict.parents
	context.title = frappe.form_dict.name

	if not frappe.has_website_permission(context.doc):
		frappe.throw(_("Not Permitted"), frappe.PermissionError)

	default_print_format = frappe.db.get_value(
		"Property Setter",
		dict(property="default_print_format", doc_type=frappe.form_dict.doctype),
		"value",
	)
	if default_print_format:
		context.print_format = default_print_format
	else:
		context.print_format = "Standard"
	context.doc.products = get_more_products_info(context.doc.products, context.doc.name)


def get_more_products_info(products, material_request):
	for product in products:
		product.customer_provided = frappe.get_value("Product", product.product_code, "is_customer_provided_product")
		product.work_orders = frappe.db.sql(
			"""
			select
				wo.name, wo.status, wo_product.consumed_qty
			from
				`tabWork Order Product` wo_product, `tabWork Order` wo
			where
				wo_product.product_code=%s
				and wo_product.consumed_qty=0
				and wo_product.parent=wo.name
				and wo.status not in ('Completed', 'Cancelled', 'Stopped')
			order by
				wo.name asc""",
			product.product_code,
			as_dict=1,
		)
		product.delivered_qty = flt(
			frappe.db.sql(
				"""select sum(transfer_qty)
						from `tabStock Entry Detail` where material_request = %s
						and product_code = %s and docstatus = 1""",
				(material_request, product.product_code),
			)[0][0]
		)
	return products
