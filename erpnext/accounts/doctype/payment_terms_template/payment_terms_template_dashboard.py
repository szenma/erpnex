from frappe import _


def get_data():
	return {
		"fieldname": "payment_terms_template",
		"non_standard_fieldnames": {
			"Customer Group": "payment_terms",
			"Supplier Group": "payment_terms",
			"Supplier": "payment_terms",
			"Customer": "payment_terms",
		},
		"transactions": [
			{"label": _("Sales"), "products": ["Sales Invoice", "Sales Order", "Quotation"]},
			{"label": _("Purchase"), "products": ["Purchase Invoice", "Purchase Order"]},
			{"label": _("Party"), "products": ["Customer", "Supplier"]},
			{"label": _("Group"), "products": ["Customer Group", "Supplier Group"]},
		],
	}
