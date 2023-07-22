from frappe import _


def get_data():
	return {
		"fieldname": "taxes_and_charges",
		"non_standard_fieldnames": {
			"Tax Rule": "purchase_tax_template",
		},
		"transactions": [
			{
				"label": _("Transactions"),
				"products": ["Purchase Invoice", "Purchase Order", "Purchase Receipt"],
			},
			{"label": _("References"), "products": ["Supplier Quotation", "Tax Rule"]},
		],
	}
