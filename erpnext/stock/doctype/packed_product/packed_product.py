# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

# For license information, please see license.txt


import json

import frappe
from frappe.model.document import Document
from frappe.utils import flt

from erpnext.stock.get_product_details import get_product_details, get_price_list_rate


class PackedProduct(Document):
	pass


def make_packing_list(doc):
	"Make/Update packing list for Product Bundle Product."
	if doc.get("_action") and doc._action == "update_after_submit":
		return

	parent_products_price, reset = {}, False
	set_price_from_children = frappe.db.get_single_value(
		"Selling Settings", "editable_bundle_product_rates"
	)

	stale_packed_products_table = get_indexed_packed_products_table(doc)

	reset = reset_packing_list(doc)

	for product_row in doc.get("products"):
		if is_product_bundle(product_row.product_code):
			for bundle_product in get_product_bundle_products(product_row.product_code):
				pi_row = add_packed_product_row(
					doc=doc,
					packing_product=bundle_product,
					main_product_row=product_row,
					packed_products_table=stale_packed_products_table,
					reset=reset,
				)
				product_data = get_packed_product_details(bundle_product.product_code, doc.company)
				update_packed_product_basic_data(product_row, pi_row, bundle_product, product_data)
				update_packed_product_stock_data(product_row, pi_row, bundle_product, product_data, doc)
				update_packed_product_price_data(pi_row, product_data, doc)
				update_packed_product_from_cancelled_doc(product_row, bundle_product, pi_row, doc)

				if set_price_from_children:  # create/update bundle product wise price dict
					update_product_bundle_rate(parent_products_price, pi_row, product_row)

	if parent_products_price:
		set_product_bundle_rate_amount(doc, parent_products_price)  # set price in bundle product


def is_product_bundle(product_code: str) -> bool:
	return bool(frappe.db.exists("Product Bundle", {"new_product_code": product_code}))


def get_indexed_packed_products_table(doc):
	"""
	Create dict from stale packed products table like:
	{(Parent Product 1, Bundle Product 1, ae4b5678): {...}, (key): {value}}

	Use: to quickly retrieve/check if row existed in table instead of looping n times
	"""
	indexed_table = {}
	for packed_product in doc.get("packed_products"):
		key = (packed_product.parent_product, packed_product.product_code, packed_product.parent_detail_docname)
		indexed_table[key] = packed_product

	return indexed_table


def reset_packing_list(doc):
	"Conditionally reset the table and return if it was reset or not."
	reset_table = False
	doc_before_save = doc.get_doc_before_save()

	if doc_before_save:
		# reset table if:
		# 1. products were deleted
		# 2. if bundle product replaced by another product (same no. of products but different products)
		# we maintain list to track recurring product rows as well
		products_before_save = [(product.name, product.product_code) for product in doc_before_save.get("products")]
		products_after_save = [(product.name, product.product_code) for product in doc.get("products")]
		reset_table = products_before_save != products_after_save
	else:
		# reset: if via Update Products OR
		# if new mapped doc with packed products set (SO -> DN)
		# (cannot determine action)
		reset_table = True

	if reset_table:
		doc.set("packed_products", [])
	return reset_table


def get_product_bundle_products(product_code):
	product_bundle = frappe.qb.DocType("Product Bundle")
	product_bundle_product = frappe.qb.DocType("Product Bundle Product")

	query = (
		frappe.qb.from_(product_bundle_product)
		.join(product_bundle)
		.on(product_bundle_product.parent == product_bundle.name)
		.select(
			product_bundle_product.product_code,
			product_bundle_product.qty,
			product_bundle_product.uom,
			product_bundle_product.description,
		)
		.where(product_bundle.new_product_code == product_code)
		.orderby(product_bundle_product.idx)
	)
	return query.run(as_dict=True)


def add_packed_product_row(doc, packing_product, main_product_row, packed_products_table, reset):
	"""Add and return packed product row.
	doc: Transaction document
	packing_product (dict): Packed Product details
	main_product_row (dict): Products table row corresponding to packed product
	packed_products_table (dict): Packed Products table before save (indexed)
	reset (bool): State if table is reset or preserved as is
	"""
	exists, pi_row = False, {}

	# check if row already exists in packed products table
	key = (main_product_row.product_code, packing_product.product_code, main_product_row.name)
	if packed_products_table.get(key):
		pi_row, exists = packed_products_table.get(key), True

	if not exists:
		pi_row = doc.append("packed_products", {})
	elif reset:  # add row if row exists but table is reset
		pi_row.idx, pi_row.name = None, None
		pi_row = doc.append("packed_products", pi_row)

	return pi_row


def get_packed_product_details(product_code, company):
	product = frappe.qb.DocType("Product")
	product_default = frappe.qb.DocType("Product Default")
	query = (
		frappe.qb.from_(product)
		.left_join(product_default)
		.on((product_default.parent == product.name) & (product_default.company == company))
		.select(
			product.product_name,
			product.is_stock_product,
			product.description,
			product.stock_uom,
			product.valuation_rate,
			product_default.default_warehouse,
		)
		.where(product.name == product_code)
	)
	return query.run(as_dict=True)[0]


