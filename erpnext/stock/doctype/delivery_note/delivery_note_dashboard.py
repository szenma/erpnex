from frappe import _


def get_data():
	return {
		"fieldname": "delivery_note",
		"non_standard_fieldnames": {
			"Stock Entry": "delivery_note_no",
			"Quality Inspection": "reference_name",
			"Auto Repeat": "reference_document",
		},
		"internal_links": {
			"Sales Order": ["products", "against_sales_order"],
			"Material Request": ["products", "material_request"],
			"Purchase Order": ["products", "purchase_order"],
		},
		"transactions": [
			{"label": _("Related"), "products": ["Sales Invoice", "Packing Slip", "Delivery Trip"]},
			{"label": _("Reference"), "products": ["Sales Order", "Shipment", "Quality Inspection"]},
			{"label": _("Returns"), "products": ["Stock Entry"]},
			{"label": _("Subscription"), "products": ["Auto Repeat"]},
			{"label": _("Internal Transfer"), "products": ["Material Request", "Purchase Order"]},
		],
	}
