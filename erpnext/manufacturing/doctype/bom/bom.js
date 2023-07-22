// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.bom");

frappe.ui.form.on("BOM", {
	setup(frm) {
		frm.custom_make_buttons = {
			'Work Order': 'Work Order',
			'Quality Inspection': 'Quality Inspection'
		};

		frm.set_query("bom_no", "products", function() {
			return {
				filters: {
					'currency': frm.doc.currency,
					'company': frm.doc.company
				}
			};
		});

		frm.set_query("source_warehouse", "products", function() {
			return {
				filters: {
					'company': frm.doc.company
				}
			};
		});

		frm.set_query("product", function() {
			return {
				query: "erpnext.manufacturing.doctype.bom.bom.product_query",
				filters: {
					"is_stock_product": 1
				}
			};
		});

		frm.set_query("project", function() {
			return{
				filters:[
					['Project', 'status', 'not in', 'Completed, Cancelled']
				]
			};
		});

		frm.set_query("product_code", "products", function(doc) {
			return {
				query: "erpnext.manufacturing.doctype.bom.bom.product_query",
				filters: {
					"include_product_in_manufacturing": 1,
					"is_fixed_asset": 0
				}
			};
		});

		frm.set_query("bom_no", "products", function(doc, cdt, cdn) {
			var d = locals[cdt][cdn];
			return {
				filters: {
					'product': d.product_code,
					'is_active': 1,
					'docstatus': 1
				}
			};
		});
	},

	validate: function(frm) {
		if (frm.doc.fg_based_operating_cost && frm.doc.with_operations) {
			frappe.throw({message: __("Please check either with operations or FG Based Operating Cost."), title: __("Mandatory")});
		}
	},

	with_operations: function(frm) {
		frm.set_df_property("fg_based_operating_cost", "hidden", frm.doc.with_operations ? 1 : 0);
	},

	fg_based_operating_cost: function(frm) {
		frm.set_df_property("with_operations", "hidden", frm.doc.fg_based_operating_cost ? 1 : 0);
	},

	onload_post_render: function(frm) {
		frm.get_field("products").grid.set_multiple_add("product_code", "qty");
	},

	refresh(frm) {
		frm.toggle_enable("product", frm.doc.__islocal);

		frm.set_indicator_formatter('product_code',
			function(doc) {
				if (doc.original_product){
					return (doc.product_code != doc.original_product) ? "orange" : ""
				}
				return ""
			}
		)

		if (!frm.is_new() && frm.doc.docstatus<2) {
			frm.add_custom_button(__("Update Cost"), function() {
				frm.events.update_cost(frm, true);
			});
			frm.add_custom_button(__("Browse BOM"), function() {
				frappe.route_options = {
					"bom": frm.doc.name
				};
				frappe.set_route("Tree", "BOM");
			});
		}

		if (!frm.is_new() && !frm.doc.docstatus == 0) {
			frm.add_custom_button(__("New Version"), function() {
				let new_bom = frappe.model.copy_doc(frm.doc);
				frappe.set_route("Form", "BOM", new_bom.name);
			});
		}

		if(frm.doc.docstatus==1) {
			frm.add_custom_button(__("Work Order"), function() {
				frm.trigger("make_work_order");
			}, __("Create"));

			if (frm.doc.has_variants) {
				frm.add_custom_button(__("Variant BOM"), function() {
					frm.trigger("make_variant_bom");
				}, __("Create"));
			}

			if (frm.doc.inspection_required) {
				frm.add_custom_button(__("Quality Inspection"), function() {
					frm.trigger("make_quality_inspection");
				}, __("Create"));
			}

			frm.page.set_inner_btn_group_as_primary(__('Create'));
		}

		if(frm.doc.products && frm.doc.allow_alternative_product) {
			const has_alternative = frm.doc.products.find(i => i.allow_alternative_product === 1);
			if (frm.doc.docstatus == 0 && has_alternative) {
				frm.add_custom_button(__('Alternate Product'), () => {
					erpnext.utils.select_alternate_products({
						frm: frm,
						child_docname: "products",
						warehouse_field: "source_warehouse",
						child_doctype: "BOM Product",
						original_product_field: "original_product",
						condition: (d) => {
							if (d.allow_alternative_product) {return true;}
						}
					})
				});
			}
		}


		if (frm.doc.has_variants) {
			frm.set_intro(__('This is a Template BOM and will be used to make the work order for {0} of the product {1}',
				[
					`<a class="variants-intro">variants</a>`,
					`<a href="/app/product/${frm.doc.product}">${frm.doc.product}</a>`,
				]), true);

			frm.$wrapper.find(".variants-intro").on("click", () => {
				frappe.set_route("List", "Product", {"variant_of": frm.doc.product});
			});
		}
	},

	make_work_order(frm) {
		frm.events.setup_variant_prompt(frm, "Work Order", (frm, product, data, variant_products) => {
			frappe.call({
				method: "erpnext.manufacturing.doctype.work_order.work_order.make_work_order",
				args: {
					bom_no: frm.doc.name,
					product: product,
					qty: data.qty || 0.0,
					project: frm.doc.project,
					variant_products: variant_products
				},
				freeze: true,
				callback(r) {
					if(r.message) {
						let doc = frappe.model.sync(r.message)[0];
						frappe.set_route("Form", doc.doctype, doc.name);
					}
				}
			});
		});
	},

	make_variant_bom(frm) {
		frm.events.setup_variant_prompt(frm, "Variant BOM", (frm, product, data, variant_products) => {
			frappe.call({
				method: "erpnext.manufacturing.doctype.bom.bom.make_variant_bom",
				args: {
					source_name: frm.doc.name,
					bom_no: frm.doc.name,
					product: product,
					variant_products: variant_products
				},
				freeze: true,
				callback(r) {
					if(r.message) {
						let doc = frappe.model.sync(r.message)[0];
						frappe.set_route("Form", doc.doctype, doc.name);
					}
				}
			});
		}, true);
	},

	setup_variant_prompt(frm, title, callback, skip_qty_field) {
		const fields = [];

		if (frm.doc.has_variants) {
			fields.push({
				fieldtype: 'Link',
				label: __('Variant Product'),
				fieldname: 'product',
				options: "Product",
				reqd: 1,
				get_query() {
					return {
						query: "erpnext.controllers.queries.product_query",
						filters: {
							"variant_of": frm.doc.product
						}
					};
				}
			});
		}

		if (!skip_qty_field) {
			fields.push({
				fieldtype: 'Float',
				label: __('Qty To Manufacture'),
				fieldname: 'qty',
				reqd: 1,
				default: 1,
				onchange: () => {
					const { quantity, products: rm } = frm.doc;
					const variant_products_map = rm.reduce((acc, product) => {
						acc[product.product_code] = product.qty;
						return acc;
					}, {});
					const mf_qty = cur_dialog.fields_list.filter(
						(f) => f.df.fieldname === "qty"
					)[0]?.value;
					const products = cur_dialog.fields.filter(
						(f) => f.fieldname === "products"
					)[0]?.data;

					if (!products) {
						return;
					}

					products.forEach((product) => {
						product.qty =
							(variant_products_map[product.product_code] * mf_qty) /
							quantity;
					});

					cur_dialog.refresh();
				}
			});
		}

		var has_template_rm = frm.doc.products.filter(d => d.has_variants === 1) || [];
		if (has_template_rm && has_template_rm.length > 0) {
			fields.push({
				fieldname: "products",
				fieldtype: "Table",
				label: __("Raw Materials"),
				fields: [
					{
						fieldname: "product_code",
						options: "Product",
						label: __("Template Product"),
						fieldtype: "Link",
						in_list_view: 1,
						reqd: 1,
					},
					{
						fieldname: "variant_product_code",
						options: "Product",
						label: __("Variant Product"),
						fieldtype: "Link",
						in_list_view: 1,
						reqd: 1,
						get_query(data) {
							if (!data.product_code) {
								frappe.throw(__("Select template product"));
							}

							return {
								query: "erpnext.controllers.queries.product_query",
								filters: {
									"variant_of": data.product_code
								}
							};
						}
					},
					{
						fieldname: "qty",
						label: __("Quantity"),
						fieldtype: "Float",
						in_list_view: 1,
						reqd: 1,
					},
					{
						fieldname: "source_warehouse",
						label: __("Source Warehouse"),
						fieldtype: "Link",
						options: "Warehouse"
					},
					{
						fieldname: "operation",
						label: __("Operation"),
						fieldtype: "Data",
						hidden: 1,
					}
				],
				in_place_edit: true,
				data: [],
				get_data () {
					return [];
				},
			});
		}

		let dialog = frappe.prompt(fields, data => {
			let product = data.product || frm.doc.product;
			let variant_products = data.products || [];

			variant_products.forEach(d => {
				if (!d.variant_product_code) {
					frappe.throw(__("Select variant product code for the template product {0}", [d.product_code]));
				}
			})

			callback(frm, product, data, variant_products);

		}, __(title), __("Create"));

		has_template_rm.forEach(d => {
			dialog.fields_dict.products.df.data.push({
				"product_code": d.product_code,
				"variant_product_code": "",
				"qty": d.qty,
				"source_warehouse": d.source_warehouse,
				"operation": d.operation
			});
		});

		if (has_template_rm && has_template_rm.length) {
			dialog.fields_dict.products.grid.refresh();
		}
	},

	make_quality_inspection(frm) {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.quality_inspection.quality_inspection.make_quality_inspection",
			frm: frm
		})
	},

	update_cost(frm, save_doc=false) {
		return frappe.call({
			doc: frm.doc,
			method: "update_cost",
			freeze: true,
			args: {
				update_parent: true,
				save: save_doc,
				from_child_bom: false
			},
			callback(r) {
				refresh_field("products");
				if(!r.exc) frm.refresh_fields();
			}
		});
	},

	rm_cost_as_per(frm) {
		if (in_list(["Valuation Rate", "Last Purchase Rate"], frm.doc.rm_cost_as_per)) {
			frm.set_value("plc_conversion_rate", 1.0);
		}
	},

	routing(frm) {
		if (frm.doc.routing) {
			frappe.call({
				doc: frm.doc,
				method: "get_routing",
				freeze: true,
				callback(r) {
					if (!r.exc) {
						frm.refresh_fields();
						erpnext.bom.calculate_op_cost(frm.doc);
						erpnext.bom.calculate_total(frm.doc);
					}
				}
			});
		}
	},

	process_loss_percentage(frm) {
		let qty = 0.0
		if (frm.doc.process_loss_percentage) {
			qty = (frm.doc.quantity * frm.doc.process_loss_percentage) / 100;
		}

		frm.set_value("process_loss_qty", qty);
	}
});

