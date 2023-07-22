// Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Website Product', {
	onload: (frm) => {
		// should never check Private
		frm.fields_dict["website_image"].df.is_private = 0;

		frm.set_query("website_warehouse", () => {
			return {
				filters: {"is_group": 0}
			};
		});
	},

	refresh: (frm) => {
		frm.add_custom_button(__("Prices"), function() {
			frappe.set_route("List", "Product Price", {"product_code": frm.doc.product_code});
		}, __("View"));

		frm.add_custom_button(__("Stock"), function() {
			frappe.route_options = {
				"product_code": frm.doc.product_code
			};
			frappe.set_route("query-report", "Stock Balance");
		}, __("View"));

		frm.add_custom_button(__("E Commerce Settings"), function() {
			frappe.set_route("Form", "E Commerce Settings");
		}, __("View"));
	},

	copy_from_product_group: (frm) => {
		return frm.call({
			doc: frm.doc,
			method: "copy_specification_from_product_group"
		});
	},

	set_meta_tags: (frm) => {
		frappe.utils.set_meta_tag(frm.doc.route);
	}
});
