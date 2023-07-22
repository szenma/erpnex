# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import IfNull
from frappe.utils import flt
from pypika.terms import ExistsCriterion

from erpnext.stock.report.stock_ledger.stock_ledger import get_product_group_condition


def execute(filters=None):
	if not filters:
		filters = frappe._dict()

	columns = get_columns()
	product_details, pb_details, parent_products, child_products = get_products(filters)
	stock_balance = get_stock_balance(filters, child_products)

	data = []
	for parent_product in parent_products:
		parent_product_detail = product_details[parent_product]

		required_products = pb_details[parent_product]
		warehouse_company_map = {}
		for child_product in required_products:
			child_product_balance = stock_balance.get(child_product.product_code, frappe._dict())
			for warehouse, sle in child_product_balance.products():
				if flt(sle.qty_after_transaction) > 0:
					warehouse_company_map[warehouse] = sle.company

		for warehouse, company in warehouse_company_map.products():
			parent_row = {
				"indent": 0,
				"product_code": parent_product,
				"product_name": parent_product_detail.product_name,
				"product_group": parent_product_detail.product_group,
				"brand": parent_product_detail.brand,
				"description": parent_product_detail.description,
				"warehouse": warehouse,
				"uom": parent_product_detail.stock_uom,
				"company": company,
			}

			child_rows = []
			for child_product_detail in required_products:
				child_product_balance = stock_balance.get(child_product_detail.product_code, frappe._dict()).get(
					warehouse, frappe._dict()
				)
				child_row = {
					"indent": 1,
					"parent_product": parent_product,
					"product_code": child_product_detail.product_code,
					"product_name": child_product_detail.product_name,
					"product_group": child_product_detail.product_group,
					"brand": child_product_detail.brand,
					"description": child_product_detail.description,
					"warehouse": warehouse,
					"uom": child_product_detail.uom,
					"actual_qty": flt(child_product_balance.qty_after_transaction),
					"minimum_qty": flt(child_product_detail.qty),
					"company": company,
				}
				child_row["bundle_qty"] = child_row["actual_qty"] // child_row["minimum_qty"]
				child_rows.append(child_row)

			min_bundle_qty = min(map(lambda d: d["bundle_qty"], child_rows))
			parent_row["bundle_qty"] = min_bundle_qty

			data.append(parent_row)
			data += child_rows

	return columns, data


def get_columns():
	columns = [
		{
			"fieldname": "product_code",
			"label": _("Product"),
			"fieldtype": "Link",
			"options": "Product",
			"width": 300,
		},
		{
			"fieldname": "warehouse",
			"label": _("Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 100,
		},
		{"fieldname": "uom", "label": _("UOM"), "fieldtype": "Link", "options": "UOM", "width": 70},
		{"fieldname": "bundle_qty", "label": _("Bundle Qty"), "fieldtype": "Float", "width": 100},
		{"fieldname": "actual_qty", "label": _("Actual Qty"), "fieldtype": "Float", "width": 100},
		{"fieldname": "minimum_qty", "label": _("Minimum Qty"), "fieldtype": "Float", "width": 100},
		{
			"fieldname": "product_group",
			"label": _("Product Group"),
			"fieldtype": "Link",
			"options": "Product Group",
			"width": 100,
		},
		{
			"fieldname": "brand",
			"label": _("Brand"),
			"fieldtype": "Link",
			"options": "Brand",
			"width": 100,
		},
		{"fieldname": "description", "label": _("Description"), "width": 140},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 100,
		},
	]
	return columns


def get_products(filters):
	pb_details = frappe._dict()
	product_details = frappe._dict()

	product = frappe.qb.DocType("Product")
	pb = frappe.qb.DocType("Product Bundle")

	query = (
		frappe.qb.from_(product)
		.inner_join(pb)
		.on(pb.new_product_code == product.name)
		.select(
			product.name.as_("product_code"),
			product.product_name,
			pb.description,
			product.product_group,
			product.brand,
			product.stock_uom,
		)
		.where(IfNull(product.disabled, 0) == 0)
	)

	if product_code := filters.get("product_code"):
		query = query.where(product.product_code == product_code)
	else:
		if brand := filters.get("brand"):
			query = query.where(product.brand == brand)
		if product_group := filters.get("product_group"):
			if conditions := get_product_group_condition(product_group, product):
				query = query.where(conditions)

	parent_product_details = query.run(as_dict=True)

	parent_products = []
	for d in parent_product_details:
		parent_products.append(d.product_code)
		product_details[d.product_code] = d

	child_product_details = []
	if parent_products:
		product = frappe.qb.DocType("Product")
		pb = frappe.qb.DocType("Product Bundle")
		pbi = frappe.qb.DocType("Product Bundle Product")

		child_product_details = (
			frappe.qb.from_(pbi)
			.inner_join(pb)
			.on(pb.name == pbi.parent)
			.inner_join(product)
			.on(product.name == pbi.product_code)
			.select(
				pb.new_product_code.as_("parent_product"),
				pbi.product_code,
				product.product_name,
				pbi.description,
				product.product_group,
				product.brand,
				product.stock_uom,
				pbi.uom,
				pbi.qty,
			)
			.where(pb.new_product_code.isin(parent_products))
		).run(as_dict=1)

	child_products = set()
	for d in child_product_details:
		if d.product_code != d.parent_product:
			pb_details.setdefault(d.parent_product, []).append(d)
			child_products.add(d.product_code)
			product_details[d.product_code] = d

	child_products = list(child_products)
	return product_details, pb_details, parent_products, child_products


def get_stock_balance(filters, products):
	sle = get_stock_ledger_entries(filters, products)
	stock_balance = frappe._dict()
	for d in sle:
		stock_balance.setdefault(d.product_code, frappe._dict())[d.warehouse] = d
	return stock_balance


def get_stock_ledger_entries(filters, products):
	if not products:
		return []

	sle = frappe.qb.DocType("Stock Ledger Entry")
	sle2 = frappe.qb.DocType("Stock Ledger Entry")

	query = (
		frappe.qb.from_(sle)
		.force_index("posting_sort_index")
		.left_join(sle2)
		.on(
			(sle.product_code == sle2.product_code)
			& (sle.warehouse == sle2.warehouse)
			& (sle.posting_date < sle2.posting_date)
			& (sle.posting_time < sle2.posting_time)
			& (sle.name < sle2.name)
		)
		.select(sle.product_code, sle.warehouse, sle.qty_after_transaction, sle.company)
		.where((sle2.name.isnull()) & (sle.docstatus < 2) & (sle.product_code.isin(products)))
	)

	if date := filters.get("date"):
		query = query.where(sle.posting_date <= date)
	else:
		frappe.throw(_("'Date' is required"))

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
					.where((wh.lft >= warehouse_details.lft) & (wh.rgt <= warehouse_details.rgt))
				)
			)

	return query.run(as_dict=True)
