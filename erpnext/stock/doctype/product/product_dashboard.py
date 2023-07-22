from frappe import _


def get_data():
	return {
		"heatmap": True,
		"heatmap_message": _("This is based on stock movement. See {0} for details").format(
			'<a href="/app/query-report/Stock Ledger">' + _("Stock Ledger") + "</a>"
		),
		"fieldname": "product_code",
		"non_standard_fieldnames": {
			"Work Order": "production_product",
			"Product Bundle": "new_product_code",
			"BOM": "product",
			"Batch": "product",
		},
		"transactions": [
			{"label": _("Groups"), "products": ["BOM", "Product Bundle", "Product Alternative"]},
			{"label": _("Pricing"), "products": ["Product Price", "Pricing Rule"]},
			{"label": _("Sell"), "products": ["Quotation", "Sales Order", "Delivery Note", "Sales Invoice"]},
			{
				"label": _("Buy"),
				"products": [
					"Material Request",
					"Supplier Quotation",
					"Request for Quotation",
					"Purchase Order",
					"Purchase Receipt",
					"Purchase Invoice",
				],
			},
			{"label": _("Manufacture"), "products": ["Production Plan", "Work Order", "Product Manufacturer"]},
			{"label": _("Traceability"), "products": ["Serial No", "Batch"]},
			{"label": _("Stock Movement"), "products": ["Stock Entry", "Stock Reconciliation"]},
			{"label": _("E-commerce"), "products": ["Website Product"]},
		],
	}
