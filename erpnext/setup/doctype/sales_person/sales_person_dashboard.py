from frappe import _


def get_data():
	return {
		"heatmap": True,
		"heatmap_message": _(
			"This is based on transactions against this Sales Person. See timeline below for details"
		),
		"fieldname": "sales_person",
		"transactions": [
			{"label": _("Sales"), "products": ["Sales Order", "Delivery Note", "Sales Invoice"]},
		],
	}
