// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Incorrect Balance Qty After Transaction"] = {
	"filters": [
		{
			label: __("Company"),
			fieldtype: "Link",
			fieldname: "company",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1
		},
		{
			label: __('Product Code'),
			fieldtype: 'Link',
			fieldname: 'product_code',
			options: 'Product'
		},
		{
			label: __('Warehouse'),
			fieldtype: 'Link',
			fieldname: 'warehouse'
		}
	]
};
