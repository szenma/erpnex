import frappe

from erpnext.selling.doctype.sales_order.sales_order import update_produced_qty_in_so_product


def execute():
	frappe.reload_doctype("Sales Order Product")
	frappe.reload_doctype("Sales Order")

	for d in frappe.get_all(
		"Work Order",
		fields=["sales_order", "sales_order_product"],
		filters={"sales_order": ("!=", ""), "sales_order_product": ("!=", "")},
	):

		# update produced qty in sales order
		update_produced_qty_in_so_product(d.sales_order, d.sales_order_product)
