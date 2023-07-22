class ProductConfigure {
	constructor(product_code, product_name) {
		this.product_code = product_code;
		this.product_name = product_name;

		this.get_attributes_and_values()
			.then(attribute_data => {
				this.attribute_data = attribute_data;
				this.show_configure_dialog();
			});
	}

	show_configure_dialog() {
		const fields = this.attribute_data.map(a => {
			return {
				fieldtype: 'Select',
				label: a.attribute,
				fieldname: a.attribute,
				options: a.values.map(v => {
					return {
						label: v,
						value: v
					};
				}),
				change: (e) => {
					this.on_attribute_selection(e);
				}
			};
		});

		this.dialog = new frappe.ui.Dialog({
			title: __('Select Variant for {0}', [this.product_name]),
			fields,
			on_hide: () => {
				set_continue_configuration();
			}
		});

		this.attribute_data.forEach(a => {
			const field = this.dialog.get_field(a.attribute);
			const $a = $(`<a href>${__("Clear")}</a>`);
			$a.on('click', (e) => {
				e.preventDefault();
				this.dialog.set_value(a.attribute, '');
			});
			field.$wrapper.find('.help-box').append($a);
		});

		this.append_status_area();
		this.dialog.show();

		this.dialog.set_values(JSON.parse(localStorage.getProduct(this.get_cache_key())));

		$('.btn-configure').prop('disabled', false);
	}

	on_attribute_selection(e) {
		if (e) {
			const changed_fieldname = $(e.target).data('fieldname');
			this.show_range_input_if_applicable(changed_fieldname);
		} else {
			this.show_range_input_for_all_fields();
		}

		const values = this.dialog.get_values();
		if (Object.keys(values).length === 0) {
			this.clear_status();
			localStorage.removeProduct(this.get_cache_key());
			return;
		}

		// save state
		localStorage.setProduct(this.get_cache_key(), JSON.stringify(values));

		// show
		this.set_loading_status();

		this.get_next_attribute_and_values(values)
			.then(data => {
				const {
					valid_options_for_attributes,
				} = data;

				this.set_product_found_status(data);

				for (let attribute in valid_options_for_attributes) {
					const valid_options = valid_options_for_attributes[attribute];
					const options = this.dialog.get_field(attribute).df.options;
					const new_options = options.map(o => {
						o.disabled = !valid_options.includes(o.value);
						return o;
					});

					this.dialog.set_df_property(attribute, 'options', new_options);
					this.dialog.get_field(attribute).set_options();
				}
			});
	}

	show_range_input_for_all_fields() {
		this.dialog.fields.forEach(f => {
			this.show_range_input_if_applicable(f.fieldname);
		});
	}

	show_range_input_if_applicable(fieldname) {
		const changed_field = this.dialog.get_field(fieldname);
		const changed_value = changed_field.get_value();
		if (changed_value && changed_value.includes(' to ')) {
			// possible range input
			let numbers = changed_value.split(' to ');
			numbers = numbers.map(number => parseFloat(number));

			if (!numbers.some(n => isNaN(n))) {
				numbers.sort((a, b) => a - b);
				if (changed_field.$input_wrapper.find('.range-selector').length) {
					return;
				}
				const parent = $('<div class="range-selector">')
					.insertBefore(changed_field.$input_wrapper.find('.help-box'));
				const control = frappe.ui.form.make_control({
					df: {
						fieldtype: 'Int',
						label: __('Enter value betweeen {0} and {1}', [numbers[0], numbers[1]]),
						change: () => {
							const value = control.get_value();
							if (value < numbers[0] || value > numbers[1]) {
								control.$wrapper.addClass('was-validated');
								control.set_description(
									__('Value must be between {0} and {1}', [numbers[0], numbers[1]]));
								control.$input[0].setCustomValidity('error');
							} else {
								control.$wrapper.removeClass('was-validated');
								control.set_description('');
								control.$input[0].setCustomValidity('');
								this.update_range_values(fieldname, value);
							}
						}
					},
					render_input: true,
					parent
				});
				control.$wrapper.addClass('mt-3');
			}
		}
	}

	update_range_values(attribute, range_value) {
		this.range_values = this.range_values || {};
		this.range_values[attribute] = range_value;
	}

	show_remaining_optional_attributes() {
		// show all attributes if remaining
		// unselected attributes are all optional
		const unselected_attributes = this.dialog.fields.filter(df => {
			const value_selected = this.dialog.get_value(df.fieldname);
			return !value_selected;
		});
		const is_optional_attribute = df => {
			const optional_attributes = this.attribute_data
				.filter(a => a.optional).map(a => a.attribute);
			return optional_attributes.includes(df.fieldname);
		};
		if (unselected_attributes.every(is_optional_attribute)) {
			unselected_attributes.forEach(df => {
				this.dialog.fields_dict[df.fieldname].$wrapper.show();
			});
		}
	}

	set_loading_status() {
		this.dialog.$status_area.html(`
			<div class="alert alert-warning d-flex justify-content-between align-products-center" role="alert">
				${__('Loading...')}
			</div>
		`);
	}

	set_product_found_status(data) {
		const html = this.get_html_for_product_found(data);
		this.dialog.$status_area.html(html);
	}

	clear_status() {
		this.dialog.$status_area.empty();
	}

	get_html_for_product_found({ filtered_products_count, filtered_products, exact_match, product_info, available_qty, settings }) {
		const one_product = exact_match.length === 1
			? exact_match[0]
			: filtered_products_count === 1
				? filtered_products[0]
				: '';

		let product_add_to_cart = one_product ? `
			<button data-product-code="${one_product}"
				class="btn btn-primary btn-add-to-cart w-100"
				data-action="btn_add_to_cart"
			>
				<span class="mr-2">
					${frappe.utils.icon('assets', 'md')}
				</span>
				${__("Add to Cart")}
			</button>
		` : '';

		const products_found = filtered_products_count === 1 ?
			__('{0} product found.', [filtered_products_count]) :
			__('{0} products found.', [filtered_products_count]);

		/* eslint-disable indent */
		const product_found_status = exact_match.length === 1
			? `<div class="alert alert-success d-flex justify-content-between align-products-center" role="alert">
				<div><div>
					${one_product}
					${product_info && product_info.price && !$.isEmptyObject(product_info.price)
						? '(' + product_info.price.formatted_price_sales_uom + ')'
						: ''
					}

					${available_qty === 0 && product_info && product_info?.is_stock_product
						? '<span class="text-danger">(' + __('Out of Stock') + ')</span>' : ''}

				</div></div>
				<a href data-action="btn_clear_values" data-product-code="${one_product}">
					${__('Clear Values')}
				</a>
			</div>`
			: `<div class="alert alert-warning d-flex justify-content-between align-products-center" role="alert">
					<span>
						${products_found}
					</span>
					<a href data-action="btn_clear_values">
						${__('Clear values')}
					</a>
			</div>`;
		/* eslint-disable indent */

		if (!product_info?.allow_products_not_in_stock && available_qty === 0
			&& product_info && product_info?.is_stock_product) {
			product_add_to_cart = '';
		}

		return `
			${product_found_status}
			${product_add_to_cart}
		`;
	}

	btn_add_to_cart(e) {
		if (frappe.session.user !== 'Guest') {
			localStorage.removeProduct(this.get_cache_key());
		}
		const product_code = $(e.currentTarget).data('product-code');
		const additional_notes = Object.keys(this.range_values || {}).map(attribute => {
			return `${attribute}: ${this.range_values[attribute]}`;
		}).join('\n');
		erpnext.e_commerce.shopping_cart.update_cart({
			product_code,
			additional_notes,
			qty: 1
		});
		this.dialog.hide();
	}

	btn_clear_values() {
		this.dialog.fields_list.forEach(f => {
			if (f.df?.options) {
				f.df.options = f.df.options.map(option => {
					option.disabled = false;
					return option;
				});
			}
		});
		this.dialog.clear();
		this.dialog.$status_area.empty();
		this.on_attribute_selection();
	}

	append_status_area() {
		this.dialog.$status_area = $('<div class="status-area mt-5">');
		this.dialog.$wrapper.find('.modal-body').append(this.dialog.$status_area);
		this.dialog.$wrapper.on('click', '[data-action]', (e) => {
			e.preventDefault();
			const $target = $(e.currentTarget);
			const action = $target.data('action');
			const method = this[action];
			method.call(this, e);
		});
		this.dialog.$wrapper.addClass('product-configurator-dialog');
	}

	get_next_attribute_and_values(selected_attributes) {
		return this.call('erpnext.e_commerce.variant_selector.utils.get_next_attribute_and_values', {
			product_code: this.product_code,
			selected_attributes
		});
	}

	get_attributes_and_values() {
		return this.call('erpnext.e_commerce.variant_selector.utils.get_attributes_and_values', {
			product_code: this.product_code
		});
	}

	get_cache_key() {
		return `configure:${this.product_code}`;
	}

	call(method, args) {
		// promisified frappe.call
		return new Promise((resolve, reject) => {
			frappe.call(method, args)
				.then(r => resolve(r.message))
				.fail(reject);
		});
	}
}

function set_continue_configuration() {
	const $btn_configure = $('.btn-configure');
	const { productCode } = $btn_configure.data();

	if (localStorage.getProduct(`configure:${productCode}`)) {
		$btn_configure.text(__('Continue Selection'));
	} else {
		$btn_configure.text(__('Select Variant'));
	}
}

frappe.ready(() => {
	const $btn_configure = $('.btn-configure');
	if (!$btn_configure.length) return;
	const { productCode, productName } = $btn_configure.data();

	set_continue_configuration();

	$btn_configure.on('click', () => {
		$btn_configure.prop('disabled', true);
		new ProductConfigure(productCode, productName);
	});
});
