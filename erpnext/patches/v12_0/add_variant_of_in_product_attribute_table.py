import frappe


def execute():
	frappe.reload_doc("stock", "doctype", "product_variant_attribute")
	frappe.db.sql(
		"""
		UPDATE `tabProduct Variant Attribute` t1
		INNER JOIN `tabProduct` t2 ON t2.name = t1.parent
		SET t1.variant_of = t2.variant_of
	"""
	)
