frappe.listview_settings['Issue'] = {
	colwidths: {"subject": 6},
	add_fields: ['priority'],
	filters: [["status", "=", "Open"]],
	onload: function(listview) {
		var method = "erpnext.support.doctype.issue.issue.set_multiple_status";

		listview.page.add_action_product(__("Set as Open"), function() {
			listview.call_for_selected_products(method, {"status": "Open"});
		});

		listview.page.add_action_product(__("Set as Closed"), function() {
			listview.call_for_selected_products(method, {"status": "Closed"});
		});
	},
	get_indicator: function(doc) {
		if (doc.status === 'Open') {
			const color = {
				'Low': 'yellow',
				'Medium': 'orange',
				'High': 'red'
			};
			return [__(doc.status), color[doc.priority] || 'red', `status,=,Open`];
		} else if (doc.status === 'Closed') {
			return [__(doc.status), "green", "status,=," + doc.status];
		} else {
			return [__(doc.status), "gray", "status,=," + doc.status];
		}
	}
}
