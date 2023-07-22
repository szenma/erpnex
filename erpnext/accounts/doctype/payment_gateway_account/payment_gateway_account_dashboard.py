def get_data():
	return {
		"fieldname": "payment_gateway_account",
		"non_standard_fieldnames": {"Subscription Plan": "payment_gateway"},
		"transactions": [{"products": ["Payment Request"]}, {"products": ["Subscription Plan"]}],
	}