erpnext.bom.BomController = class BomController extends erpnext.TransactionController {
	conversion_rate(doc) {
		if(this.frm.doc.currency === this.get_company_currency()) {
			this.frm.set_value("conversion_rate", 1.0);
		} else {
			erpnext.bom.update_cost(doc);
		}
	}

	product_code(doc, cdt, cdn){
		var scrap_products = false;
		var child = locals[cdt][cdn];
		if (child.doctype == 'BOM Scrap Product') {
			scrap_products = true;
		}

		if (child.bom_no) {
			child.bom_no = '';
		}

		get_bom_material_detail(doc, cdt, cdn, scrap_products);
	}

	buying_price_list(doc) {
		this.apply_price_list();
	}

	plc_conversion_rate(doc) {
		if (!this.in_apply_price_list) {
			this.apply_price_list(null, true);
		}
	}

	conversion_factor(doc, cdt, cdn) {
		if (frappe.meta.get_docfield(cdt, "stock_qty", cdn)) {
			var product = frappe.get_doc(cdt, cdn);
			frappe.model.round_floats_in(product, ["qty", "conversion_factor"]);
			product.stock_qty = flt(product.qty * product.conversion_factor, precision("stock_qty", product));
			refresh_field("stock_qty", product.name, product.parentfield);
			this.toggle_conversion_factor(product);
			this.frm.events.update_cost(this.frm);
		}
	}
};

