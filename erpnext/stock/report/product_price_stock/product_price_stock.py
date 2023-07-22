# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns, data = [], []
	columns = get_columns()
	data = get_data(filters, columns)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Product Code"),
			"fieldname": "product_code",
			"fieldtype": "Link",
			"options": "Product",
			"width": 120,
		},
		{"label": _("Product Name"), "fieldname": "product_name", "fieldtype": "Data", "width": 120},
		{"label": _("Brand"), "fieldname": "brand", "fieldtype": "Data", "width": 100},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 120,
		},
		{
			"label": _("Stock Available"),
			"fieldname": "stock_available",
			"fieldtype": "Float",
			"width": 120,
		},
		{
			"label": _("Buying Price List"),
			"fieldname": "buying_price_list",
			"fieldtype": "Link",
			"options": "Price List",
			"width": 120,
		},
		{"label": _("Buying Rate"), "fieldname": "buying_rate", "fieldtype": "Currency", "width": 120},
		{
			"label": _("Selling Price List"),
			"fieldname": "selling_price_list",
			"fieldtype": "Link",
			"options": "Price List",
			"width": 120,
		},
		{"label": _("Selling Rate"), "fieldname": "selling_rate", "fieldtype": "Currency", "width": 120},
	]


def get_data(filters, columns):
	product_price_qty_data = []
	product_price_qty_data = get_product_price_qty_data(filters)
	return product_price_qty_data


def get_product_price_qty_data(filters):
	product_price = frappe.qb.DocType("Product Price")
	bin = frappe.qb.DocType("Bin")

	query = (
		frappe.qb.from_(product_price)
		.left_join(bin)
		.on(product_price.product_code == bin.product_code)
		.select(
			product_price.product_code,
			product_price.product_name,
			product_price.name.as_("price_list_name"),
			product_price.brand.as_("brand"),
			bin.warehouse.as_("warehouse"),
			bin.actual_qty.as_("actual_qty"),
		)
	)

	if filters.get("product_code"):
		query = query.where(product_price.product_code == filters.get("product_code"))

	product_results = query.run(as_dict=True)

	price_list_names = list(set(product.price_list_name for product in product_results))

	buying_price_map = get_price_map(price_list_names, buying=1)
	selling_price_map = get_price_map(price_list_names, selling=1)

	result = []
	if product_results:
		for product_dict in product_results:
			data = {
				"product_code": product_dict.product_code,
				"product_name": product_dict.product_name,
				"brand": product_dict.brand,
				"warehouse": product_dict.warehouse,
				"stock_available": product_dict.actual_qty or 0,
				"buying_price_list": "",
				"buying_rate": 0.0,
				"selling_price_list": "",
				"selling_rate": 0.0,
			}

			price_list = product_dict["price_list_name"]
			if buying_price_map.get(price_list):
				data["buying_price_list"] = buying_price_map.get(price_list)["Buying Price List"] or ""
				data["buying_rate"] = buying_price_map.get(price_list)["Buying Rate"] or 0
			if selling_price_map.get(price_list):
				data["selling_price_list"] = selling_price_map.get(price_list)["Selling Price List"] or ""
				data["selling_rate"] = selling_price_map.get(price_list)["Selling Rate"] or 0

			result.append(data)

	return result


def get_price_map(price_list_names, buying=0, selling=0):
	price_map = {}

	if not price_list_names:
		return price_map

	rate_key = "Buying Rate" if buying else "Selling Rate"
	price_list_key = "Buying Price List" if buying else "Selling Price List"

	filters = {"name": ("in", price_list_names)}
	if buying:
		filters["buying"] = 1
	else:
		filters["selling"] = 1

	pricing_details = frappe.get_all(
		"Product Price", fields=["name", "price_list", "price_list_rate"], filters=filters
	)

	for d in pricing_details:
		name = d["name"]
		price_map[name] = {price_list_key: d["price_list"], rate_key: d["price_list_rate"]}

	return price_map
