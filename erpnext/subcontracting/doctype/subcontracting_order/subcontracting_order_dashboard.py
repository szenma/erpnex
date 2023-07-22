from frappe import _


def get_data():
	return {
		"fieldname": "subcontracting_order",
		"transactions": [{"label": _("Reference"), "products": ["Subcontracting Receipt", "Stock Entry"]}],
	}