extend_cscript(cur_frm.cscript, new erpnext.bom.BomController({frm: cur_frm}));

cur_frm.cscript.hour_rate = function(doc) {
	erpnext.bom.calculate_op_cost(doc);
	erpnext.bom.calculate_total(doc);
};

cur_frm.cscript.time_in_mins = cur_frm.cscript.hour_rate;

cur_frm.cscript.bom_no = function(doc, cdt, cdn) {
	get_bom_material_detail(doc, cdt, cdn, false);
};

cur_frm.cscript.is_default = function(doc) {
	if (doc.is_default) cur_frm.set_value("is_active", 1);
};

var get_bom_material_detail = function(doc, cdt, cdn, scrap_products) {
	if (!doc.company) {
		frappe.throw({message: __("Please select a Company first."), title: __("Mandatory")});
	}

	var d = locals[cdt][cdn];
	if (d.product_code) {
		return frappe.call({
			doc: doc,
			method: "get_bom_material_detail",
			args: {
				"company": doc.company,
				"product_code": d.product_code,
				"bom_no": d.bom_no != null ? d.bom_no: '',
				"scrap_products": scrap_products,
				"qty": d.qty,
				"stock_qty": d.stock_qty,
				"include_product_in_manufacturing": d.include_product_in_manufacturing,
				"uom": d.uom,
				"stock_uom": d.stock_uom,
				"conversion_factor": d.conversion_factor,
				"sourced_by_supplier": d.sourced_by_supplier,
				"do_not_explode": d.do_not_explode
			},
			callback: function(r) {
				d = locals[cdt][cdn];

				$.extend(d, r.message);
				refresh_field("products");
				refresh_field("scrap_products");

				doc = locals[doc.doctype][doc.name];
				erpnext.bom.calculate_rm_cost(doc);
				erpnext.bom.calculate_scrap_materials_cost(doc);
				erpnext.bom.calculate_total(doc);
			},
			freeze: true
		});
	}
};

