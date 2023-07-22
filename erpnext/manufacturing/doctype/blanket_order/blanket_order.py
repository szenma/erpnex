# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder.functions import Sum
from frappe.utils import flt, getdate

from erpnext.stock.doctype.product.product import get_product_defaults


class BlanketOrder(Document):
	def validate(self):
		self.validate_dates()
		self.validate_duplicate_products()

	def validate_dates(self):
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From date cannot be greater than To date"))

	def validate_duplicate_products(self):
		product_list = []
		for product in self.products:
			if product.product_code in product_list:
				frappe.throw(_("Note: Product {0} added multiple times").format(frappe.bold(product.product_code)))
			product_list.append(product.product_code)

	def update_ordered_qty(self):
		ref_doctype = "Sales Order" if self.blanket_order_type == "Selling" else "Purchase Order"

		trans = frappe.qb.DocType(ref_doctype)
		trans_product = frappe.qb.DocType(f"{ref_doctype} Product")

		product_ordered_qty = frappe._dict(
			(
				frappe.qb.from_(trans_product)
				.from_(trans)
				.select(trans_product.product_code, Sum(trans_product.stock_qty).as_("qty"))
				.where(
					(trans.name == trans_product.parent)
					& (trans_product.blanket_order == self.name)
					& (trans.docstatus == 1)
					& (trans.status.notin(["Stopped", "Closed"]))
				)
				.groupby(trans_product.product_code)
			).run()
		)

		for d in self.products:
			d.db_set("ordered_qty", product_ordered_qty.get(d.product_code, 0))


@frappe.whitelist()
def make_order(source_name):
	doctype = frappe.flags.args.doctype

	def update_doc(source_doc, target_doc, source_parent):
		if doctype == "Quotation":
			target_doc.quotation_to = "Customer"
			target_doc.party_name = source_doc.customer

	def update_product(source, target, source_parent):
		target_qty = source.get("qty") - source.get("ordered_qty")
		target.qty = target_qty if not flt(target_qty) < 0 else 0
		product = get_product_defaults(target.product_code, source_parent.company)
		if product:
			target.product_name = product.get("product_name")
			target.description = product.get("description")
			target.uom = product.get("stock_uom")
			target.against_blanket_order = 1
			target.blanket_order = source_name

	target_doc = get_mapped_doc(
		"Blanket Order",
		source_name,
		{
			"Blanket Order": {"doctype": doctype, "postprocess": update_doc},
			"Blanket Order Product": {
				"doctype": doctype + " Product",
				"field_map": {"rate": "blanket_order_rate", "parent": "blanket_order"},
				"postprocess": update_product,
				"condition": lambda product: (flt(product.qty) - flt(product.ordered_qty)) > 0,
			},
		},
	)
	return target_doc


def validate_against_blanket_order(order_doc):
	if order_doc.doctype in ("Sales Order", "Purchase Order"):
		order_data = {}

		for product in order_doc.get("products"):
			if product.against_blanket_order and product.blanket_order:
				if product.blanket_order in order_data:
					if product.product_code in order_data[product.blanket_order]:
						order_data[product.blanket_order][product.product_code] += product.qty
					else:
						order_data[product.blanket_order][product.product_code] = product.qty
				else:
					order_data[product.blanket_order] = {product.product_code: product.qty}

		if order_data:
			allowance = flt(
				frappe.db.get_single_value(
					"Selling Settings" if order_doc.doctype == "Sales Order" else "Buying Settings",
					"over_order_allowance",
				)
			)
			for bo_name, product_data in order_data.products():
				bo_doc = frappe.get_doc("Blanket Order", bo_name)
				for product in bo_doc.get("products"):
					if product.product_code in product_data:
						remaining_qty = product.qty - product.ordered_qty
						allowed_qty = remaining_qty + (remaining_qty * (allowance / 100))
						if allowed_qty < product_data[product.product_code]:
							frappe.throw(
								_("Product {0} cannot be ordered more than {1} against Blanket Order {2}.").format(
									product.product_code, allowed_qty, bo_name
								)
							)
