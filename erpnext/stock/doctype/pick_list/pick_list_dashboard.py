def get_data():
	return {
		"fieldname": "pick_list",
		"internal_links": {
			"Sales Order": ["locations", "sales_order"],
		},
		"transactions": [
			{"products": ["Stock Entry", "Sales Order", "Delivery Note"]},
		],
	}
