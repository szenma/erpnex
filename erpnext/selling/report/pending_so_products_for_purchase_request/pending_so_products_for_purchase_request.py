# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	columns = get_columns()
	data = get_data()
	return columns, data


def get_columns():
	columns = [
		{
			"label": _("Product Code"),
			"options": "Product",
			"fieldname": "product_code",
			"fieldtype": "Link",
			"width": 200,
		},
		{"label": _("Product Name"), "fieldname": "product_name", "fieldtype": "Data", "width": 200},
		{"label": _("Description"), "fieldname": "description", "fieldtype": "Data", "width": 140},
		{
			"label": _("S.O. No."),
			"options": "Sales Order",
			"fieldname": "sales_order_no",
			"fieldtype": "Link",
			"width": 140,
		},
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 140},
		{
			"label": _("Material Request"),
			"fieldname": "material_request",
			"fieldtype": "Data",
			"width": 140,
		},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 140},
		{"label": _("Territory"), "fieldname": "territory", "fieldtype": "Data", "width": 140},
		{"label": _("SO Qty"), "fieldname": "so_qty", "fieldtype": "Float", "width": 140},
		{"label": _("Requested Qty"), "fieldname": "requested_qty", "fieldtype": "Float", "width": 140},
		{"label": _("Pending Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 140},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Data", "width": 140},
	]
	return columns


def get_data():
	sales_order_entry = frappe.db.sql(
		"""
		SELECT
			so_product.product_code,
			so_product.product_name,
			so_product.description,
			so.name,
			so.transaction_date,
			so.customer,
			so.territory,
			sum(so_product.qty) as total_qty,
			so.company
		FROM `tabSales Order` so, `tabSales Order Product` so_product
		WHERE
			so.docstatus = 1
			and so.name = so_product.parent
			and so.status not in  ('Closed','Completed','Cancelled')
		GROUP BY
			so.name,so_product.product_code
		""",
		as_dict=1,
	)

	sales_orders = [row.name for row in sales_order_entry]
	mr_records = frappe.get_all(
		"Material Request Product",
		{"sales_order": ("in", sales_orders), "docstatus": 1},
		["parent", "qty", "sales_order", "product_code"],
	)

	bundled_product_map = get_packed_products(sales_orders)

	product_with_product_bundle = get_products_with_product_bundle(
		[row.product_code for row in sales_order_entry]
	)

	materials_request_dict = {}

	for record in mr_records:
		key = (record.sales_order, record.product_code)
		if key not in materials_request_dict:
			materials_request_dict.setdefault(key, {"qty": 0, "material_requests": [record.parent]})

		details = materials_request_dict.get(key)
		details["qty"] += record.qty

		if record.parent not in details.get("material_requests"):
			details["material_requests"].append(record.parent)

	pending_so = []
	for so in sales_order_entry:
		if so.product_code not in product_with_product_bundle:
			material_requests_against_so = materials_request_dict.get((so.name, so.product_code)) or {}
			# check for pending sales order
			if flt(so.total_qty) > flt(material_requests_against_so.get("qty")):
				so_record = {
					"product_code": so.product_code,
					"product_name": so.product_name,
					"description": so.description,
					"sales_order_no": so.name,
					"date": so.transaction_date,
					"material_request": ",".join(material_requests_against_so.get("material_requests", [])),
					"customer": so.customer,
					"territory": so.territory,
					"so_qty": so.total_qty,
					"requested_qty": material_requests_against_so.get("qty"),
					"pending_qty": so.total_qty - flt(material_requests_against_so.get("qty")),
					"company": so.company,
				}
				pending_so.append(so_record)
		else:
			for product in bundled_product_map.get((so.name, so.product_code), []):
				material_requests_against_so = materials_request_dict.get((so.name, product.product_code)) or {}
				if flt(product.qty) > flt(material_requests_against_so.get("qty")):
					so_record = {
						"product_code": product.product_code,
						"product_name": product.product_name,
						"description": product.description,
						"sales_order_no": so.name,
						"date": so.transaction_date,
						"material_request": ",".join(material_requests_against_so.get("material_requests", [])),
						"customer": so.customer,
						"territory": so.territory,
						"so_qty": product.qty,
						"requested_qty": material_requests_against_so.get("qty", 0),
						"pending_qty": product.qty - flt(material_requests_against_so.get("qty", 0)),
						"company": so.company,
					}
					pending_so.append(so_record)

	return pending_so


def get_products_with_product_bundle(product_list):
	bundled_products = frappe.get_all(
		"Product Bundle", filters=[("new_product_code", "IN", product_list)], fields=["new_product_code"]
	)

	return [d.new_product_code for d in bundled_products]


def get_packed_products(sales_order_list):
	packed_products = frappe.get_all(
		"Packed Product",
		filters=[("parent", "IN", sales_order_list)],
		fields=["parent_product", "product_code", "qty", "product_name", "description", "parent"],
	)

	bundled_product_map = frappe._dict()
	for d in packed_products:
		bundled_product_map.setdefault((d.parent, d.parent_product), []).append(d)

	return bundled_product_map
