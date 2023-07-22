from frappe import _


def get_data():
	return {
		"fieldname": "subcontracting_receipt_no",
		"non_standard_fieldnames": {
			"Subcontracting Receipt": "return_against",
		},
		"internal_links": {
			"Subcontracting Order": ["products", "subcontracting_order"],
			"Project": ["products", "project"],
			"Quality Inspection": ["products", "quality_inspection"],
		},
		"transactions": [
			{"label": _("Reference"), "products": ["Subcontracting Order", "Quality Inspection", "Project"]},
			{"label": _("Returns"), "products": ["Subcontracting Receipt"]},
		],
	}
