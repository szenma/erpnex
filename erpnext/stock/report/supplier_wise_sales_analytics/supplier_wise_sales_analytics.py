# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import IfNull
from frappe.utils import flt


def execute(filters=None):
	columns = get_columns(filters)
	consumed_details = get_consumed_details(filters)
	supplier_details = get_suppliers_details(filters)
	material_transfer_vouchers = get_material_transfer_vouchers()
	data = []

	for product_code, suppliers in supplier_details.products():
		consumed_qty = consumed_amount = delivered_qty = delivered_amount = 0.0
		total_qty = total_amount = 0.0
		if consumed_details.get(product_code):
			for cd in consumed_details.get(product_code):

				if cd.voucher_no not in material_transfer_vouchers:
					if cd.voucher_type in ["Delivery Note", "Sales Invoice"]:
						delivered_qty += abs(flt(cd.actual_qty))
						delivered_amount += abs(flt(cd.stock_value_difference))
					elif cd.voucher_type != "Delivery Note":
						consumed_qty += abs(flt(cd.actual_qty))
						consumed_amount += abs(flt(cd.stock_value_difference))

			if consumed_qty or consumed_amount or delivered_qty or delivered_amount:
				total_qty += delivered_qty + consumed_qty
				total_amount += delivered_amount + consumed_amount

				row = [
					cd.product_code,
					cd.product_name,
					cd.description,
					cd.stock_uom,
					consumed_qty,
					consumed_amount,
					delivered_qty,
					delivered_amount,
					total_qty,
					total_amount,
					",".join(list(set(suppliers))),
				]
				data.append(row)

	return columns, data


def get_columns(filters):
	"""return columns based on filters"""

	columns = (
		[_("Product") + ":Link/Product:100"]
		+ [_("Product Name") + "::100"]
		+ [_("Description") + "::150"]
		+ [_("UOM") + ":Link/UOM:90"]
		+ [_("Consumed Qty") + ":Float:110"]
		+ [_("Consumed Amount") + ":Currency:130"]
		+ [_("Delivered Qty") + ":Float:110"]
		+ [_("Delivered Amount") + ":Currency:130"]
		+ [_("Total Qty") + ":Float:110"]
		+ [_("Total Amount") + ":Currency:130"]
		+ [_("Supplier(s)") + "::250"]
	)

	return columns


def get_consumed_details(filters):
	product = frappe.qb.DocType("Product")
	sle = frappe.qb.DocType("Stock Ledger Entry")

	query = (
		frappe.qb.from_(sle)
		.from_(product)
		.select(
			sle.product_code,
			product.product_name,
			product.description,
			product.stock_uom,
			sle.actual_qty,
			sle.stock_value_difference,
			sle.voucher_no,
			sle.voucher_type,
		)
		.where((sle.is_cancelled == 0) & (sle.product_code == product.name) & (sle.actual_qty < 0))
	)

	if filters.get("from_date") and filters.get("to_date"):
		query = query.where(
			(sle.posting_date >= filters.get("from_date")) & (sle.posting_date <= filters.get("to_date"))
		)

	consumed_details = {}
	for d in query.run(as_dict=True):
		consumed_details.setdefault(d.product_code, []).append(d)

	return consumed_details


def get_suppliers_details(filters):
	product_supplier_map = {}
	supplier = filters.get("supplier")

	product = frappe.qb.DocType("Product")
	pr = frappe.qb.DocType("Purchase Receipt")
	pr_product = frappe.qb.DocType("Purchase Receipt Product")

	query = (
		frappe.qb.from_(pr)
		.from_(pr_product)
		.select(pr.supplier, pr_product.product_code)
		.where(
			(pr.name == pr_product.parent)
			& (pr.docstatus == 1)
			& (
				pr_product.product_code
				== (
					frappe.qb.from_(product)
					.select(product.name)
					.where((product.is_stock_product == 1) & (product.name == pr_product.product_code))
				)
			)
		)
	)

	for d in query.run(as_dict=True):
		product_supplier_map.setdefault(d.product_code, []).append(d.supplier)

	pi = frappe.qb.DocType("Purchase Invoice")
	pi_product = frappe.qb.DocType("Purchase Invoice Product")

	query = (
		frappe.qb.from_(pi)
		.from_(pi_product)
		.select(pi.supplier, pi_product.product_code)
		.where(
			(pi.name == pi_product.parent)
			& (pi.docstatus == 1)
			& (IfNull(pi.update_stock, 0) == 1)
			& (
				pi_product.product_code
				== (
					frappe.qb.from_(product)
					.select(product.name)
					.where((product.is_stock_product == 1) & (product.name == pi_product.product_code))
				)
			)
		)
	)

	for d in query.run(as_dict=True):
		if d.product_code not in product_supplier_map:
			product_supplier_map.setdefault(d.product_code, []).append(d.supplier)

	if supplier:
		invalid_products = []
		for product_code, suppliers in product_supplier_map.products():
			if supplier not in suppliers:
				invalid_products.append(product_code)

		for product_code in invalid_products:
			del product_supplier_map[product_code]

	return product_supplier_map


def get_material_transfer_vouchers():
	se = frappe.qb.DocType("Stock Entry")
	query = (
		frappe.qb.from_(se)
		.select(se.name)
		.where((se.purpose == "Material Transfer") & (se.docstatus == 1))
	)

	return [r[0] for r in query.run()]
