// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

// eslint-disable-next-line
frappe.provide("erpnext.accounts.dimensions");
{% include 'erpnext/public/js/controllers/buying.js' %};

frappe.ui.form.on('Material Request', {
	setup: function(frm) {
		frm.custom_make_buttons = {
			'Stock Entry': 'Issue Material',
			'Pick List': 'Pick List',
			'Purchase Order': 'Purchase Order',
			'Request for Quotation': 'Request for Quotation',
			'Supplier Quotation': 'Supplier Quotation',
			'Work Order': 'Work Order',
			'Purchase Receipt': 'Purchase Receipt'
		};

		// formatter for material request product
		frm.set_indicator_formatter('product_code',
			function(doc) { return (doc.stock_qty<=doc.ordered_qty) ? "green" : "orange"; });

		frm.set_query("product_code", "products", function() {
			return {
				query: "erpnext.controllers.queries.product_query"
			};
		});

		frm.set_query("from_warehouse", "products", function(doc) {
			return {
				filters: {'company': doc.company}
			};
		});

		frm.set_query("bom_no", "products", function(doc, cdt, cdn) {
			var row = locals[cdt][cdn];
			return {
				filters: {
					"product": row.product_code
				}
			}
		});
	},

	onload: function(frm) {
		// add product, if previous view was product
		erpnext.utils.add_product(frm);

		// set schedule_date
		set_schedule_date(frm);

		frm.set_query("warehouse", "products", function(doc) {
			return {
				filters: {'company': doc.company}
			};
		});

		frm.set_query("set_warehouse", function(doc){
			return {
				filters: {'company': doc.company}
			};
		});

		frm.set_query("set_from_warehouse", function(doc){
			return {
				filters: {'company': doc.company}
			};
		});

		erpnext.accounts.dimensions.setup_dimension_filters(frm, frm.doctype);
	},

	company: function(frm) {
		erpnext.accounts.dimensions.update_dimension(frm, frm.doctype);
	},

	onload_post_render: function(frm) {
		frm.get_field("products").grid.set_multiple_add("product_code", "qty");
	},

	refresh: function(frm) {
		frm.events.make_custom_buttons(frm);
		frm.toggle_reqd('customer', frm.doc.material_request_type=="Customer Provided");
	},

	set_from_warehouse: function(frm) {
		if (frm.doc.material_request_type == "Material Transfer"
			&& frm.doc.set_from_warehouse) {
			frm.doc.products.forEach(d => {
				frappe.model.set_value(d.doctype, d.name,
					"from_warehouse", frm.doc.set_from_warehouse);
			})
		}
	},

	make_custom_buttons: function(frm) {
		if (frm.doc.docstatus==0) {
			frm.add_custom_button(__("Bill of Materials"),
				() => frm.events.get_products_from_bom(frm), __("Get Products From"));
		}

		if (frm.doc.docstatus == 1 && frm.doc.status != 'Stopped') {
			let precision = frappe.defaults.get_default("float_precision");
			if (flt(frm.doc.per_ordered, precision) < 100) {
				let add_create_pick_list_button = () => {
					frm.add_custom_button(__('Pick List'),
						() => frm.events.create_pick_list(frm), __('Create'));
				}

				if (frm.doc.material_request_type === 'Material Transfer') {
					add_create_pick_list_button();
					frm.add_custom_button(__('Material Transfer'),
						() => frm.events.make_stock_entry(frm), __('Create'));

					frm.add_custom_button(__('Material Transfer (In Transit)'),
						() => frm.events.make_in_transit_stock_entry(frm), __('Create'));
				}

				if (frm.doc.material_request_type === "Material Issue") {
					frm.add_custom_button(__("Issue Material"),
						() => frm.events.make_stock_entry(frm), __('Create'));
				}

				if (frm.doc.material_request_type === "Customer Provided") {
					frm.add_custom_button(__("Material Receipt"),
						() => frm.events.make_stock_entry(frm), __('Create'));
				}

				if (frm.doc.material_request_type === "Purchase") {
					frm.add_custom_button(__('Purchase Order'),
						() => frm.events.make_purchase_order(frm), __('Create'));
				}

				if (frm.doc.material_request_type === "Purchase") {
					frm.add_custom_button(__("Request for Quotation"),
						() => frm.events.make_request_for_quotation(frm), __('Create'));
				}

				if (frm.doc.material_request_type === "Purchase") {
					frm.add_custom_button(__("Supplier Quotation"),
						() => frm.events.make_supplier_quotation(frm), __('Create'));
				}

				if (frm.doc.material_request_type === "Manufacture") {
					frm.add_custom_button(__("Work Order"),
						() => frm.events.raise_work_orders(frm), __('Create'));
				}

				frm.page.set_inner_btn_group_as_primary(__('Create'));

				// stop
				frm.add_custom_button(__('Stop'),
					() => frm.events.update_status(frm, 'Stopped'));

			}
		}

		if (frm.doc.docstatus===0) {
			frm.add_custom_button(__('Sales Order'), () => frm.events.get_products_from_sales_order(frm),
				__("Get Products From"));
		}

		if (frm.doc.docstatus == 1 && frm.doc.status == 'Stopped') {
			frm.add_custom_button(__('Re-open'), () => frm.events.update_status(frm, 'Submitted'));
		}
	},

	update_status: function(frm, stop_status) {
		frappe.call({
			method: 'erpnext.stock.doctype.material_request.material_request.update_status',
			args: { name: frm.doc.name, status: stop_status },
			callback(r) {
				if (!r.exc) {
					frm.reload_doc();
				}
			}
		});
	},

	get_products_from_sales_order: function(frm) {
		erpnext.utils.map_current_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_material_request",
			source_doctype: "Sales Order",
			target: frm,
			setters: {
				customer: frm.doc.customer || undefined,
				delivery_date: undefined,
			},
			get_query_filters: {
				docstatus: 1,
				status: ["not in", ["Closed", "On Hold"]],
				per_delivered: ["<", 99.99],
				company: frm.doc.company
			}
		});
	},

	get_product_data: function(frm, product, overwrite_warehouse=false) {
		if (product && !product.product_code) { return; }
		frm.call({
			method: "erpnext.stock.get_product_details.get_product_details",
			child: product,
			args: {
				args: {
					product_code: product.product_code,
					from_warehouse: product.from_warehouse,
					warehouse: product.warehouse,
					doctype: frm.doc.doctype,
					buying_price_list: frappe.defaults.get_default('buying_price_list'),
					currency: frappe.defaults.get_default('Currency'),
					name: frm.doc.name,
					qty: product.qty || 1,
					stock_qty: product.stock_qty,
					company: frm.doc.company,
					conversion_rate: 1,
					material_request_type: frm.doc.material_request_type,
					plc_conversion_rate: 1,
					rate: product.rate,
					uom: product.uom,
					conversion_factor: product.conversion_factor
				},
				overwrite_warehouse: overwrite_warehouse
			},
			callback: function(r) {
				const d = product;
				const qty_fields = ['actual_qty', 'projected_qty', 'min_order_qty'];

				if(!r.exc) {
					$.each(r.message, function(k, v) {
						if(!d[k] || in_list(qty_fields, k)) d[k] = v;
					});
				}
			}
		});
	},

	get_products_from_bom: function(frm) {
		var d = new frappe.ui.Dialog({
			title: __("Get Products from BOM"),
			fields: [
				{"fieldname":"bom", "fieldtype":"Link", "label":__("BOM"),
					options:"BOM", reqd: 1, get_query: function() {
						return {filters: { docstatus:1 }};
					}},
				{"fieldname":"warehouse", "fieldtype":"Link", "label":__("For Warehouse"),
					options:"Warehouse", reqd: 1},
				{"fieldname":"qty", "fieldtype":"Float", "label":__("Quantity"),
					reqd: 1, "default": 1},
				{"fieldname":"fetch_exploded", "fieldtype":"Check",
					"label":__("Fetch exploded BOM (including sub-assemblies)"), "default":1}
			],
			primary_action_label: 'Get Products',
			primary_action(values) {
				if(!values) return;
				values["company"] = frm.doc.company;
				if(!frm.doc.company) frappe.throw(__("Company field is required"));
				frappe.call({
					method: "erpnext.manufacturing.doctype.bom.bom.get_bom_products",
					args: values,
					callback: function(r) {
						if (!r.message) {
							frappe.throw(__("BOM does not contain any stock product"));
						} else {
							erpnext.utils.remove_empty_first_row(frm, "products");
							$.each(r.message, function(i, product) {
								var d = frappe.model.add_child(cur_frm.doc, "Material Request Product", "products");
								d.product_code = product.product_code;
								d.product_name = product.product_name;
								d.description = product.description;
								d.warehouse = values.warehouse;
								d.uom = product.stock_uom;
								d.stock_uom = product.stock_uom;
								d.conversion_factor = 1;
								d.qty = product.qty;
								d.project = product.project;
							});
						}
						d.hide();
						refresh_field("products");
					}
				});
			}
		});

		d.show();
	},

	make_purchase_order: function(frm) {
		frappe.prompt(
			{
				label: __('For Default Supplier (Optional)'),
				fieldname:'default_supplier',
				fieldtype: 'Link',
				options: 'Supplier',
				description: __('Select a Supplier from the Default Suppliers of the products below. On selection, a Purchase Order will be made against products belonging to the selected Supplier only.'),
				get_query: () => {
					return{
						query: "erpnext.stock.doctype.material_request.material_request.get_default_supplier_query",
						filters: {'doc': frm.doc.name}
					}
				}
			},
			(values) => {
				frappe.model.open_mapped_doc({
					method: "erpnext.stock.doctype.material_request.material_request.make_purchase_order",
					frm: frm,
					args: { default_supplier: values.default_supplier },
					run_link_triggers: true
				});
			},
			__('Enter Supplier'),
			__('Create')
		)
	},

	make_request_for_quotation: function(frm) {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_request_for_quotation",
			frm: frm,
			run_link_triggers: true
		});
	},

	make_supplier_quotation: function(frm) {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_supplier_quotation",
			frm: frm
		});
	},

	make_stock_entry: function(frm) {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_stock_entry",
			frm: frm
		});
	},

	make_in_transit_stock_entry(frm) {
		frappe.prompt(
			[
				{
					label: __('In Transit Warehouse'),
					fieldname: 'in_transit_warehouse',
					fieldtype: 'Link',
					options: 'Warehouse',
					reqd: 1,
					get_query: () => {
						return{
							filters: {
								'company': frm.doc.company,
								'is_group': 0,
								'warehouse_type': 'Transit'
							}
						}
					}
				}
			],
			(values) => {
				frappe.call({
					method: "erpnext.stock.doctype.material_request.material_request.make_in_transit_stock_entry",
					args: {
						source_name: frm.doc.name,
						in_transit_warehouse: values.in_transit_warehouse
					},
					callback: function(r) {
						if (r.message) {
							let doc = frappe.model.sync(r.message);
							frappe.set_route('Form', doc[0].doctype, doc[0].name);
						}
					}
				})
			},
			__('In Transit Transfer'),
			__('Create Stock Entry')
		)
	},

	create_pick_list: (frm) => {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.create_pick_list",
			frm: frm
		});
	},

	raise_work_orders: function(frm) {
		frappe.call({
			method:"erpnext.stock.doctype.material_request.material_request.raise_work_orders",
			args: {
				"material_request": frm.doc.name
			},
			freeze: true,
			callback: function(r) {
				if(r.message.length) {
					frm.reload_doc();
				}
			}
		});
	},
	material_request_type: function(frm) {
		frm.toggle_reqd('customer', frm.doc.material_request_type=="Customer Provided");

		if (frm.doc.material_request_type !== 'Material Transfer' && frm.doc.set_from_warehouse) {
			frm.set_value('set_from_warehouse', '');
		}
	},

});

