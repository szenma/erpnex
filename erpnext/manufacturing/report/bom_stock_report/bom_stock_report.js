frappe.query_reports["BOM Stock Report"] = {
	"filters": [
		{
			"fieldname": "bom",
			"label": __("BOM"),
			"fieldtype": "Link",
			"options": "BOM",
			"reqd": 1
		}, {
			"fieldname": "warehouse",
			"label": __("Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"reqd": 1
		}, {
			"fieldname": "show_exploded_view",
			"label": __("Show exploded view"),
			"fieldtype": "Check"
		}, {
			"fieldname": "qty_to_produce",
			"label": __("Quantity to Produce"),
			"fieldtype": "Int",
			"default": "1"
		 },
	],
	"formatter": function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.id == "product") {
			if (data["in_stock_qty"] >= data["required_qty"]) {
				value = `<a style='color:green' href="/app/product/${data['product']}" data-doctype="Product">${data['product']}</a>`;
			} else {
				value = `<a style='color:red' href="/app/product/${data['product']}" data-doctype="Product">${data['product']}</a>`;
			}
		}
		return value
	}
}