cur_frm.cscript.qty = function(doc) {
	erpnext.bom.calculate_rm_cost(doc);
	erpnext.bom.calculate_scrap_materials_cost(doc);
	erpnext.bom.calculate_total(doc);
};

cur_frm.cscript.rate = function(doc, cdt, cdn) {
	var d = locals[cdt][cdn];
	const is_scrap_product = cdt == "BOM Scrap Product";

	if (d.bom_no) {
		frappe.msgprint(__("You cannot change the rate if BOM is mentioned against any Product."));
		get_bom_material_detail(doc, cdt, cdn, is_scrap_product);
	} else {
		erpnext.bom.calculate_rm_cost(doc);
		erpnext.bom.calculate_scrap_materials_cost(doc);
		erpnext.bom.calculate_total(doc);
	}
};

erpnext.bom.update_cost = function(doc) {
	erpnext.bom.calculate_op_cost(doc);
	erpnext.bom.calculate_rm_cost(doc);
	erpnext.bom.calculate_scrap_materials_cost(doc);
	erpnext.bom.calculate_total(doc);
};

erpnext.bom.calculate_op_cost = function(doc) {
	doc.operating_cost = 0.0;
	doc.base_operating_cost = 0.0;

	if(doc.with_operations) {
		doc.operations.forEach((product) => {
			let operating_cost = flt(flt(product.hour_rate) * flt(product.time_in_mins) / 60, 2);
			let base_operating_cost = flt(operating_cost * doc.conversion_rate, 2);
			frappe.model.set_value('BOM Operation',product.name, {
				"operating_cost": operating_cost,
				"base_operating_cost": base_operating_cost
			});

			doc.operating_cost += operating_cost;
			doc.base_operating_cost += base_operating_cost;
		});
	} else if(doc.fg_based_operating_cost) {
		let total_operating_cost = doc.quantity * flt(doc.operating_cost_per_bom_quantity);
		doc.operating_cost = total_operating_cost;
		doc.base_operating_cost = flt(total_operating_cost * doc.conversion_rate, 2);
	}
	refresh_field(['operating_cost', 'base_operating_cost']);
};

