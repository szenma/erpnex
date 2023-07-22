from frappe import _


def get_data():
	return {
		"fieldname": "production_plan",
		"transactions": [
			{"label": _("Transactions"), "products": ["Work Order", "Material Request"]},
			{"label": _("Subcontract"), "products": ["Purchase Order"]},
		],
	}
