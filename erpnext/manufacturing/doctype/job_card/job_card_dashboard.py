from frappe import _


def get_data():
	return {
		"fieldname": "job_card",
		"non_standard_fieldnames": {"Quality Inspection": "reference_name"},
		"transactions": [
			{"label": _("Transactions"), "products": ["Material Request", "Stock Entry"]},
			{"label": _("Reference"), "products": ["Quality Inspection"]},
		],
	}
