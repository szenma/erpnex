from frappe import _


def get_data():
	return {
		"fieldname": "operation",
		"transactions": [{"label": _("Manufacture"), "products": ["BOM", "Work Order", "Job Card"]}],
	}
