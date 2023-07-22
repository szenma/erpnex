from frappe import _


def get_data():
	return {
		"fieldname": "fiscal_year",
		"transactions": [
			{"label": _("Budgets"), "products": ["Budget"]},
			{"label": _("References"), "products": ["Period Closing Voucher"]},
			{
				"label": _("Target Details"),
				"products": ["Sales Person", "Sales Partner", "Territory", "Monthly Distribution"],
			},
		],
	}
