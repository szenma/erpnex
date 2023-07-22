from frappe import _


def get_data():
	return {
		"fieldname": "product_tax_template",
		"transactions": [
			{"label": _("Pre Sales"), "products": ["Quotation", "Supplier Quotation"]},
			{"label": _("Sales"), "products": ["Sales Invoice", "Sales Order", "Delivery Note"]},
			{"label": _("Purchase"), "products": ["Purchase Invoice", "Purchase Order", "Purchase Receipt"]},
			{"label": _("Stock"), "products": ["Product Groups", "Product"]},
		],
	}
