// Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Quick Stock Balance', {

	setup: (frm) => {
		frm.set_query('product', () => {
			if (!(frm.doc.warehouse && frm.doc.date)) {
				frm.trigger('check_warehouse_and_date');
			}
		});
	},

	make_custom_stock_report_button: (frm) => {
		if (frm.doc.product) {
			frm.add_custom_button(__('Stock Balance Report'), () => {
				frappe.set_route('query-report', 'Stock Balance',
					{ 'product_code': frm.doc.product, 'warehouse': frm.doc.warehouse });
			});
		}
	},

	refresh: (frm) => {
		frm.disable_save();
		frm.trigger('make_custom_stock_report_button');
	},

	check_warehouse_and_date: (frm) => {
		frappe.msgprint(__('Please enter Warehouse and Date'));
		frm.doc.product = '';
		frm.refresh();
	},

	warehouse: (frm) => {
		if (frm.doc.product || frm.doc.product_barcode) {
			frm.trigger('get_stock_and_product_details');
		}
	},

	date: (frm) => {
		if (frm.doc.product || frm.doc.product_barcode) {
			frm.trigger('get_stock_and_product_details');
		}
	},

	product: (frm) => {
		frappe.flags.last_updated_element = 'product';
		frm.trigger('get_stock_and_product_details');
		frm.trigger('make_custom_stock_report_button');
	},

	product_barcode: (frm) => {
		frappe.flags.last_updated_element = 'product_barcode';
		frm.trigger('get_stock_and_product_details');
		frm.trigger('make_custom_stock_report_button');
	},

	get_stock_and_product_details: (frm) => {
		if (!(frm.doc.warehouse && frm.doc.date)) {
			frm.trigger('check_warehouse_and_date');
		}
		else if (frm.doc.product || frm.doc.product_barcode) {
			let filters = {
				warehouse: frm.doc.warehouse,
				date: frm.doc.date,
			};
			if (frappe.flags.last_updated_element === 'product') {
				filters = { ...filters, ...{ product: frm.doc.product }};
			}
			else {
				filters = { ...filters, ...{ barcode: frm.doc.product_barcode }};
			}
			frappe.call({
				method: 'erpnext.stock.doctype.quick_stock_balance.quick_stock_balance.get_stock_product_details',
				args: filters,
				callback: (r) => {
					if (r.message) {
						let fields = ['product', 'qty', 'value', 'image'];
						if (!r.message['barcodes'].includes(frm.doc.product_barcode)) {
							frm.doc.product_barcode = '';
							frm.refresh();
						}
						fields.forEach(function (field) {
							frm.set_value(field, r.message[field]);
						});
					}
				}
			});
		}
	}
});
