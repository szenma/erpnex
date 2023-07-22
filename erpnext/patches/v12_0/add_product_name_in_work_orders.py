import frappe


def execute():
	frappe.reload_doc("manufacturing", "doctype", "work_order")

	frappe.db.sql(
		"""
		UPDATE
			`tabWork Order` wo
				JOIN `tabProduct` product ON wo.production_product = product.product_code
		SET
			wo.product_name = product.product_name
	"""
	)
	frappe.db.commit()
