# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _


def execute(filters=None):
	data = []
	columns = get_columns()
	get_data(filters, data)
	return columns, data


def get_data(filters, data):
	get_exploded_products(filters.bom, data)


def get_exploded_products(bom, data, indent=0, qty=1):
	exploded_products = frappe.get_all(
		"BOM Product",
		filters={"parent": bom},
		fields=["qty", "bom_no", "qty", "product_code", "product_name", "description", "uom"],
	)

	for product in exploded_products:
		print(product.bom_no, indent)
		product["indent"] = indent
		data.append(
			{
				"product_code": product.product_code,
				"product_name": product.product_name,
				"indent": indent,
				"bom_level": indent,
				"bom": product.bom_no,
				"qty": product.qty * qty,
				"uom": product.uom,
				"description": product.description,
			}
		)
		if product.bom_no:
			get_exploded_products(product.bom_no, data, indent=indent + 1, qty=product.qty)


def get_columns():
	return [
		{
			"label": _("Product Code"),
			"fieldtype": "Link",
			"fieldname": "product_code",
			"width": 300,
			"options": "Product",
		},
		{"label": _("Product Name"), "fieldtype": "data", "fieldname": "product_name", "width": 100},
		{"label": _("BOM"), "fieldtype": "Link", "fieldname": "bom", "width": 150, "options": "BOM"},
		{"label": _("Qty"), "fieldtype": "data", "fieldname": "qty", "width": 100},
		{"label": _("UOM"), "fieldtype": "data", "fieldname": "uom", "width": 100},
		{"label": _("BOM Level"), "fieldtype": "Int", "fieldname": "bom_level", "width": 100},
		{
			"label": _("Standard Description"),
			"fieldtype": "data",
			"fieldname": "description",
			"width": 150,
		},
	]
