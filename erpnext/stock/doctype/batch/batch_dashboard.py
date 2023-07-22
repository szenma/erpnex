from frappe import _


def get_data():
	return {
		"fieldname": "batch_no",
		"transactions": [
			{"label": _("Buy"), "products": ["Purchase Invoice", "Purchase Receipt"]},
			{"label": _("Sell"), "products": ["Sales Invoice", "Delivery Note"]},
			{"label": _("Move"), "products": ["Stock Entry"]},
			{"label": _("Quality"), "products": ["Quality Inspection"]},
		],
	}
