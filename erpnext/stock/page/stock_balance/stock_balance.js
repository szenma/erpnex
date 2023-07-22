frappe.pages['stock-balance'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Stock Summary'),
		single_column: true
	});
	page.start = 0;

	page.warehouse_field = page.add_field({
		fieldname: 'warehouse',
		label: __('Warehouse'),
		fieldtype:'Link',
		options:'Warehouse',
		change: function() {
			page.product_dashboard.start = 0;
			page.product_dashboard.refresh();
		}
	});

	page.product_field = page.add_field({
		fieldname: 'product_code',
		label: __('Product'),
		fieldtype:'Link',
		options:'Product',
		change: function() {
			page.product_dashboard.start = 0;
			page.product_dashboard.refresh();
		}
	});

	page.product_group_field = page.add_field({
		fieldname: 'product_group',
		label: __('Product Group'),
		fieldtype:'Link',
		options:'Product Group',
		change: function() {
			page.product_dashboard.start = 0;
			page.product_dashboard.refresh();
		}
	});

	page.sort_selector = new frappe.ui.SortSelector({
		parent: page.wrapper.find('.page-form'),
		args: {
			sort_by: 'projected_qty',
			sort_order: 'asc',
			options: [
				{fieldname: 'projected_qty', label: __('Projected qty')},
				{fieldname: 'reserved_qty', label: __('Reserved for sale')},
				{fieldname: 'reserved_qty_for_production', label: __('Reserved for manufacturing')},
				{fieldname: 'reserved_qty_for_sub_contract', label: __('Reserved for sub contracting')},
				{fieldname: 'actual_qty', label: __('Actual qty in stock')},
			]
		},
		change: function(sort_by, sort_order) {
			page.product_dashboard.sort_by = sort_by;
			page.product_dashboard.sort_order = sort_order;
			page.product_dashboard.start = 0;
			page.product_dashboard.refresh();
		}
	});

	// page.sort_selector.wrapper.css({'margin-right': '15px', 'margin-top': '4px'});

	frappe.require('product-dashboard.bundle.js', function() {
		page.product_dashboard = new erpnext.stock.ProductDashboard({
			parent: page.main,
			page_length: 20,
			method: 'erpnext.stock.dashboard.product_dashboard.get_data',
			template: 'product_dashboard_list'
		})

		page.product_dashboard.before_refresh = function() {
			this.product_code = page.product_field.get_value();
			this.warehouse = page.warehouse_field.get_value();
			this.product_group = page.product_group_field.get_value();
		}

		page.product_dashboard.refresh();

		// product click
		var setup_click = function(doctype) {
			page.main.on('click', 'a[data-type="'+ doctype.toLowerCase() +'"]', function() {
				var name = $(this).attr('data-name');
				var field = page[doctype.toLowerCase() + '_field'];
				if(field.get_value()===name) {
					frappe.set_route('Form', doctype, name)
				} else {
					field.set_input(name);
					page.product_dashboard.refresh();
				}
			});
		}

		setup_click('Product');
		setup_click('Warehouse');
	});


}
