// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors and contributors
// For license information, please see license.txt

frappe.query_reports["Product Bundle Balance"] = {
	"filters": [
		{
			"fieldname":"date",
			"label": __("Date"),
			"fieldtype": "Date",
			"width": "80",
			"reqd": 1,
			"default": frappe.datetime.get_today(),
		},
		{
			"fieldname": "product_code",
			"label": __("Product"),
			"fieldtype": "Link",
			"width": "80",
			"options": "Product",
			"get_query": function() {
				return {
					query: "erpnext.controllers.queries.product_query",
					filters: {"is_stock_product": 0}
				};
			}
		},
		{
			"fieldname": "product_group",
			"label": __("Product Group"),
			"fieldtype": "Link",
			"width": "80",
			"options": "Product Group"
		},
		{
			"fieldname":"brand",
			"label": __("Brand"),
			"fieldtype": "Link",
			"options": "Brand"
		},
		{
			"fieldname": "warehouse",
			"label": __("Warehouse"),
			"fieldtype": "Link",
			"width": "80",
			"options": "Warehouse"
		},
	],
	"initial_depth": 0,
	"formatter": function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data.parent_product) {
			value = $(`<span>${value}</span>`);
			var $value = $(value).css("font-weight", "bold");
			value = $value.wrap("<p></p>").parent().html();
		}
		return value;
	}
};
