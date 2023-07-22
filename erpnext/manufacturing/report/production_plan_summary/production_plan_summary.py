# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	columns, data = [], []
	data = get_data(filters)
	columns = get_column(filters)

	return columns, data


def get_data(filters):
	data = []

	order_details = {}
	get_work_order_details(filters, order_details)
	get_purchase_order_details(filters, order_details)
	get_production_plan_product_details(filters, data, order_details)

	return data


def get_production_plan_product_details(filters, data, order_details):
	productwise_indent = {}

	production_plan_doc = frappe.get_cached_doc("Production Plan", filters.get("production_plan"))
	for row in production_plan_doc.po_products:
		work_order = frappe.get_value(
			"Work Order",
			{"production_plan_product": row.name, "bom_no": row.bom_no, "production_product": row.product_code},
			"name",
		)

		if row.product_code not in productwise_indent:
			productwise_indent.setdefault(row.product_code, {})

		data.append(
			{
				"indent": 0,
				"product_code": row.product_code,
				"product_name": frappe.get_cached_value("Product", row.product_code, "product_name"),
				"qty": row.planned_qty,
				"document_type": "Work Order",
				"document_name": work_order or "",
				"bom_level": 0,
				"produced_qty": order_details.get((work_order, row.product_code), {}).get("produced_qty", 0),
				"pending_qty": flt(row.planned_qty)
				- flt(order_details.get((work_order, row.product_code), {}).get("produced_qty", 0)),
			}
		)

		get_production_plan_sub_assembly_product_details(
			filters, row, production_plan_doc, data, order_details
		)


def get_production_plan_sub_assembly_product_details(
	filters, row, production_plan_doc, data, order_details
):
	for product in production_plan_doc.sub_assembly_products:
		if row.name == product.production_plan_product:
			subcontracted_product = product.type_of_manufacturing == "Subcontract"

			if subcontracted_product:
				docname = frappe.get_value(
					"Purchase Order Product",
					{"production_plan_sub_assembly_product": product.name, "docstatus": ("<", 2)},
					"parent",
				)
			else:
				docname = frappe.get_value(
					"Work Order", {"production_plan_sub_assembly_product": product.name, "docstatus": ("<", 2)}, "name"
				)

			data.append(
				{
					"indent": 1,
					"product_code": product.production_product,
					"product_name": product.product_name,
					"qty": product.qty,
					"document_type": "Work Order" if not subcontracted_product else "Purchase Order",
					"document_name": docname or "",
					"bom_level": product.bom_level,
					"produced_qty": order_details.get((docname, product.production_product), {}).get("produced_qty", 0),
					"pending_qty": flt(product.qty)
					- flt(order_details.get((docname, product.production_product), {}).get("produced_qty", 0)),
				}
			)


def get_work_order_details(filters, order_details):
	for row in frappe.get_all(
		"Work Order",
		filters={"production_plan": filters.get("production_plan")},
		fields=["name", "produced_qty", "production_plan", "production_product"],
	):
		order_details.setdefault((row.name, row.production_product), row)


def get_purchase_order_details(filters, order_details):
	for row in frappe.get_all(
		"Purchase Order Product",
		filters={"production_plan": filters.get("production_plan")},
		fields=["parent", "received_qty as produced_qty", "product_code"],
	):
		order_details.setdefault((row.parent, row.product_code), row)


def get_column(filters):
	return [
		{
			"label": _("Finished Good"),
			"fieldtype": "Link",
			"fieldname": "product_code",
			"width": 300,
			"options": "Product",
		},
		{"label": _("Product Name"), "fieldtype": "data", "fieldname": "product_name", "width": 100},
		{
			"label": _("Document Type"),
			"fieldtype": "Link",
			"fieldname": "document_type",
			"width": 150,
			"options": "DocType",
		},
		{
			"label": _("Document Name"),
			"fieldtype": "Dynamic Link",
			"fieldname": "document_name",
			"width": 150,
		},
		{"label": _("BOM Level"), "fieldtype": "Int", "fieldname": "bom_level", "width": 100},
		{"label": _("Order Qty"), "fieldtype": "Float", "fieldname": "qty", "width": 120},
		{"label": _("Received Qty"), "fieldtype": "Float", "fieldname": "produced_qty", "width": 160},
		{"label": _("Pending Qty"), "fieldtype": "Float", "fieldname": "pending_qty", "width": 110},
	]
