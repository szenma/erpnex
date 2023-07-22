frappe.provide("erpnext.bulk_transaction_processing");

$.extend(erpnext.bulk_transaction_processing, {
	create: function(listview, from_doctype, to_doctype) {
		let checked_products = listview.get_checked_products();
		const doc_name = [];
		checked_products.forEach((Product)=> {
			if (Product.docstatus == 0) {
				doc_name.push(Product.name);
			}
		});

		let count_of_rows = checked_products.length;
		frappe.confirm(__("Create {0} {1} ?", [count_of_rows, to_doctype]), ()=>{
			if (doc_name.length == 0) {
				frappe.call({
					method: "erpnext.utilities.bulk_transaction.transaction_processing",
					args: {data: checked_products, from_doctype: from_doctype, to_doctype: to_doctype}
				}).then(()=> {

				});
				if (count_of_rows > 10) {
					frappe.show_alert("Starting a background job to create {0} {1}", [count_of_rows, to_doctype]);
				}
			} else {
				frappe.msgprint(__("Selected document must be in submitted state"));
			}
		});
	}
});