def update_packed_product_basic_data(main_product_row, pi_row, packing_product, product_data):
	pi_row.parent_product = main_product_row.product_code
	pi_row.parent_detail_docname = main_product_row.name
	pi_row.product_code = packing_product.product_code
	pi_row.product_name = product_data.product_name
	pi_row.uom = product_data.stock_uom
	pi_row.qty = flt(packing_product.qty) * flt(main_product_row.stock_qty)
	pi_row.conversion_factor = main_product_row.conversion_factor

	if not pi_row.description:
		pi_row.description = packing_product.get("description")


def update_packed_product_stock_data(main_product_row, pi_row, packing_product, product_data, doc):
	# TODO batch_no, actual_batch_qty, incoming_rate
	if not pi_row.warehouse and not doc.amended_from:
		fetch_warehouse = doc.get("is_pos") or product_data.is_stock_product or not product_data.default_warehouse
		pi_row.warehouse = (
			main_product_row.warehouse
			if (fetch_warehouse and main_product_row.warehouse)
			else product_data.default_warehouse
		)

	if not pi_row.target_warehouse:
		pi_row.target_warehouse = main_product_row.get("target_warehouse")

	bin = get_packed_product_bin_qty(packing_product.product_code, pi_row.warehouse)
	pi_row.actual_qty = flt(bin.get("actual_qty"))
	pi_row.projected_qty = flt(bin.get("projected_qty"))


def update_packed_product_price_data(pi_row, product_data, doc):
	"Set price as per price list or from the Product master."
	if pi_row.rate:
		return

	product_doc = frappe.get_cached_doc("Product", pi_row.product_code)
	row_data = pi_row.as_dict().copy()
	row_data.update(
		{
			"company": doc.get("company"),
			"price_list": doc.get("selling_price_list"),
			"currency": doc.get("currency"),
			"conversion_rate": doc.get("conversion_rate"),
		}
	)
	rate = get_price_list_rate(row_data, product_doc).get("price_list_rate")

	pi_row.rate = rate or product_data.get("valuation_rate") or 0.0


def update_packed_product_from_cancelled_doc(main_product_row, packing_product, pi_row, doc):
	"Update packed product row details from cancelled doc into amended doc."
	prev_doc_packed_products_map = None
	if doc.amended_from:
		prev_doc_packed_products_map = get_cancelled_doc_packed_product_details(doc.packed_products)

	if prev_doc_packed_products_map and prev_doc_packed_products_map.get(
		(packing_product.product_code, main_product_row.product_code)
	):
		prev_doc_row = prev_doc_packed_products_map.get((packing_product.product_code, main_product_row.product_code))
		pi_row.batch_no = prev_doc_row[0].batch_no
		pi_row.serial_no = prev_doc_row[0].serial_no
		pi_row.warehouse = prev_doc_row[0].warehouse


def get_packed_product_bin_qty(product, warehouse):
	bin_data = frappe.db.get_values(
		"Bin",
		fieldname=["actual_qty", "projected_qty"],
		filters={"product_code": product, "warehouse": warehouse},
		as_dict=True,
	)

	return bin_data[0] if bin_data else {}


def get_cancelled_doc_packed_product_details(old_packed_products):
	prev_doc_packed_products_map = {}
	for products in old_packed_products:
		prev_doc_packed_products_map.setdefault((products.product_code, products.parent_product), []).append(
			products.as_dict()
		)
	return prev_doc_packed_products_map


def update_product_bundle_rate(parent_products_price, pi_row, product_row):
	"""
	Update the price dict of Product Bundles based on the rates of the Products in the bundle.

	Stucture:
	{(Bundle Product 1, ae56fgji): 150.0, (Bundle Product 2, bc78fkjo): 200.0}
	"""
	key = (pi_row.parent_product, pi_row.parent_detail_docname)
	rate = parent_products_price.get(key)
	if not rate:
		parent_products_price[key] = 0.0

	parent_products_price[key] += flt((pi_row.rate * pi_row.qty) / product_row.stock_qty)


def set_product_bundle_rate_amount(doc, parent_products_price):
	"Set cumulative rate and amount in bundle product."
	for product in doc.get("products"):
		bundle_rate = parent_products_price.get((product.product_code, product.name))
		if bundle_rate and bundle_rate != product.rate:
			product.rate = bundle_rate
			product.amount = flt(bundle_rate * product.qty)


def on_doctype_update():
	frappe.db.add_index("Packed Product", ["product_code", "warehouse"])


@frappe.whitelist()
def get_products_from_product_bundle(row):
	row, products = json.loads(row), []

	bundled_products = get_product_bundle_products(row["product_code"])
	for product in bundled_products:
		row.update({"product_code": product.product_code, "qty": flt(row["quantity"]) * flt(product.qty)})
		products.append(get_product_details(row))

	return products
