from frappe import _


def get_data():
	return {
		"fieldname": "finance_book",
		"non_standard_fieldnames": {"Asset": "default_finance_book", "Company": "default_finance_book"},
		"transactions": [
			{"label": _("Assets"), "products": ["Asset", "Asset Value Adjustment"]},
			{"products": ["Company"]},
			{"products": ["Journal Entry"]},
		],
	}
