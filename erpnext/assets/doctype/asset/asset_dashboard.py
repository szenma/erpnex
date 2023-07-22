from frappe import _


def get_data():
	return {
		"non_standard_fieldnames": {"Asset Movement": "asset"},
		"transactions": [{"label": _("Movement"), "products": ["Asset Movement"]}],
	}
