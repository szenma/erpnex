from frappe import _


def get_data():
	return {
		"fieldname": "material_request",
		"internal_links": {
			"Sales Order": ["products", "sales_order"],
		},
		"transactions": [
			{
				"label": _("Reference"),
				"products": ["Sales Order", "Request for Quotation", "Supplier Quotation", "Purchase Order"],
			},
			{"label": _("Stock"), "products": ["Stock Entry", "Purchase Receipt", "Pick List"]},
			{"label": _("Manufacturing"), "products": ["Work Order"]},
			{"label": _("Internal Transfer"), "products": ["Sales Order"]},
		],
	}
