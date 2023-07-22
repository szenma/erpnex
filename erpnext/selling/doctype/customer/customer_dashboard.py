from frappe import _


def get_data():
	return {
		"heatmap": True,
		"heatmap_message": _(
			"This is based on transactions against this Customer. See timeline below for details"
		),
		"fieldname": "customer",
		"non_standard_fieldnames": {
			"Payment Entry": "party",
			"Quotation": "party_name",
			"Opportunity": "party_name",
			"Bank Account": "party",
			"Subscription": "party",
		},
		"dynamic_links": {"party_name": ["Customer", "quotation_to"]},
		"transactions": [
			{"label": _("Pre Sales"), "products": ["Opportunity", "Quotation"]},
			{"label": _("Orders"), "products": ["Sales Order", "Delivery Note", "Sales Invoice"]},
			{"label": _("Payments"), "products": ["Payment Entry", "Bank Account"]},
			{
				"label": _("Support"),
				"products": ["Issue", "Maintenance Visit", "Installation Note", "Warranty Claim"],
			},
			{"label": _("Projects"), "products": ["Project"]},
			{"label": _("Pricing"), "products": ["Pricing Rule"]},
			{"label": _("Subscriptions"), "products": ["Subscription"]},
		],
	}
