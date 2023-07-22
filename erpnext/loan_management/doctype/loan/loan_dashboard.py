def get_data():
	return {
		"fieldname": "loan",
		"non_standard_fieldnames": {
			"Loan Disbursement": "against_loan",
			"Loan Repayment": "against_loan",
		},
		"transactions": [
			{"products": ["Loan Security Pledge", "Loan Security Shortfall", "Loan Disbursement"]},
			{
				"products": [
					"Loan Repayment",
					"Loan Interest Accrual",
					"Loan Write Off",
					"Loan Security Unpledge",
				]
			},
		],
	}
