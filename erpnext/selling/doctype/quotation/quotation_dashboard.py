from frappe import _


def get_data():
	return {
		"fieldname": "prevdoc_docname",
		"non_standard_fieldnames": {
			"Auto Repeat": "reference_document",
		},
		"transactions": [
			{"label": _("Sales Order"), "products": ["Sales Order"]},
			{"label": _("Subscription"), "products": ["Auto Repeat"]},
		],
	}
