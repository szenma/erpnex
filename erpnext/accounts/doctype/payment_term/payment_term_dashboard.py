from frappe import _


def get_data():
	return {
		"fieldname": "payment_term",
		"transactions": [
			{"label": _("Sales"), "products": ["Sales Invoice", "Sales Order", "Quotation"]},
			{"label": _("Purchase"), "products": ["Purchase Invoice", "Purchase Order"]},
			{"products": ["Payment Terms Template"]},
		],
	}
