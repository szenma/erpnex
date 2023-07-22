// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Stock Projected Qty"] = {
	"filters": [
		{
			"fieldname":"company",
			"label": __("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"default": frappe.defaults.get_user_default("Company")
		},
		{
			"fieldname":"warehouse",
			"label": __("Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"get_query": () => {
				return {
					filters: {
						company: frappe.query_report.get_filter_value('company')
					}
				}
			}
		},
		{
			"fieldname":"product_code",
			"label": __("Product"),
			"fieldtype": "Link",
			"options": "Product",
			"get_query": function() {
				return {
					query: "erpnext.controllers.queries.product_query"
				}
			}
		},
		{
			"fieldname":"product_group",
			"label": __("Product Group"),
			"fieldtype": "Link",
			"options": "Product Group"
		},
		{
			"fieldname":"brand",
			"label": __("Brand"),
			"fieldtype": "Link",
			"options": "Brand"
		},
		{
			"fieldname":"include_uom",
			"label": __("Include UOM"),
			"fieldtype": "Link",
			"options": "UOM"
		}
	]
}
