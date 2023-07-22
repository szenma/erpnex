from frappe import _


def get_data():
	return {
		"fieldname": "sales_order",
		"non_standard_fieldnames": {
			"Delivery Note": "against_sales_order",
			"Journal Entry": "reference_name",
			"Payment Entry": "reference_name",
			"Payment Request": "reference_name",
			"Auto Repeat": "reference_document",
			"Maintenance Visit": "prevdoc_docname",
		},
		"internal_links": {
			"Quotation": ["products", "prevdoc_docname"],
		},
		"transactions": [
			{
				"label": _("Fulfillment"),
				"products": ["Sales Invoice", "Pick List", "Delivery Note", "Maintenance Visit"],
			},
			{"label": _("Purchasing"), "products": ["Material Request", "Purchase Order"]},
			{"label": _("Projects"), "products": ["Project"]},
			{"label": _("Manufacturing"), "products": ["Work Order"]},
			{"label": _("Reference"), "products": ["Quotation", "Auto Repeat"]},
			{"label": _("Payment"), "products": ["Payment Entry", "Payment Request", "Journal Entry"]},
		],
	}