// rm : raw material
erpnext.bom.calculate_rm_cost = function(doc) {
	var rm = doc.products || [];
	var total_rm_cost = 0;
	var base_total_rm_cost = 0;
	for(var i=0;i<rm.length;i++) {
		var amount = flt(rm[i].rate) * flt(rm[i].qty);
		var base_amount = amount * flt(doc.conversion_rate);

		frappe.model.set_value('BOM Product', rm[i].name, 'base_rate',
			flt(rm[i].rate) * flt(doc.conversion_rate));
		frappe.model.set_value('BOM Product', rm[i].name, 'amount', amount);
		frappe.model.set_value('BOM Product', rm[i].name, 'base_amount', base_amount);
		frappe.model.set_value('BOM Product', rm[i].name,
			'qty_consumed_per_unit', flt(rm[i].stock_qty)/flt(doc.quantity));

		total_rm_cost += amount;
		base_total_rm_cost += base_amount;
	}
	cur_frm.set_value("raw_material_cost", total_rm_cost);
	cur_frm.set_value("base_raw_material_cost", base_total_rm_cost);
};

// sm : scrap material
erpnext.bom.calculate_scrap_materials_cost = function(doc) {
	var sm = doc.scrap_products || [];
	var total_sm_cost = 0;
	var base_total_sm_cost = 0;

	for(var i=0;i<sm.length;i++) {
		var base_rate = flt(sm[i].rate) * flt(doc.conversion_rate);
		var amount = flt(sm[i].rate) * flt(sm[i].stock_qty);
		var base_amount = amount * flt(doc.conversion_rate);

		frappe.model.set_value('BOM Scrap Product',sm[i].name, 'base_rate', base_rate);
		frappe.model.set_value('BOM Scrap Product',sm[i].name, 'amount', amount);
		frappe.model.set_value('BOM Scrap Product',sm[i].name, 'base_amount', base_amount);

		total_sm_cost += amount;
		base_total_sm_cost += base_amount;
	}

	cur_frm.set_value("scrap_material_cost", total_sm_cost);
	cur_frm.set_value("base_scrap_material_cost", base_total_sm_cost);
};

// Calculate Total Cost
erpnext.bom.calculate_total = function(doc) {
	var total_cost = flt(doc.operating_cost) + flt(doc.raw_material_cost) - flt(doc.scrap_material_cost);
	var base_total_cost = flt(doc.base_operating_cost) + flt(doc.base_raw_material_cost)
		- flt(doc.base_scrap_material_cost);

	cur_frm.set_value("total_cost", total_cost);
	cur_frm.set_value("base_total_cost", base_total_cost);
};

cur_frm.cscript.validate = function(doc) {
	erpnext.bom.update_cost(doc);
};

frappe.ui.form.on("BOM Operation", "operation", function(frm, cdt, cdn) {
	var d = locals[cdt][cdn];

	if(!d.operation) return;

	frappe.call({
		"method": "frappe.client.get",
		args: {
			doctype: "Operation",
			name: d.operation
		},
		callback: function (data) {
			if(data.message.description) {
				frappe.model.set_value(d.doctype, d.name, "description", data.message.description);
			}
			if(data.message.workstation) {
				frappe.model.set_value(d.doctype, d.name, "workstation", data.message.workstation);
			}
		}
	});
});

