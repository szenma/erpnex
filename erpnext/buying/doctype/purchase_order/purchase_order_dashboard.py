from frappe import _


def get_data():
	return {
		"fieldname": "purchase_order",
		"non_standard_fieldnames": {
			"Journal Entry": "reference_name",
			"Payment Entry": "reference_name",
			"Payment Request": "reference_name",
			"Auto Repeat": "reference_document",
		},
		"internal_links": {
			"Material Request": ["products", "material_request"],
			"Supplier Quotation": ["products", "supplier_quotation"],
			"Project": ["products", "project"],
		},
		"transactions": [
			{"label": _("Related"), "products": ["Purchase Receipt", "Purchase Invoice"]},
			{"label": _("Payment"), "products": ["Payment Entry", "Journal Entry", "Payment Request"]},
			{
				"label": _("Reference"),
				"products": ["Material Request", "Supplier Quotation", "Project", "Auto Repeat"],
			},
			{"label": _("Sub-contracting"), "products": ["Subcontracting Order", "Stock Entry"]},
			{"label": _("Internal"), "products": ["Sales Order"]},
		],
	}
