import frappe


def execute():
	"""
	Remove "production_plan_product" field where linked field doesn't exist in tha table.
	"""

	work_order = frappe.qb.DocType("Work Order")
	pp_product = frappe.qb.DocType("Production Plan Product")

	broken_work_orders = (
		frappe.qb.from_(work_order)
		.left_join(pp_product)
		.on(work_order.production_plan_product == pp_product.name)
		.select(work_order.name)
		.where(
			(work_order.docstatus == 0)
			& (work_order.production_plan_product.notnull())
			& (work_order.production_plan_product.like("new-production-plan%"))
			& (pp_product.name.isnull())
		)
	).run(pluck=True)

	if not broken_work_orders:
		return

	(
		frappe.qb.update(work_order)
		.set(work_order.production_plan_product, None)
		.where(work_order.name.isin(broken_work_orders))
	).run()