frappe.ui.form.on("BOM Operation", "workstation", function(frm, cdt, cdn) {
	var d = locals[cdt][cdn];

	frappe.call({
		"method": "frappe.client.get",
		args: {
			doctype: "Workstation",
			name: d.workstation
		},
		callback: function (data) {
			frappe.model.set_value(d.doctype, d.name, "base_hour_rate", data.message.hour_rate);
			frappe.model.set_value(d.doctype, d.name, "hour_rate",
				flt(flt(data.message.hour_rate) / flt(frm.doc.conversion_rate)), 2);

			erpnext.bom.calculate_op_cost(frm.doc);
			erpnext.bom.calculate_total(frm.doc);
		}
	});
});

frappe.ui.form.on("BOM Product", {
	do_not_explode: function(frm, cdt, cdn) {
		get_bom_material_detail(frm.doc, cdt, cdn, false);
	}
})


frappe.ui.form.on("BOM Product", "qty", function(frm, cdt, cdn) {
	var d = locals[cdt][cdn];
	d.stock_qty = d.qty * d.conversion_factor;
	refresh_field("stock_qty", d.name, d.parentfield);
});

frappe.ui.form.on("BOM Product", "product_code", function(frm, cdt, cdn) {
	var d = locals[cdt][cdn];
	frappe.db.get_value('Product', {name: d.product_code}, 'allow_alternative_product', (r) => {
		d.allow_alternative_product = r.allow_alternative_product
	})
	refresh_field("allow_alternative_product", d.name, d.parentfield);
});

frappe.ui.form.on("BOM Product", "sourced_by_supplier", function(frm, cdt, cdn) {
	var d = locals[cdt][cdn];
	if (d.sourced_by_supplier) {
		d.rate = 0;
		refresh_field("rate", d.name, d.parentfield);
	}
});

frappe.ui.form.on("BOM Product", "rate", function(frm, cdt, cdn) {
	var d = locals[cdt][cdn];
	if (d.sourced_by_supplier) {
		d.rate = 0;
		refresh_field("rate", d.name, d.parentfield);
	}
});

frappe.ui.form.on("BOM Operation", "operations_remove", function(frm) {
	erpnext.bom.calculate_op_cost(frm.doc);
	erpnext.bom.calculate_total(frm.doc);
});

frappe.ui.form.on("BOM Product", "products_remove", function(frm) {
	erpnext.bom.calculate_rm_cost(frm.doc);
	erpnext.bom.calculate_total(frm.doc);
});

frappe.tour['BOM'] = [
	{
		fieldname: "product",
		title: "Product",
		description: __("Select the Product to be manufactured. The Product name, UoM, Company, and Currency will be fetched automatically.")
	},
	{
		fieldname: "quantity",
		title: "Quantity",
		description: __("Enter the quantity of the Product that will be manufactured from this Bill of Materials.")
	},
	{
		fieldname: "with_operations",
		title: "With Operations",
		description: __("To add Operations tick the 'With Operations' checkbox.")
	},
	{
		fieldname: "products",
		title: "Raw Materials",
		description: __("Select the raw materials (Products) required to manufacture the Product")
	}
];

frappe.ui.form.on("BOM Scrap Product", {
	product_code(frm, cdt, cdn) {
		const { product_code } = locals[cdt][cdn];
	},
});

function trigger_process_loss_qty_prompt(frm, cdt, cdn, product_code) {
	frappe.prompt(
		{
			fieldname: "percent",
			fieldtype: "Percent",
			label: __("% Finished Product Quantity"),
			description:
				__("Set quantity of process loss product:") +
				` ${product_code} ` +
				__("as a percentage of finished product quantity"),
		},
		(data) => {
			const row = locals[cdt][cdn];
			row.stock_qty = (frm.doc.quantity * data.percent) / 100;
			row.qty = row.stock_qty / (row.conversion_factor || 1);
			refresh_field("scrap_products");
		},
		__("Set Process Loss Product Quantity"),
		__("Set Quantity")
	);
}
