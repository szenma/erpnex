from frappe import _


def get_data():
	return {
		"fieldname": "supplier_quotation",
		"non_standard_fieldnames": {"Auto Repeat": "reference_document"},
		"internal_links": {
			"Material Request": ["products", "material_request"],
			"Request for Quotation": ["products", "request_for_quotation"],
			"Project": ["products", "project"],
		},
		"transactions": [
			{"label": _("Related"), "products": ["Purchase Order", "Quotation"]},
			{"label": _("Reference"), "products": ["Material Request", "Request for Quotation", "Project"]},
			{"label": _("Subscription"), "products": ["Auto Repeat"]},
		],
	}
