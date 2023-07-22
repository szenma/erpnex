from frappe import _


def get_data():
	return {"fieldname": "issue", "transactions": [{"label": _("Activity"), "products": ["Task"]}]}
