// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

cur_frm.add_fetch("customer", "customer_name", "customer_name")
cur_frm.add_fetch("supplier", "supplier_name", "supplier_name")

cur_frm.add_fetch("product_code", "product_name", "product_name")
cur_frm.add_fetch("product_code", "description", "description")
cur_frm.add_fetch("product_code", "product_group", "product_group")
cur_frm.add_fetch("product_code", "brand", "brand")

cur_frm.cscript.onload = function() {
	cur_frm.set_query("product_code", function() {
		return erpnext.queries.product({"is_stock_product": 1, "has_serial_no": 1})
	});
};

frappe.ui.form.on("Serial No", "refresh", function(frm) {
	frm.toggle_enable("product_code", frm.doc.__islocal);
});
