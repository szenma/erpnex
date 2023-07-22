from frappe import _


def get_data():
	return {
		"heatmap": True,
		"heatmap_message": _("This is based on the Time Sheets created against this project"),
		"fieldname": "project",
		"transactions": [
			{
				"label": _("Project"),
				"products": ["Task", "Timesheet", "Issue", "Project Update"],
			},
			{"label": _("Material"), "products": ["Material Request", "BOM", "Stock Entry"]},
			{"label": _("Sales"), "products": ["Sales Order", "Delivery Note", "Sales Invoice"]},
			{"label": _("Purchase"), "products": ["Purchase Order", "Purchase Receipt", "Purchase Invoice"]},
		],
	}
