from frappe import _


def get_data():
	return {
		"fieldname": "reference_name",
		"internal_links": {"Sales Invoice": ["invoices", "sales_invoice"]},
		"transactions": [
			{"label": _("Reference"), "products": ["Sales Invoice"]},
			{"label": _("Payment"), "products": ["Payment Entry", "Journal Entry"]},
		],
	}
