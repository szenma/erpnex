from frappe import _


def get_data():
	return {
		"fieldname": "cost_center",
		"reports": [{"label": _("Reports"), "products": ["Budget Variance Report", "General Ledger"]}],
	}
