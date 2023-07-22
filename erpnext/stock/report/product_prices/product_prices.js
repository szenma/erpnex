// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Product Prices"] = {
	"filters": [
		{
			"fieldname": "products",
			"label": __("Products Filter"),
			"fieldtype": "Select",
			"options": "Enabled Products only\nDisabled Products only\nAll Products",
			"default": "Enabled Products only",
			"on_change": function(query_report) {
				query_report.trigger_refresh();
			}
		}
	]
}
