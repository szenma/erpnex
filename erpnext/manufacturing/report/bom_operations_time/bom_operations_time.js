// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["BOM Operations Time"] = {
	"filters": [
		{
			"fieldname": "product_code",
			"label": __("Product Code"),
			"fieldtype": "Link",
			"width": "100",
			"options": "Product",
			"get_query": () =>{
				return {
					filters: { "disabled": 0, "is_stock_product": 1 }
				}
			}
		},
		{
			"fieldname": "bom_id",
			"label": __("BOM ID"),
			"fieldtype": "MultiSelectList",
			"width": "100",
			"options": "BOM",
			"get_data": function(txt) {
				return frappe.db.get_link_options("BOM", txt);
			},
			"get_query": () =>{
				return {
					filters: { "docstatus": 1, "is_active": 1, "with_operations": 1 }
				}
			}
		},
		{
			"fieldname": "workstation",
			"label": __("Workstation"),
			"fieldtype": "Link",
			"width": "100",
			"options": "Workstation"
		},
	]
};
