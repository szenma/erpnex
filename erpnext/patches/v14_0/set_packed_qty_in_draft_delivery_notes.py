# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.query_builder.functions import Sum


def execute():
	ps = frappe.qb.DocType("Packing Slip")
	dn = frappe.qb.DocType("Delivery Note")
	ps_product = frappe.qb.DocType("Packing Slip Product")

	ps_details = (
		frappe.qb.from_(ps)
		.join(ps_product)
		.on(ps.name == ps_product.parent)
		.join(dn)
		.on(ps.delivery_note == dn.name)
		.select(
			dn.name.as_("delivery_note"),
			ps_product.product_code.as_("product_code"),
			Sum(ps_product.qty).as_("packed_qty"),
		)
		.where((ps.docstatus == 1) & (dn.docstatus == 0))
		.groupby(dn.name, ps_product.product_code)
	).run(as_dict=True)

	if ps_details:
		dn_list = set()
		product_code_list = set()
		for ps_detail in ps_details:
			dn_list.add(ps_detail.delivery_note)
			product_code_list.add(ps_detail.product_code)

		dn_product = frappe.qb.DocType("Delivery Note Product")
		dn_product_query = (
			frappe.qb.from_(dn_product)
			.select(
				dn.parent.as_("delivery_note"),
				dn_product.name,
				dn_product.product_code,
				dn_product.qty,
			)
			.where((dn_product.parent.isin(dn_list)) & (dn_product.product_code.isin(product_code_list)))
		)

		dn_details = frappe._dict()
		for r in dn_product_query.run(as_dict=True):
			dn_details.setdefault((r.delivery_note, r.product_code), frappe._dict()).setdefault(r.name, r.qty)

		for ps_detail in ps_details:
			dn_products = dn_details.get((ps_detail.delivery_note, ps_detail.product_code))

			if dn_products:
				remaining_qty = ps_detail.packed_qty
				for name, qty in dn_products.products():
					if remaining_qty > 0:
						row_packed_qty = min(qty, remaining_qty)
						frappe.db.set_value("Delivery Note Product", name, "packed_qty", row_packed_qty)
						remaining_qty -= row_packed_qty
