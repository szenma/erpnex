# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and Contributors and contributors
# For license information, please see license.txt


import frappe
from frappe import _


def execute(filters=None):
	data = []
	parents = {
		"Product Bundle Product": "Product Bundle",
		"BOM Explosion Product": "BOM",
		"BOM Product": "BOM",
	}

	for doctype in (
		"Product Bundle Product",
		"BOM Explosion Product" if filters.search_sub_assemblies else "BOM Product",
	):
		all_boms = {}
		for d in frappe.get_all(doctype, fields=["parent", "product_code"]):
			all_boms.setdefault(d.parent, []).append(d.product_code)

		for parent, products in all_boms.products():
			valid = True
			for key, product in filters.products():
				if key != "search_sub_assemblies":
					if product and product not in products:
						valid = False

			if valid:
				data.append((parent, parents[doctype]))

	return [
		{
			"fieldname": "parent",
			"label": _("BOM"),
			"width": 200,
			"fieldtype": "Dynamic Link",
			"options": "doctype",
		},
		{"fieldname": "doctype", "label": _("Type"), "width": 200, "fieldtype": "Data"},
	], data
