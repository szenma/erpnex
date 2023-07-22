from frappe import _


def get_data():
	return {
		"heatmap": True,
		"heatmap_message": _(
			"This is based on transactions against this Supplier. See timeline below for details"
		),
		"fieldname": "supplier",
		"non_standard_fieldnames": {"Payment Entry": "party_name", "Bank Account": "party"},
		"transactions": [
			{"label": _("Procurement"), "products": ["Request for Quotation", "Supplier Quotation"]},
			{"label": _("Orders"), "products": ["Purchase Order", "Purchase Receipt", "Purchase Invoice"]},
			{"label": _("Payments"), "products": ["Payment Entry", "Bank Account"]},
			{"label": _("Pricing"), "products": ["Pricing Rule"]},
		],
	}
