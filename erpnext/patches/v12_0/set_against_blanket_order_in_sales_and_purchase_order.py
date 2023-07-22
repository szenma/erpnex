import frappe


def execute():

	frappe.reload_doc("selling", "doctype", "sales_order_product", force=True)
	frappe.reload_doc("buying", "doctype", "purchase_order_product", force=True)

	for doctype in ("Sales Order Product", "Purchase Order Product"):
		frappe.db.sql(
			"""
			UPDATE `tab{0}`
			SET against_blanket_order = 1
			WHERE ifnull(blanket_order, '') != ''
		""".format(
				doctype
			)
		)
