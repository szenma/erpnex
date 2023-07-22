from frappe import _


def get_data():
	return {
		"fieldname": "bank",
		"transactions": [{"label": _("Bank Details"), "products": ["Bank Account", "Bank Guarantee"]}],
	}
