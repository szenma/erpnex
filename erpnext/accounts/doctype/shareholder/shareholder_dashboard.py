def get_data():
	return {
		"fieldname": "shareholder",
		"non_standard_fieldnames": {"Share Transfer": "to_shareholder"},
		"transactions": [{"products": ["Share Transfer"]}],
	}
