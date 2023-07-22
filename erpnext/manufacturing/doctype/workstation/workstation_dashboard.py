from frappe import _


def get_data():
	return {
		"fieldname": "workstation",
		"transactions": [
			{"label": _("Master"), "products": ["BOM", "Routing", "Operation"]},
			{
				"label": _("Transaction"),
				"products": [
					"Work Order",
					"Job Card",
				],
			},
		],
		"disable_create_buttons": [
			"BOM",
			"Routing",
			"Operation",
			"Work Order",
			"Job Card",
		],
	}
