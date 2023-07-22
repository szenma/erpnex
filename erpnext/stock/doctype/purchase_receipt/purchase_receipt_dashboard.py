from frappe import _


def get_data():
	return {
		"fieldname": "purchase_receipt_no",
		"non_standard_fieldnames": {
			"Purchase Invoice": "purchase_receipt",
			"Asset": "purchase_receipt",
			"Landed Cost Voucher": "receipt_document",
			"Auto Repeat": "reference_document",
			"Purchase Receipt": "return_against",
		},
		"internal_links": {
			"Material Request": ["products", "material_request"],
			"Purchase Order": ["products", "purchase_order"],
			"Project": ["products", "project"],
			"Quality Inspection": ["products", "quality_inspection"],
		},
		"transactions": [
			{"label": _("Related"), "products": ["Purchase Invoice", "Landed Cost Voucher", "Asset"]},
			{
				"label": _("Reference"),
				"products": ["Material Request", "Purchase Order", "Quality Inspection", "Project"],
			},
			{"label": _("Returns"), "products": ["Purchase Receipt"]},
			{"label": _("Subscription"), "products": ["Auto Repeat"]},
		],
	}
