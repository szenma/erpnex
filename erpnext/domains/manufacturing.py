data = {
	"desktop_icons": [
		"Product",
		"BOM",
		"Customer",
		"Supplier",
		"Sales Order",
		"Purchase Order",
		"Work Order",
		"Task",
		"Accounts",
		"HR",
		"ToDo",
	],
	"properties": [
		{
			"doctype": "Product",
			"fieldname": "manufacturing",
			"property": "collapsible_depends_on",
			"value": "is_stock_product",
		},
	],
	"set_value": [["Stock Settings", None, "show_barcode_field", 1]],
	"default_portal_role": "Customer",
}
