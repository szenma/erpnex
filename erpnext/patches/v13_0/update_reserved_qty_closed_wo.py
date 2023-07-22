import frappe

from erpnext.stock.utils import get_bin


def execute():

	wo = frappe.qb.DocType("Work Order")
	wo_product = frappe.qb.DocType("Work Order Product")

	incorrect_product_wh = (
		frappe.qb.from_(wo)
		.join(wo_product)
		.on(wo.name == wo_product.parent)
		.select(wo_product.product_code, wo.source_warehouse)
		.distinct()
		.where((wo.status == "Closed") & (wo.docstatus == 1) & (wo.source_warehouse.notnull()))
	).run()

	for product_code, warehouse in incorrect_product_wh:
		if not (product_code and warehouse):
			continue

		bin = get_bin(product_code, warehouse)
		bin.update_reserved_qty_for_production()
