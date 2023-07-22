# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import IfNull, Sum
from frappe.utils.data import comma_and
from pypika.terms import ExistsCriterion


def execute(filters=None):
	columns = get_columns()
	data = []

	bom_data = get_bom_data(filters)
	qty_to_make = filters.get("qty_to_make")
	manufacture_details = get_manufacturer_records()

	for row in bom_data:
		required_qty = qty_to_make * row.qty_per_unit
		last_purchase_rate = frappe.db.get_value("Product", row.product_code, "last_purchase_rate")

		data.append(get_report_data(last_purchase_rate, required_qty, row, manufacture_details))

	return columns, data


def get_report_data(last_purchase_rate, required_qty, row, manufacture_details):
	qty_per_unit = row.qty_per_unit if row.qty_per_unit > 0 else 0
	difference_qty = row.actual_qty - required_qty
	return [
		row.product_code,
		row.description,
		comma_and(manufacture_details.get(row.product_code, {}).get("manufacturer", []), add_quotes=False),
		comma_and(
			manufacture_details.get(row.product_code, {}).get("manufacturer_part", []), add_quotes=False
		),
		qty_per_unit,
		row.actual_qty,
		required_qty,
		difference_qty,
		last_purchase_rate,
	]


def get_columns():
	return [
		{
			"fieldname": "product",
			"label": _("Product"),
			"fieldtype": "Link",
			"options": "Product",
			"width": 120,
		},
		{
			"fieldname": "description",
			"label": _("Description"),
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"fieldname": "manufacturer",
			"label": _("Manufacturer"),
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"fieldname": "manufacturer_part_number",
			"label": _("Manufacturer Part Number"),
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"fieldname": "qty_per_unit",
			"label": _("Qty Per Unit"),
			"fieldtype": "Float",
			"width": 110,
		},
		{
			"fieldname": "available_qty",
			"label": _("Available Qty"),
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"fieldname": "required_qty",
			"label": _("Required Qty"),
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"fieldname": "difference_qty",
			"label": _("Difference Qty"),
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"fieldname": "last_purchase_rate",
			"label": _("Last Purchase Rate"),
			"fieldtype": "Float",
			"width": 160,
		},
	]


def get_bom_data(filters):
	if filters.get("show_exploded_view"):
		bom_product_table = "BOM Explosion Product"
	else:
		bom_product_table = "BOM Product"

	bom_product = frappe.qb.DocType(bom_product_table)
	bin = frappe.qb.DocType("Bin")

	query = (
		frappe.qb.from_(bom_product)
		.left_join(bin)
		.on(bom_product.product_code == bin.product_code)
		.select(
			bom_product.product_code,
			bom_product.description,
			bom_product.qty_consumed_per_unit.as_("qty_per_unit"),
			IfNull(Sum(bin.actual_qty), 0).as_("actual_qty"),
		)
		.where((bom_product.parent == filters.get("bom")) & (bom_product.parenttype == "BOM"))
		.groupby(bom_product.product_code)
	)

	if filters.get("warehouse"):
		warehouse_details = frappe.db.get_value(
			"Warehouse", filters.get("warehouse"), ["lft", "rgt"], as_dict=1
		)

		if warehouse_details:
			wh = frappe.qb.DocType("Warehouse")
			query = query.where(
				ExistsCriterion(
					frappe.qb.from_(wh)
					.select(wh.name)
					.where(
						(wh.lft >= warehouse_details.lft)
						& (wh.rgt <= warehouse_details.rgt)
						& (bin.warehouse == wh.name)
					)
				)
			)
		else:
			query = query.where(bin.warehouse == filters.get("warehouse"))

	return query.run(as_dict=True)


def get_manufacturer_records():
	details = frappe.get_all(
		"Product Manufacturer", fields=["manufacturer", "manufacturer_part_no", "product_code"]
	)

	manufacture_details = frappe._dict()
	for detail in details:
		dic = manufacture_details.setdefault(detail.get("product_code"), {})
		dic.setdefault("manufacturer", []).append(detail.get("manufacturer"))
		dic.setdefault("manufacturer_part", []).append(detail.get("manufacturer_part_no"))

	return manufacture_details
