from frappe import _


def get_data():
	return {
		"fieldname": "bom_no",
		"non_standard_fieldnames": {
			"Product": "default_bom",
			"Purchase Order": "bom",
			"Purchase Receipt": "bom",
			"Purchase Invoice": "bom",
		},
		"transactions": [
			{"label": _("Stock"), "products": ["Product", "Stock Entry", "Quality Inspection"]},
			{"label": _("Manufacture"), "products": ["BOM", "Work Order", "Job Card"]},
			{
				"label": _("Subcontract"),
				"products": ["Purchase Order", "Purchase Receipt", "Purchase Invoice"],
			},
		],
		"disable_create_buttons": [
			"Product",
			"Purchase Order",
			"Purchase Receipt",
			"Purchase Invoice",
			"Job Card",
			"Stock Entry",
			"BOM",
		],
	}
