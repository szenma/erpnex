import frappe


def execute():
	"""Remove has_variants and attribute fields from product variant settings."""
	frappe.reload_doc("stock", "doctype", "Product Variant Settings")

	frappe.db.sql(
		"""delete from `tabVariant Field`
			where field_name in ('attributes', 'has_variants')"""
	)
