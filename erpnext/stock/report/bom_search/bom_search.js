// Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and Contributors and contributors
// For license information, please see license.txt

frappe.query_reports["BOM Search"] = {
	"filters": [
		{
			fieldname: "product1",
			label: __("Product 1"),
			fieldtype: "Link",
			options: "Product"
		},
		{
			fieldname: "product2",
			label: __("Product 2"),
			fieldtype: "Link",
			options: "Product"
		},
		{
			fieldname: "product3",
			label: __("Product 3"),
			fieldtype: "Link",
			options: "Product"
		},
		{
			fieldname: "product4",
			label: __("Product 4"),
			fieldtype: "Link",
			options: "Product"
		},
		{
			fieldname: "product5",
			label: __("Product 5"),
			fieldtype: "Link",
			options: "Product"
		},
		{
			fieldname: "search_sub_assemblies",
			label: __("Search Sub Assemblies"),
			fieldtype: "Check",
		},
	]
}
