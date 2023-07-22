import frappe


def execute():
	"""
	1. Get submitted Work Orders with MR, MR Product and SO set
	2. Get SO Product detail from MR Product detail in WO, and set in WO
	3. Update work_order_qty in SO
	"""
	work_order = frappe.qb.DocType("Work Order")
	query = (
		frappe.qb.from_(work_order)
		.select(
			work_order.name,
			work_order.produced_qty,
			work_order.material_request,
			work_order.material_request_product,
			work_order.sales_order,
		)
		.where(
			(work_order.material_request.isnotnull())
			& (work_order.material_request_product.isnotnull())
			& (work_order.sales_order.isnotnull())
			& (work_order.docstatus == 1)
			& (work_order.produced_qty > 0)
		)
	)
	results = query.run(as_dict=True)

	for row in results:
		so_product = frappe.get_value(
			"Material Request Product", row.material_request_product, "sales_order_product"
		)
		frappe.db.set_value("Work Order", row.name, "sales_order_product", so_product)

		if so_product:
			wo = frappe.get_doc("Work Order", row.name)
			wo.update_work_order_qty_in_so()
