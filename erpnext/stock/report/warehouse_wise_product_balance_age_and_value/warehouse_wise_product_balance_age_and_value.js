// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Warehouse wise Product Balance Age and Value"] = {
        "filters": [
{
                        "fieldname":"from_date",
                        "label": __("From Date"),
                        "fieldtype": "Date",
                        "width": "80",
                        "reqd": 1,
                        "default": frappe.datetime.add_months(frappe.datetime.get_today(), -1),
                },
                {
                        "fieldname":"to_date",
                        "label": __("To Date"),
                        "fieldtype": "Date",
                        "width": "80",
                        "reqd": 1,
                        "default": frappe.datetime.get_today()
                },
                {
                        "fieldname": "product_group",
                        "label": __("Product Group"),
                        "fieldtype": "Link",
                        "width": "80",
                        "options": "Product Group"
                },
                {
                        "fieldname": "product_code",
                        "label": __("Product"),
                        "fieldtype": "Link",
                        "width": "80",
                        "options": "Product"
                },
                {
                        "fieldname": "warehouse",
                        "label": __("Warehouse"),
                        "fieldtype": "Link",
                        "width": "80",
                        "options": "Warehouse"
                },
                {
                        "fieldname": "filter_total_zero_qty",
                        "label": __("Filter Total Zero Qty"),
                        "fieldtype": "Check",
                        "default": 1
                },
        ]
}
