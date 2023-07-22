// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Product Price Stock"] = {
	"filters": [
		{
			"fieldname":"product_code",
			"label": __("Product"),
			"fieldtype": "Link",
			"options": "Product"
		}
	]
}
