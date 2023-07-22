// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Product Group", {
	onload: function(frm) {
		frm.list_route = "Tree/Product Group";

		//get query select product group
		frm.fields_dict['parent_product_group'].get_query = function(doc,cdt,cdn) {
			return{
				filters:[
					['Product Group', 'is_group', '=', 1],
					['Product Group', 'name', '!=', doc.product_group_name]
				]
			}
		}
		frm.fields_dict['product_group_defaults'].grid.get_field("default_discount_account").get_query = function(doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			return {
				filters: {
					'report_type': 'Profit and Loss',
					'company': row.company,
					"is_group": 0
				}
			};
		}
		frm.fields_dict["product_group_defaults"].grid.get_field("expense_account").get_query = function(doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			return {
				query: "erpnext.controllers.queries.get_expense_account",
				filters: { company: row.company }
			}
		}
		frm.fields_dict["product_group_defaults"].grid.get_field("income_account").get_query = function(doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			return {
				query: "erpnext.controllers.queries.get_income_account",
				filters: { company: row.company }
			}
		}

		frm.fields_dict["product_group_defaults"].grid.get_field("buying_cost_center").get_query = function(doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			return {
				filters: {
					"is_group": 0,
					"company": row.company
				}
			}
		}

		frm.fields_dict["product_group_defaults"].grid.get_field("selling_cost_center").get_query = function(doc, cdt, cdn) {
			const row = locals[cdt][cdn];
			return {
				filters: {
					"is_group": 0,
					"company": row.company
				}
			}
		}
	},

	refresh: function(frm) {
		frm.trigger("set_root_readonly");
		frm.add_custom_button(__("Product Group Tree"), function() {
			frappe.set_route("Tree", "Product Group");
		});

		if(!frm.is_new()) {
			frm.add_custom_button(__("Products"), function() {
				frappe.set_route("List", "Product", {"product_group": frm.doc.name});
			});
		}

		frappe.model.with_doctype('Website Product', () => {
			const web_product_meta = frappe.get_meta('Website Product');

			const valid_fields = web_product_meta.fields.filter(df =>
				['Link', 'Table MultiSelect'].includes(df.fieldtype) && !df.hidden
			).map(df =>
				({ label: df.label, value: df.fieldname })
			);

			frm.get_field("filter_fields").grid.update_docfield_property(
				'fieldname', 'options', valid_fields
			);
		});
	},

	set_root_readonly: function(frm) {
		// read-only for root product group
		frm.set_intro("");
		if(!frm.doc.parent_product_group && !frm.doc.__islocal) {
			frm.set_read_only();
			frm.set_intro(__("This is a root product group and cannot be edited."), true);
		}
	},

	page_name: frappe.utils.warn_page_name_change
});
