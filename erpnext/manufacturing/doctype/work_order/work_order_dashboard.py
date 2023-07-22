from frappe import _


def get_data():
	return {
		"fieldname": "work_order",
		"non_standard_fieldnames": {"Batch": "reference_name"},
		"transactions": [
			{"label": _("Transactions"), "products": ["Stock Entry", "Job Card", "Pick List"]},
			{"label": _("Reference"), "products": ["Serial No", "Batch", "Material Request"]},
		],
	}
