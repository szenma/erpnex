import frappe


def execute():
	frappe.reload_doc("stock", "doctype", "material_request_product")
	frappe.reload_doc("selling", "doctype", "sales_order_product")

	for doctype in ["Sales Order", "Material Request"]:
		condition = " and child_doc.stock_qty > child_doc.produced_qty and doc.per_delivered < 100"
		if doctype == "Material Request":
			condition = " and doc.per_ordered < 100 and doc.material_request_type = 'Manufacture'"

		frappe.db.sql(
			""" UPDATE `tab{doc}` as doc, `tab{doc} Product` as child_doc, tabProduct as product
			SET
				child_doc.bom_no = product.default_bom
			WHERE
				child_doc.product_code = product.name and child_doc.docstatus < 2
				and child_doc.parent = doc.name
				and product.default_bom is not null and product.default_bom != '' {cond}
		""".format(
				doc=doctype, cond=condition
			)
		)