frappe.ui.form.on("Material Request Product", {
	qty: function (frm, doctype, name) {
		const product = locals[doctype][name];
		if (flt(product.qty) < flt(product.min_order_qty)) {
			frappe.msgprint(__("Warning: Material Requested Qty is less than Minimum Order Qty"));
		}
		frm.events.get_product_data(frm, product, false);
	},

	from_warehouse: function(frm, doctype, name) {
		const product = locals[doctype][name];
		frm.events.get_product_data(frm, product, false);
	},

	warehouse: function(frm, doctype, name) {
		const product = locals[doctype][name];
		frm.events.get_product_data(frm, product, false);
	},

	rate: function(frm, doctype, name) {
		const product = locals[doctype][name];
		frm.events.get_product_data(frm, product, false);
	},

	product_code: function(frm, doctype, name) {
		const product = locals[doctype][name];
		product.rate = 0;
		product.uom = '';
		set_schedule_date(frm);
		frm.events.get_product_data(frm, product, true);
	},

	schedule_date: function(frm, cdt, cdn) {
		var row = locals[cdt][cdn];
		if (row.schedule_date) {
			if(!frm.doc.schedule_date) {
				erpnext.utils.copy_value_in_all_rows(frm.doc, cdt, cdn, "products", "schedule_date");
			} else {
				set_schedule_date(frm);
			}
		}
	}
});

