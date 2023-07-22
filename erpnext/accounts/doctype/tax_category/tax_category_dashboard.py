from frappe import _


def get_data():
	return {
		"fieldname": "tax_category",
		"transactions": [
			{"label": _("Pre Sales"), "products": ["Quotation", "Supplier Quotation"]},
			{"label": _("Sales"), "products": ["Sales Invoice", "Delivery Note", "Sales Order"]},
			{"label": _("Purchase"), "products": ["Purchase Invoice", "Purchase Receipt"]},
			{"label": _("Party"), "products": ["Customer", "Supplier"]},
			{"label": _("Taxes"), "products": ["Product", "Tax Rule"]},
		],
	}
