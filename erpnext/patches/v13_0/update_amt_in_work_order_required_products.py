import frappe


def execute():
	"""Correct amount in child table of required products table."""

	frappe.reload_doc("manufacturing", "doctype", "work_order")
	frappe.reload_doc("manufacturing", "doctype", "work_order_product")

	frappe.db.sql(
		"""UPDATE `tabWork Order Product` SET amount = ifnull(rate, 0.0) * ifnull(required_qty, 0.0)"""
	)
