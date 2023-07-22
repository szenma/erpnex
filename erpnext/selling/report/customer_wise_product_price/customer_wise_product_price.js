// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Customer-wise Product Price"] = {
	"filters": [
		{
			"label": __("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"reqd": 1
		},
		{
			"label": __("Product"),
			"fieldname": "product",
			"fieldtype": "Link",
			"options": "Product",
			"get_query": () => {
				return {
					query: "erpnext.controllers.queries.product_query",
					filters: { 'is_sales_product': 1 }
				}
			}
		}
	]
}
