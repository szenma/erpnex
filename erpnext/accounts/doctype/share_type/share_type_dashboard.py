from frappe import _


def get_data():
	return {
		"fieldname": "share_type",
		"transactions": [{"label": _("References"), "products": ["Share Transfer", "Shareholder"]}],
	}
