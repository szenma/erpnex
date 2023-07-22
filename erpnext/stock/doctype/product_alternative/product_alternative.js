// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Product Alternative', {
	setup: function(frm) {
		frm.fields_dict.product_code.get_query = () => {
			return {
				filters: {
					'allow_alternative_product': 1
				}
			};
		};
	}
});
