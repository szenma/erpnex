# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _

from erpnext import get_default_company
from erpnext.accounts.party import get_party_details
from erpnext.stock.get_product_details import get_price_list_rate_for


def execute(filters=None):
	if not filters:
		filters = {}

	if not filters.get("customer"):
		frappe.throw(_("Please select a Customer"))

	columns = get_columns(filters)
	data = get_data(filters)

	return columns, data


def get_columns(filters=None):
	return [
		{
			"label": _("Product Code"),
			"fieldname": "product_code",
			"fieldtype": "Link",
			"options": "Product",
			"width": 150,
		},
		{"label": _("Product Name"), "fieldname": "product_name", "fieldtype": "Data", "width": 200},
		{"label": _("Selling Rate"), "fieldname": "selling_rate", "fieldtype": "Currency"},
		{
			"label": _("Available Stock"),
			"fieldname": "available_stock",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Price List"),
			"fieldname": "price_list",
			"fieldtype": "Link",
			"options": "Price List",
			"width": 120,
		},
	]


def get_data(filters=None):
	data = []
	customer_details = get_customer_details(filters)

	products = get_selling_products(filters)
	product_stock_map = frappe.get_all(
		"Bin", fields=["product_code", "sum(actual_qty) AS available"], group_by="product_code"
	)
	product_stock_map = {product.product_code: product.available for product in product_stock_map}

	for product in products:
		price_list_rate = get_price_list_rate_for(customer_details, product.product_code) or 0.0
		available_stock = product_stock_map.get(product.product_code)

		data.append(
			{
				"product_code": product.product_code,
				"product_name": product.product_name,
				"selling_rate": price_list_rate,
				"price_list": customer_details.get("price_list"),
				"available_stock": available_stock,
			}
		)

	return data


def get_customer_details(filters):
	customer_details = get_party_details(party=filters.get("customer"), party_type="Customer")
	customer_details.update(
		{"company": get_default_company(), "price_list": customer_details.get("selling_price_list")}
	)

	return customer_details


def get_selling_products(filters):
	if filters.get("product"):
		product_filters = {"product_code": filters.get("product"), "is_sales_product": 1, "disabled": 0}
	else:
		product_filters = {"is_sales_product": 1, "disabled": 0}

	products = frappe.get_all(
		"Product", filters=product_filters, fields=["product_code", "product_name"], order_by="product_name"
	)

	return products
