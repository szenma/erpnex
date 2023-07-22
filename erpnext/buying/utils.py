# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json
from typing import Dict

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate

from erpnext.stock.doctype.product.product import get_last_purchase_details, validate_end_of_life


def update_last_purchase_rate(doc, is_submit) -> None:
	"""updates last_purchase_rate in product table for each product"""

	this_purchase_date = getdate(doc.get("posting_date") or doc.get("transaction_date"))

	for d in doc.get("products"):
		# get last purchase details
		last_purchase_details = get_last_purchase_details(d.product_code, doc.name)

		# compare last purchase date and this transaction's date
		last_purchase_rate = None
		if last_purchase_details and (
			doc.get("docstatus") == 2 or last_purchase_details.purchase_date > this_purchase_date
		):
			last_purchase_rate = last_purchase_details["base_net_rate"]
		elif is_submit == 1:
			# even if this transaction is the latest one, it should be submitted
			# for it to be considered for latest purchase rate
			if flt(d.conversion_factor):
				last_purchase_rate = flt(d.base_net_rate) / flt(d.conversion_factor)
			# Check if product code is present
			# Conversion factor should not be mandatory for non productized products
			elif d.product_code:
				frappe.throw(_("UOM Conversion factor is required in row {0}").format(d.idx))

		# update last purchsae rate
		frappe.db.set_value("Product", d.product_code, "last_purchase_rate", flt(last_purchase_rate))


def validate_for_products(doc) -> None:
	products = []
	for d in doc.get("products"):
		if not d.qty:
			if doc.doctype == "Purchase Receipt" and d.rejected_qty:
				continue
			frappe.throw(_("Please enter quantity for Product {0}").format(d.product_code))

		set_stock_levels(row=d)  # update with latest quantities
		product = validate_product_and_get_basic_data(row=d)
		validate_stock_product_warehouse(row=d, product=product)
		validate_end_of_life(d.product_code, product.end_of_life, product.disabled)

		products.append(cstr(d.product_code))

	if (
		products
		and len(products) != len(set(products))
		and not cint(frappe.db.get_single_value("Buying Settings", "allow_multiple_products") or 0)
	):
		frappe.throw(_("Same product cannot be entered multiple times."))


def set_stock_levels(row) -> None:
	projected_qty = frappe.db.get_value(
		"Bin",
		{
			"product_code": row.product_code,
			"warehouse": row.warehouse,
		},
		"projected_qty",
	)

	qty_data = {
		"projected_qty": flt(projected_qty),
		"ordered_qty": 0,
		"received_qty": 0,
	}
	if row.doctype in ("Purchase Receipt Product", "Purchase Invoice Product"):
		qty_data.pop("received_qty")

	for field in qty_data:
		if row.meta.get_field(field):
			row.set(field, qty_data[field])


def validate_product_and_get_basic_data(row) -> Dict:
	product = frappe.db.get_values(
		"Product",
		filters={"name": row.product_code},
		fieldname=["is_stock_product", "is_sub_contracted_product", "end_of_life", "disabled"],
		as_dict=1,
	)
	if not product:
		frappe.throw(_("Row #{0}: Product {1} does not exist").format(row.idx, frappe.bold(row.product_code)))

	return product[0]


def validate_stock_product_warehouse(row, product) -> None:
	if (
		product.is_stock_product == 1
		and row.qty
		and not row.warehouse
		and not row.get("delivered_by_supplier")
	):
		frappe.throw(
			_("Row #{1}: Warehouse is mandatory for stock Product {0}").format(
				frappe.bold(row.product_code), row.idx
			)
		)


def check_on_hold_or_closed_status(doctype, docname) -> None:
	status = frappe.db.get_value(doctype, docname, "status")

	if status in ("Closed", "On Hold"):
		frappe.throw(
			_("{0} {1} status is {2}").format(doctype, docname, status), frappe.InvalidStatusError
		)


@frappe.whitelist()
def get_linked_material_requests(products):
	products = json.loads(products)
	mr_list = []
	for product in products:
		material_request = frappe.db.sql(
			"""SELECT distinct mr.name AS mr_name,
				(mr_product.qty - mr_product.ordered_qty) AS qty,
				mr_product.product_code AS product_code,
				mr_product.name AS mr_product
			FROM `tabMaterial Request` mr, `tabMaterial Request Product` mr_product
			WHERE mr.name = mr_product.parent
				AND mr_product.product_code = %(product)s
				AND mr.material_request_type = 'Purchase'
				AND mr.per_ordered < 99.99
				AND mr.docstatus = 1
				AND mr.status != 'Stopped'
                        ORDER BY mr_product.product_code ASC""",
			{"product": product},
			as_dict=1,
		)
		if material_request:
			mr_list.append(material_request)

	return mr_list
