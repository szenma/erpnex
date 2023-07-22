# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import cint


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	columns = [
		{
			"fieldname": "territory",
			"fieldtype": "Link",
			"label": _("Territory"),
			"options": "Territory",
			"width": 100,
		},
		{
			"fieldname": "product_group",
			"fieldtype": "Link",
			"label": _("Product Group"),
			"options": "Product Group",
			"width": 150,
		},
		{"fieldname": "product", "fieldtype": "Link", "options": "Product", "label": _("Product"), "width": 150},
		{"fieldname": "product_name", "fieldtype": "Data", "label": _("Product Name"), "width": 150},
		{
			"fieldname": "customer",
			"fieldtype": "Link",
			"label": _("Customer"),
			"options": "Customer",
			"width": 100,
		},
		{
			"fieldname": "last_order_date",
			"fieldtype": "Date",
			"label": _("Last Order Date"),
			"width": 100,
		},
		{"fieldname": "qty", "fieldtype": "Float", "label": _("Quantity"), "width": 100},
		{
			"fieldname": "days_since_last_order",
			"fieldtype": "Int",
			"label": _("Days Since Last Order"),
			"width": 100,
		},
	]

	return columns


def get_data(filters):
	data = []
	products = get_products(filters)
	territories = get_territories(filters)
	sales_invoice_data = get_sales_details(filters)

	for territory in territories:
		for product in products:
			row = {
				"territory": territory.name,
				"product_group": product.product_group,
				"product": product.product_code,
				"product_name": product.product_name,
			}

			if sales_invoice_data.get((territory.name, product.product_code)):
				product_obj = sales_invoice_data[(territory.name, product.product_code)]
				if product_obj.days_since_last_order > cint(filters["days"]):
					row.update(
						{
							"territory": product_obj.territory,
							"customer": product_obj.customer,
							"last_order_date": product_obj.last_order_date,
							"qty": product_obj.qty,
							"days_since_last_order": product_obj.days_since_last_order,
						}
					)
				else:
					continue

			data.append(row)

	return data


def get_sales_details(filters):
	data = []
	product_details_map = {}

	date_field = "s.transaction_date" if filters["based_on"] == "Sales Order" else "s.posting_date"

	sales_data = frappe.db.sql(
		"""
		select s.territory, s.customer, si.product_group, si.product_code, si.qty, {date_field} as last_order_date,
		DATEDIFF(CURRENT_DATE, {date_field}) as days_since_last_order
		from `tab{doctype}` s, `tab{doctype} Product` si
		where s.name = si.parent and s.docstatus = 1
		order by days_since_last_order """.format(  # nosec
			date_field=date_field, doctype=filters["based_on"]
		),
		as_dict=1,
	)

	for d in sales_data:
		product_details_map.setdefault((d.territory, d.product_code), d)

	return product_details_map


def get_territories(filters):

	filter_dict = {}
	if filters.get("territory"):
		filter_dict.update({"name": filters["territory"]})

	territories = frappe.get_all("Territory", fields=["name"], filters=filter_dict)

	return territories


def get_products(filters):
	filters_dict = {"disabled": 0, "is_stock_product": 1}

	if filters.get("product_group"):
		filters_dict.update({"product_group": filters["product_group"]})

	if filters.get("product"):
		filters_dict.update({"name": filters["product"]})

	products = frappe.get_all(
		"Product",
		fields=["name", "product_group", "product_name", "product_code"],
		filters=filters_dict,
		order_by="name",
	)

	return products
