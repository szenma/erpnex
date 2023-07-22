from frappe import _


def get_data():
	return {
		"fieldname": "process_pr",
		"transactions": [
			{
				"label": _("Reconciliation Logs"),
				"products": [
					"Process Payment Reconciliation Log",
				],
			},
		],
	}
