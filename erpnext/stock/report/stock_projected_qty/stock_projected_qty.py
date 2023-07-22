# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.utils import flt, today
from pypika.terms import ExistsCriterion

from erpnext.accounts.doctype.pos_invoice.pos_invoice import get_pos_reserved_qty
from erpnext.stock.utils import (
	is_reposting_product_valuation_in_progress,
	update_included_uom_in_report,
)


def execute(filters=None):
	is_reposting_product_valuation_in_progress()
	filters = frappe._dict(filters or {})
	include_uom = filters.get("include_uom")
	columns = get_columns()
	bin_list = get_bin_list(filters)
	product_map = get_product_map(filters.get("product_code"), include_uom)

	warehouse_company = {}
	data = []
	conversion_factors = []
	for bin in bin_list:
		product = product_map.get(bin.product_code)

		if not product:
			# likely an product that has reached its end of life
			continue

		# product = product_map.setdefault(bin.product_code, get_product(bin.product_code))
		company = warehouse_company.setdefault(
			bin.warehouse, frappe.db.get_value("Warehouse", bin.warehouse, "company")
		)

		if filters.brand and filters.brand != product.brand:
			continue

		elif filters.product_group and filters.product_group != product.product_group:
			continue

		elif filters.company and filters.company != company:
			continue

		re_order_level = re_order_qty = 0

		for d in product.get("reorder_levels"):
			if d.warehouse == bin.warehouse:
				re_order_level = d.warehouse_reorder_level
				re_order_qty = d.warehouse_reorder_qty

		shortage_qty = 0
		if (re_order_level or re_order_qty) and re_order_level > bin.projected_qty:
			shortage_qty = re_order_level - flt(bin.projected_qty)

		reserved_qty_for_pos = get_pos_reserved_qty(bin.product_code, bin.warehouse)
		if reserved_qty_for_pos:
			bin.projected_qty -= reserved_qty_for_pos

		data.append(
			[
				product.name,
				product.product_name,
				product.description,
				product.product_group,
				product.brand,
				bin.warehouse,
				product.stock_uom,
				bin.actual_qty,
				bin.planned_qty,
				bin.indented_qty,
				bin.ordered_qty,
				bin.reserved_qty,
				bin.reserved_qty_for_production,
				bin.reserved_qty_for_production_plan,
				bin.reserved_qty_for_sub_contract,
				reserved_qty_for_pos,
				bin.projected_qty,
				re_order_level,
				re_order_qty,
				shortage_qty,
			]
		)

		if include_uom:
			conversion_factors.append(product.conversion_factor)

	update_included_uom_in_report(columns, data, include_uom, conversion_factors)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Product Code"),
			"fieldname": "product_code",
			"fieldtype": "Link",
			"options": "Product",
			"width": 140,
		},
		{"label": _("Product Name"), "fieldname": "product_name", "width": 100},
		{"label": _("Description"), "fieldname": "description", "width": 200},
		{
			"label": _("Product Group"),
			"fieldname": "product_group",
			"fieldtype": "Link",
			"options": "Product Group",
			"width": 100,
		},
		{
			"label": _("Brand"),
			"fieldname": "brand",
			"fieldtype": "Link",
			"options": "Brand",
			"width": 100,
		},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 120,
		},
		{
			"label": _("UOM"),
			"fieldname": "stock_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 100,
		},
		{
			"label": _("Actual Qty"),
			"fieldname": "actual_qty",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Planned Qty"),
			"fieldname": "planned_qty",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Requested Qty"),
			"fieldname": "indented_qty",
			"fieldtype": "Float",
			"width": 110,
			"convertible": "qty",
		},
		{
			"label": _("Ordered Qty"),
			"fieldname": "ordered_qty",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Reserved Qty"),
			"fieldname": "reserved_qty",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Reserved for Production"),
			"fieldname": "reserved_qty_for_production",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Reserved for Production Plan"),
			"fieldname": "reserved_qty_for_production_plan",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Reserved for Sub Contracting"),
			"fieldname": "reserved_qty_for_sub_contract",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Reserved for POS Transactions"),
			"fieldname": "reserved_qty_for_pos",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Projected Qty"),
			"fieldname": "projected_qty",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Reorder Level"),
			"fieldname": "re_order_level",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Reorder Qty"),
			"fieldname": "re_order_qty",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
		{
			"label": _("Shortage Qty"),
			"fieldname": "shortage_qty",
			"fieldtype": "Float",
			"width": 100,
			"convertible": "qty",
		},
	]


def get_bin_list(filters):
	bin = frappe.qb.DocType("Bin")
	query = (
		frappe.qb.from_(bin)
		.select(
			bin.product_code,
			bin.warehouse,
			bin.actual_qty,
			bin.planned_qty,
			bin.indented_qty,
			bin.ordered_qty,
			bin.reserved_qty,
			bin.reserved_qty_for_production,
			bin.reserved_qty_for_sub_contract,
			bin.reserved_qty_for_production_plan,
			bin.projected_qty,
		)
		.orderby(bin.product_code, bin.warehouse)
	)

	if filters.product_code:
		query = query.where(bin.product_code == filters.product_code)

	if filters.warehouse:
		warehouse_details = frappe.db.get_value(
			"Warehouse", filters.warehouse, ["lft", "rgt"], as_dict=1
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

	bin_list = query.run(as_dict=True)

	return bin_list


def get_product_map(product_code, include_uom):
	"""Optimization: get only the product doc and re_order_levels table"""

	bin = frappe.qb.DocType("Bin")
	product = frappe.qb.DocType("Product")

	query = (
		frappe.qb.from_(product)
		.select(product.name, product.product_name, product.description, product.product_group, product.brand, product.stock_uom)
		.where(
			(product.is_stock_product == 1)
			& (product.disabled == 0)
			& (
				(product.end_of_life > today()) | (product.end_of_life.isnull()) | (product.end_of_life == "0000-00-00")
			)
			& (ExistsCriterion(frappe.qb.from_(bin).select(bin.name).where(bin.product_code == product.name)))
		)
	)

	if product_code:
		query = query.where(product.product_code == product_code)

	if include_uom:
		ucd = frappe.qb.DocType("UOM Conversion Detail")
		query = query.left_join(ucd).on((ucd.parent == product.name) & (ucd.uom == include_uom))

	products = query.run(as_dict=True)

	ir = frappe.qb.DocType("Product Reorder")
	query = frappe.qb.from_(ir).select("*")

	if product_code:
		query = query.where(ir.parent == product_code)

	reorder_levels = frappe._dict()
	for d in query.run(as_dict=True):
		if d.parent not in reorder_levels:
			reorder_levels[d.parent] = []

		reorder_levels[d.parent].append(d)

	product_map = frappe._dict()
	for product in products:
		product["reorder_levels"] = reorder_levels.get(product.name) or []
		product_map[product.name] = product

	return product_map
