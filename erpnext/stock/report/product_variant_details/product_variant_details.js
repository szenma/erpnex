// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Product Variant Details"] = {
	"filters": [
		{
			reqd: 1,
			default: "",
			options: "Product",
			label: __("Product"),
			fieldname: "product",
			fieldtype: "Link",
			get_query: () => {
				return {
					filters: { "has_variants": 1 }
				}
			}
		}
	]
}
