from frappe import _


def get_data():
	return {
		"fieldname": "time_sheet",
		"transactions": [{"label": _("References"), "products": ["Sales Invoice"]}],
	}
