// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Inactive Sales Products"] = {
	"filters": [
		{
			fieldname: "territory",
			label: __("Territory"),
			fieldtype: "Link",
			options: "Territory",
			reqd: 1,
		},
		{
			fieldname: "product",
			label: __("Product"),
			fieldtype: "Link",
			options: "Product"
		},
		{
			fieldname: "product_group",
			label: __("Product Group"),
			fieldtype: "Link",
			options: "Product Group"
		},
		{
			fieldname: "based_on",
			label: __("Based On"),
			fieldtype: "Select",
			options: "Sales Order\nSales Invoice",
			default: "Sales Order"
		},
		{
			fieldname: "days",
			label: __("Days Since Last order"),
			fieldtype: "Select",
			options: [30, 60, 90],
			default: 30
		},
	]
};
