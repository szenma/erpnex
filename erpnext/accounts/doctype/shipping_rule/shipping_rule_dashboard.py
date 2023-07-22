from frappe import _


def get_data():
	return {
		"fieldname": "shipping_rule",
		"non_standard_fieldnames": {"Payment Entry": "party_name"},
		"transactions": [
			{"label": _("Pre Sales"), "products": ["Quotation", "Supplier Quotation"]},
			{"label": _("Sales"), "products": ["Sales Order", "Delivery Note", "Sales Invoice"]},
			{"label": _("Purchase"), "products": ["Purchase Invoice", "Purchase Order", "Purchase Receipt"]},
		],
	}
