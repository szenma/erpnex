# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import Floor, Sum
from frappe.utils import cint
from pypika.terms import ExistsCriterion


def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns()
	data = get_bom_stock(filters)

	return columns, data


def get_columns():
	"""return columns"""
	columns = [
		_("Product") + ":Link/Product:150",
		_("Description") + "::300",
		_("BOM Qty") + ":Float:160",
		_("BOM UoM") + "::160",
		_("Required Qty") + ":Float:120",
		_("In Stock Qty") + ":Float:120",
		_("Enough Parts to Build") + ":Float:200",
	]

	return columns


def get_bom_stock(filters):
	qty_to_produce = filters.get("qty_to_produce")
	if cint(qty_to_produce) <= 0:
		frappe.throw(_("Quantity to Produce should be greater than zero."))

	if filters.get("show_exploded_view"):
		bom_product_table = "BOM Explosion Product"
	else:
		bom_product_table = "BOM Product"

	warehouse_details = frappe.db.get_value(
		"Warehouse", filters.get("warehouse"), ["lft", "rgt"], as_dict=1
	)

	BOM = frappe.qb.DocType("BOM")
	BOM_PRODUCT = frappe.qb.DocType(bom_product_table)
	BIN = frappe.qb.DocType("Bin")
	WH = frappe.qb.DocType("Warehouse")
	CONDITIONS = ()

	if warehouse_details:
		CONDITIONS = ExistsCriterion(
			frappe.qb.from_(WH)
			.select(WH.name)
			.where(
				(WH.lft >= warehouse_details.lft)
				& (WH.rgt <= warehouse_details.rgt)
				& (BIN.warehouse == WH.name)
			)
		)
	else:
		CONDITIONS = BIN.warehouse == filters.get("warehouse")

	QUERY = (
		frappe.qb.from_(BOM)
		.inner_join(BOM_PRODUCT)
		.on(BOM.name == BOM_PRODUCT.parent)
		.left_join(BIN)
		.on((BOM_PRODUCT.product_code == BIN.product_code) & (CONDITIONS))
		.select(
			BOM_PRODUCT.product_code,
			BOM_PRODUCT.description,
			BOM_PRODUCT.stock_qty,
			BOM_PRODUCT.stock_uom,
			BOM_PRODUCT.stock_qty * qty_to_produce / BOM.quantity,
			Sum(BIN.actual_qty).as_("actual_qty"),
			Sum(Floor(BIN.actual_qty / (BOM_PRODUCT.stock_qty * qty_to_produce / BOM.quantity))),
		)
		.where((BOM_PRODUCT.parent == filters.get("bom")) & (BOM_PRODUCT.parenttype == "BOM"))
		.groupby(BOM_PRODUCT.product_code)
	)

	return QUERY.run()
