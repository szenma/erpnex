erpnext.ProductSelector = class ProductSelector {
	constructor(opts) {
		$.extend(this, opts);

		if (!this.product_field) {
			this.product_field = 'product_code';
		}

		if (!this.product_query) {
			this.product_query = erpnext.queries.product().query;
		}

		this.grid = this.frm.get_field("products").grid;
		this.setup();
	}

	setup() {
		var me = this;
		if(!this.grid.add_products_button) {
			this.grid.add_products_button = this.grid.add_custom_button(__('Add Products'), function() {
				if(!me.dialog) {
					me.make_dialog();
				}
				me.dialog.show();
				me.render_products();
				setTimeout(function() { me.dialog.input.focus(); }, 1000);
			});
		}
	}

	make_dialog() {
		this.dialog = new frappe.ui.Dialog({
			title: __('Add Products')
		});
		var body = $(this.dialog.body);
		body.html('<div><p><input type="text" class="form-control"></p>\
			<br><div class="results"></div></div>');

		this.dialog.input = body.find('.form-control');
		this.dialog.results = body.find('.results');

		var me = this;
		this.dialog.results.on('click', '.image-view-product', function() {
			me.add_product($(this).attr('data-name'));
		});

		this.dialog.input.on('keyup', function() {
			if(me.timeout_id) {
				clearTimeout(me.timeout_id);
			}
			me.timeout_id = setTimeout(function() {
				me.render_products();
				me.timeout_id = undefined;
			}, 500);
		});
	}

	add_product(product_code) {
		// add row or update qty
		var added = false;

		// find row with product if exists
		$.each(this.frm.doc.products || [], (i, d) => {
			if(d[this.product_field]===product_code) {
				frappe.model.set_value(d.doctype, d.name, 'qty', d.qty + 1);
				frappe.show_alert({message: __("Added {0} ({1})", [product_code, d.qty]), indicator: 'green'});
				added = true;
				return false;
			}
		});

		if(!added) {
			var d = null;
			frappe.run_serially([
				() => { d = this.grid.add_new_row(); },
				() => frappe.model.set_value(d.doctype, d.name, this.product_field, product_code),
				() => frappe.timeout(0.1),
				() => {
					frappe.model.set_value(d.doctype, d.name, 'qty', 1);
					frappe.show_alert({message: __("Added {0} ({1})", [product_code, 1]), indicator: 'green'});
				}
			]);
		}

	}

	render_products() {
		let args = {
			query: this.product_query,
			filters: {}
		};
		args.txt = this.dialog.input.val();
		args.as_dict = 1;

		if (this.get_filters) {
			$.extend(args.filters, this.get_filters() || {});
		}

		var me = this;
		frappe.link_search("Product", args, function(r) {
			$.each(r.values, function(i, d) {
				if(!d.image) {
					d.abbr = frappe.get_abbr(d.product_name);
					d.color = frappe.get_palette(d.product_name);
				}
			});
			me.dialog.results.html(frappe.render_template('product_selector', {'data':r.values}));
		});
	}
};