erpnext.buying.MaterialRequestController = class MaterialRequestController extends erpnext.buying.BuyingController {
	tc_name() {
		this.get_terms();
	}

	product_code() {
		// to override product code trigger from transaction.js
	}

	validate_company_and_party() {
		return true;
	}

	calculate_taxes_and_totals() {
		return;
	}

	validate() {
		set_schedule_date(this.frm);
	}

	onload(doc, cdt, cdn) {
		this.frm.set_query("product_code", "products", function() {
			if (doc.material_request_type == "Customer Provided") {
				return{
					query: "erpnext.controllers.queries.product_query",
					filters:{
						'customer': me.frm.doc.customer,
						'is_stock_product':1
					}
				}
			} else if (doc.material_request_type == "Purchase") {
				return{
					query: "erpnext.controllers.queries.product_query",
					filters: {'is_purchase_product': 1}
				}
			} else {
				return{
					query: "erpnext.controllers.queries.product_query",
					filters: {'is_stock_product': 1}
				}
			}
		});
	}

	products_add(doc, cdt, cdn) {
		var row = frappe.get_doc(cdt, cdn);
		if(doc.schedule_date) {
			row.schedule_date = doc.schedule_date;
			refresh_field("schedule_date", cdn, "products");
		} else {
			this.frm.script_manager.copy_from_first_row("products", row, ["schedule_date"]);
		}
	}

	products_on_form_rendered() {
		set_schedule_date(this.frm);
	}

	schedule_date() {
		set_schedule_date(this.frm);
	}
};

// for backward compatibility: combine new and previous states
extend_cscript(cur_frm.cscript, new erpnext.buying.MaterialRequestController({frm: cur_frm}));

function set_schedule_date(frm) {
	if(frm.doc.schedule_date){
		erpnext.utils.copy_value_in_all_rows(frm.doc, frm.doc.doctype, frm.doc.name, "products", "schedule_date");
	}
}
