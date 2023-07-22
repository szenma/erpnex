import frappe


def execute():

	doctype = "Stock Reconciliation Product"

	if not frappe.db.has_column(doctype, "current_serial_no"):
		# nothing to fix if column doesn't exist
		return

	sr_product = frappe.qb.DocType(doctype)

	(
		frappe.qb.update(sr_product).set(sr_product.current_serial_no, None).where(sr_product.current_qty == 0)
	).run()
