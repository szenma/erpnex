# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns(filters.product)
	data = get_data(filters.product)
	return columns, data


def get_data(product):
	if not product:
		return []
	product_dicts = []

	variant_results = frappe.db.get_all(
		"Product", fields=["name"], filters={"variant_of": ["=", product], "disabled": 0}
	)

	if not variant_results:
		frappe.msgprint(_("There aren't any product variants for the selected product"))
		return []
	else:
		variant_list = [variant["name"] for variant in variant_results]

	order_count_map = get_open_sales_orders_count(variant_list)
	stock_details_map = get_stock_details_map(variant_list)
	buying_price_map = get_buying_price_map(variant_list)
	selling_price_map = get_selling_price_map(variant_list)
	attr_val_map = get_attribute_values_map(variant_list)

	attributes = frappe.db.get_all(
		"Product Variant Attribute",
		fields=["attribute"],
		filters={"parent": ["in", variant_list]},
		group_by="attribute",
	)
	attribute_list = [row.get("attribute") for row in attributes]

	# Prepare dicts
	variant_dicts = [{"variant_name": d["name"]} for d in variant_results]
	for product_dict in variant_dicts:
		name = product_dict.get("variant_name")

		for attribute in attribute_list:
			attr_dict = attr_val_map.get(name)
			if attr_dict and attr_dict.get(attribute):
				product_dict[frappe.scrub(attribute)] = attr_val_map.get(name).get(attribute)

		product_dict["open_orders"] = order_count_map.get(name) or 0

		if stock_details_map.get(name):
			product_dict["current_stock"] = stock_details_map.get(name)["Inventory"] or 0
			product_dict["in_production"] = stock_details_map.get(name)["In Production"] or 0
		else:
			product_dict["current_stock"] = product_dict["in_production"] = 0

		product_dict["avg_buying_price_list_rate"] = buying_price_map.get(name) or 0
		product_dict["avg_selling_price_list_rate"] = selling_price_map.get(name) or 0

		product_dicts.append(product_dict)

	return product_dicts


def get_columns(product):
	columns = [
		{
			"fieldname": "variant_name",
			"label": _("Variant"),
			"fieldtype": "Link",
			"options": "Product",
			"width": 200,
		}
	]

	product_doc = frappe.get_doc("Product", product)

	for entry in product_doc.attributes:
		columns.append(
			{
				"fieldname": frappe.scrub(entry.attribute),
				"label": entry.attribute,
				"fieldtype": "Data",
				"width": 100,
			}
		)

	additional_columns = [
		{
			"fieldname": "avg_buying_price_list_rate",
			"label": _("Avg. Buying Price List Rate"),
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"fieldname": "avg_selling_price_list_rate",
			"label": _("Avg. Selling Price List Rate"),
			"fieldtype": "Currency",
			"width": 150,
		},
		{"fieldname": "current_stock", "label": _("Current Stock"), "fieldtype": "Float", "width": 120},
		{"fieldname": "in_production", "label": _("In Production"), "fieldtype": "Float", "width": 150},
		{
			"fieldname": "open_orders",
			"label": _("Open Sales Orders"),
			"fieldtype": "Float",
			"width": 150,
		},
	]
	columns.extend(additional_columns)

	return columns


def get_open_sales_orders_count(variants_list):
	open_sales_orders = frappe.db.get_list(
		"Sales Order",
		fields=["name", "`tabSales Order Product`.product_code"],
		filters=[
			["Sales Order", "docstatus", "=", 1],
			["Sales Order Product", "product_code", "in", variants_list],
		],
		distinct=1,
	)

	order_count_map = {}
	for row in open_sales_orders:
		product_code = row.get("product_code")
		if order_count_map.get(product_code) is None:
			order_count_map[product_code] = 1
		else:
			order_count_map[product_code] += 1

	return order_count_map


def get_stock_details_map(variant_list):
	stock_details = frappe.db.get_all(
		"Bin",
		fields=[
			"sum(planned_qty) as planned_qty",
			"sum(actual_qty) as actual_qty",
			"sum(projected_qty) as projected_qty",
			"product_code",
		],
		filters={"product_code": ["in", variant_list]},
		group_by="product_code",
	)

	stock_details_map = {}
	for row in stock_details:
		name = row.get("product_code")
		stock_details_map[name] = {
			"Inventory": row.get("actual_qty"),
			"In Production": row.get("planned_qty"),
		}

	return stock_details_map


def get_buying_price_map(variant_list):
	buying = frappe.db.get_all(
		"Product Price",
		fields=[
			"avg(price_list_rate) as avg_rate",
			"product_code",
		],
		filters={"product_code": ["in", variant_list], "buying": 1},
		group_by="product_code",
	)

	buying_price_map = {}
	for row in buying:
		buying_price_map[row.get("product_code")] = row.get("avg_rate")

	return buying_price_map


def get_selling_price_map(variant_list):
	selling = frappe.db.get_all(
		"Product Price",
		fields=[
			"avg(price_list_rate) as avg_rate",
			"product_code",
		],
		filters={"product_code": ["in", variant_list], "selling": 1},
		group_by="product_code",
	)

	selling_price_map = {}
	for row in selling:
		selling_price_map[row.get("product_code")] = row.get("avg_rate")

	return selling_price_map


def get_attribute_values_map(variant_list):
	attribute_list = frappe.db.get_all(
		"Product Variant Attribute",
		fields=["attribute", "attribute_value", "parent"],
		filters={"parent": ["in", variant_list]},
	)

	attr_val_map = {}
	for row in attribute_list:
		name = row.get("parent")
		if not attr_val_map.get(name):
			attr_val_map[name] = {}

		attr_val_map[name][row.get("attribute")] = row.get("attribute_value")

	return attr_val_map
