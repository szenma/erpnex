erpnext.PointOfSale.ProductDetails = class {
	constructor({ wrapper, events, settings }) {
		this.wrapper = wrapper;
		this.events = events;
		this.hide_images = settings.hide_images;
		this.allow_rate_change = settings.allow_rate_change;
		this.allow_discount_change = settings.allow_discount_change;
		this.current_product = {};

		this.init_component();
	}

	init_component() {
		this.prepare_dom();
		this.init_child_components();
		this.bind_events();
		this.attach_shortcuts();
	}

	prepare_dom() {
		this.wrapper.append(
			`<section class="product-details-container"></section>`
		)

		this.$component = this.wrapper.find('.product-details-container');
	}

	init_child_components() {
		this.$component.html(
			`<div class="product-details-header">
				<div class="label">${__('Product Details')}</div>
				<div class="close-btn">
					<svg width="32" height="32" viewBox="0 0 14 14" fill="none">
						<path d="M4.93764 4.93759L7.00003 6.99998M9.06243 9.06238L7.00003 6.99998M7.00003 6.99998L4.93764 9.06238L9.06243 4.93759" stroke="#8D99A6"/>
					</svg>
				</div>
			</div>
			<div class="product-display">
				<div class="product-name-desc-price">
					<div class="product-name"></div>
					<div class="product-desc"></div>
					<div class="product-price"></div>
				</div>
				<div class="product-image"></div>
			</div>
			<div class="discount-section"></div>
			<div class="form-container"></div>`
		)

		this.$product_name = this.$component.find('.product-name');
		this.$product_description = this.$component.find('.product-desc');
		this.$product_price = this.$component.find('.product-price');
		this.$product_image = this.$component.find('.product-image');
		this.$form_container = this.$component.find('.form-container');
		this.$dicount_section = this.$component.find('.discount-section');
	}

	compare_with_current_product(product) {
		// returns true if `product` is currently being edited
		return product && product.name == this.current_product.name;
	}

	async toggle_product_details_section(product) {
		const current_product_changed = !this.compare_with_current_product(product);

		// if product is null or highlighted cart product is clicked twice
		const hide_product_details = !Boolean(product) || !current_product_changed;

		if ((!hide_product_details && current_product_changed) || hide_product_details) {
			// if product details is being closed OR if product details is opened but product is changed
			// in both cases, if the current product is a serialized product, then validate and remove the product
			await this.validate_serial_batch_product();
		}

		this.events.toggle_product_selector(!hide_product_details);
		this.toggle_component(!hide_product_details);

		if (product && current_product_changed) {
			this.doctype = product.doctype;
			this.product_meta = frappe.get_meta(this.doctype);
			this.name = product.name;
			this.product_row = product;
			this.currency = this.events.get_frm().doc.currency;

			this.current_product = product;

			this.render_dom(product);
			this.render_discount_dom(product);
			this.render_form(product);
			this.events.highlight_cart_product(product);
		} else {
			this.current_product = {};
		}
	}

	validate_serial_batch_product() {
		const doc = this.events.get_frm().doc;
		const product_row = doc.products.find(product => product.name === this.name);

		if (!product_row) return;

		const serialized = product_row.has_serial_no;
		const batched = product_row.has_batch_no;
		const no_serial_selected = !product_row.serial_no;
		const no_batch_selected = !product_row.batch_no;

		if ((serialized && no_serial_selected) || (batched && no_batch_selected) ||
			(serialized && batched && (no_batch_selected || no_serial_selected))) {

			frappe.show_alert({
				message: __("Product is removed since no serial / batch no selected."),
				indicator: 'orange'
			});
			frappe.utils.play_sound("cancel");
			return this.events.remove_product_from_cart();
		}
	}

	render_dom(product) {
		let { product_name, description, image, price_list_rate } = product;

		function get_description_html() {
			if (description) {
				description = description.indexOf('...') === -1 && description.length > 140 ? description.substr(0, 139) + '...' : description;
				return description;
			}
			return ``;
		}

		this.$product_name.html(product_name);
		this.$product_description.html(get_description_html());
		this.$product_price.html(format_currency(price_list_rate, this.currency));
		if (!this.hide_images && image) {
			this.$product_image.html(
				`<img
					onerror="cur_pos.product_details.handle_broken_image(this)"
					class="h-full" src="${image}"
					alt="${frappe.get_abbr(product_name)}"
					style="object-fit: cover;">`
			);
		} else {
			this.$product_image.html(`<div class="product-abbr">${frappe.get_abbr(product_name)}</div>`);
		}

	}

	handle_broken_image($img) {
		const product_abbr = $($img).attr('alt');
		$($img).replaceWith(`<div class="product-abbr">${product_abbr}</div>`);
	}

	render_discount_dom(product) {
		if (product.discount_percentage) {
			this.$dicount_section.html(
				`<div class="product-rate">${format_currency(product.price_list_rate, this.currency)}</div>
				<div class="product-discount">${product.discount_percentage}% off</div>`
			)
			this.$product_price.html(format_currency(product.rate, this.currency));
		} else {
			this.$dicount_section.html(``)
		}
	}

	render_form(product) {
		const fields_to_display = this.get_form_fields(product);
		this.$form_container.html('');

		fields_to_display.forEach((fieldname, idx) => {
			this.$form_container.append(
				`<div class="${fieldname}-control" data-fieldname="${fieldname}"></div>`
			)

			const field_meta = this.product_meta.fields.find(df => df.fieldname === fieldname);
			fieldname === 'discount_percentage' ? (field_meta.label = __('Discount (%)')) : '';
			const me = this;

			this[`${fieldname}_control`] = frappe.ui.form.make_control({
				df: {
					...field_meta,
					onchange: function() {
						me.events.form_updated(me.current_product, fieldname, this.value);
					}
				},
				parent: this.$form_container.find(`.${fieldname}-control`),
				render_input: true,
			})
			this[`${fieldname}_control`].set_value(product[fieldname]);
		});

		this.make_auto_serial_selection_btn(product);

		this.bind_custom_control_change_event();
	}

	get_form_fields(product) {
		const fields = ['qty', 'uom', 'rate', 'conversion_factor', 'discount_percentage', 'warehouse', 'actual_qty', 'price_list_rate'];
		if (product.has_serial_no) fields.push('serial_no');
		if (product.has_batch_no) fields.push('batch_no');
		return fields;
	}

	make_auto_serial_selection_btn(product) {
		if (product.has_serial_no) {
			if (!product.has_batch_no) {
				this.$form_container.append(
					`<div class="grid-filler no-select"></div>`
				);
			}
			const label = __('Auto Fetch Serial Numbers');
			this.$form_container.append(
				`<div class="btn btn-sm btn-secondary auto-fetch-btn">${label}</div>`
			);
			this.$form_container.find('.serial_no-control').find('textarea').css('height', '6rem');
		}
	}

	bind_custom_control_change_event() {
		const me = this;
		if (this.rate_control) {
			this.rate_control.df.onchange = function() {
				if (this.value || flt(this.value) === 0) {
					me.events.form_updated(me.current_product, 'rate', this.value).then(() => {
						const product_row = frappe.get_doc(me.doctype, me.name);
						const doc = me.events.get_frm().doc;
						me.$product_price.html(format_currency(product_row.rate, doc.currency));
						me.render_discount_dom(product_row);
					});
				}
			};
			this.rate_control.df.read_only = !this.allow_rate_change;
			this.rate_control.refresh();
		}

		if (this.discount_percentage_control && !this.allow_discount_change) {
			this.discount_percentage_control.df.read_only = 1;
			this.discount_percentage_control.refresh();
		}

		if (this.warehouse_control) {
			this.warehouse_control.df.reqd = 1;
			this.warehouse_control.df.onchange = function() {
				if (this.value) {
					me.events.form_updated(me.current_product, 'warehouse', this.value).then(() => {
						me.product_stock_map = me.events.get_product_stock_map();
						const available_qty = me.product_stock_map[me.product_row.product_code][this.value][0];
						const is_stock_product = Boolean(me.product_stock_map[me.product_row.product_code][this.value][1]);
						if (available_qty === undefined) {
							me.events.get_available_stock(me.product_row.product_code, this.value).then(() => {
								// product stock map is updated now reset warehouse
								me.warehouse_control.set_value(this.value);
							})
						} else if (available_qty === 0 && is_stock_product) {
							me.warehouse_control.set_value('');
							const bold_product_code = me.product_row.product_code.bold();
							const bold_warehouse = this.value.bold();
							frappe.throw(
								__('Product Code: {0} is not available under warehouse {1}.', [bold_product_code, bold_warehouse])
							);
						}
						me.actual_qty_control.set_value(available_qty);
					});
				}
			}
			this.warehouse_control.df.get_query = () => {
				return {
					filters: { company: this.events.get_frm().doc.company }
				}
			};
			this.warehouse_control.refresh();
		}

		if (this.serial_no_control) {
			this.serial_no_control.df.reqd = 1;
			this.serial_no_control.df.onchange = async function() {
				!me.current_product.batch_no && await me.auto_update_batch_no();
				me.events.form_updated(me.current_product, 'serial_no', this.value);
			}
			this.serial_no_control.refresh();
		}

		if (this.batch_no_control) {
			this.batch_no_control.df.reqd = 1;
			this.batch_no_control.df.get_query = () => {
				return {
					query: 'erpnext.controllers.queries.get_batch_no',
					filters: {
						product_code: me.product_row.product_code,
						warehouse: me.product_row.warehouse,
						posting_date: me.events.get_frm().doc.posting_date
					}
				}
			};
			this.batch_no_control.refresh();
		}

		if (this.uom_control) {
			this.uom_control.df.onchange = function() {
				me.events.form_updated(me.current_product, 'uom', this.value);

				const product_row = frappe.get_doc(me.doctype, me.name);
				me.conversion_factor_control.df.read_only = (product_row.stock_uom == this.value);
				me.conversion_factor_control.refresh();
			}
		}

		frappe.model.on("POS Invoice Product", "*", (fieldname, value, product_row) => {
			const field_control = this[`${fieldname}_control`];
			const product_row_is_being_edited = this.compare_with_current_product(product_row);

			if (product_row_is_being_edited && field_control && field_control.get_value() !== value) {
				field_control.set_value(value);
				cur_pos.update_cart_html(product_row);
			}
		});
	}

	async auto_update_batch_no() {
		if (this.serial_no_control && this.batch_no_control) {
			const selected_serial_nos = this.serial_no_control.get_value().split(`\n`).filter(s => s);
			if (!selected_serial_nos.length) return;

			// find batch nos of the selected serial no
			const serials_with_batch_no = await frappe.db.get_list("Serial No", {
				filters: { 'name': ["in", selected_serial_nos]},
				fields: ["batch_no", "name"]
			});
			const batch_serial_map = serials_with_batch_no.reduce((acc, r) => {
				if (!acc[r.batch_no]) {
					acc[r.batch_no] = [];
				}
				acc[r.batch_no] = [...acc[r.batch_no], r.name];
				return acc;
			}, {});
			// set current product's batch no and serial no
			const batch_no = Object.keys(batch_serial_map)[0];
			const batch_serial_nos = batch_serial_map[batch_no].join(`\n`);
			// eg. 10 selected serial no. -> 5 belongs to first batch other 5 belongs to second batch
			const serial_nos_belongs_to_other_batch = selected_serial_nos.length !== batch_serial_map[batch_no].length;

			const current_batch_no = this.batch_no_control.get_value();
			current_batch_no != batch_no && await this.batch_no_control.set_value(batch_no);

			if (serial_nos_belongs_to_other_batch) {
				this.serial_no_control.set_value(batch_serial_nos);
				this.qty_control.set_value(batch_serial_map[batch_no].length);

				delete batch_serial_map[batch_no];
				this.events.clone_new_batch_product_in_frm(batch_serial_map, this.current_product);
			}
		}
	}

	bind_events() {
		this.bind_auto_serial_fetch_event();
		this.bind_fields_to_numpad_fields();

		this.$component.on('click', '.close-btn', () => {
			this.events.close_product_details();
		});
	}

	attach_shortcuts() {
		this.wrapper.find('.close-btn').attr("title", "Esc");
		frappe.ui.keys.on("escape", () => {
			const product_details_visible = this.$component.is(":visible");
			if (product_details_visible) {
				this.events.close_product_details();
			}
		});
	}

	bind_fields_to_numpad_fields() {
		const me = this;
		this.$form_container.on('click', '.input-with-feedback', function() {
			const fieldname = $(this).attr('data-fieldname');
			if (this.last_field_focused != fieldname) {
				me.events.product_field_focused(fieldname);
				this.last_field_focused = fieldname;
			}
		});
	}

	bind_auto_serial_fetch_event() {
		this.$form_container.on('click', '.auto-fetch-btn', () => {
			this.batch_no_control && this.batch_no_control.set_value('');
			let qty = this.qty_control.get_value();
			let conversion_factor = this.conversion_factor_control.get_value();
			let expiry_date = this.product_row.has_batch_no ? this.events.get_frm().doc.posting_date : "";

			let numbers = frappe.call({
				method: "erpnext.stock.doctype.serial_no.serial_no.auto_fetch_serial_number",
				args: {
					qty: qty * conversion_factor,
					product_code: this.current_product.product_code,
					warehouse: this.warehouse_control.get_value() || '',
					batch_nos: this.current_product.batch_no || '',
					posting_date: expiry_date,
					for_doctype: 'POS Invoice'
				}
			});

			numbers.then((data) => {
				let auto_fetched_serial_numbers = data.message;
				let records_length = auto_fetched_serial_numbers.length;
				if (!records_length) {
					const warehouse = this.warehouse_control.get_value().bold();
					const product_code = this.current_product.product_code.bold();
					frappe.msgprint(
						__('Serial numbers unavailable for Product {0} under warehouse {1}. Please try changing warehouse.', [product_code, warehouse])
					);
				} else if (records_length < qty) {
					frappe.msgprint(
						__('Fetched only {0} available serial numbers.', [records_length])
					);
					this.qty_control.set_value(records_length);
				}
				numbers = auto_fetched_serial_numbers.join(`\n`);
				this.serial_no_control.set_value(numbers);
			});
		})
	}

	toggle_component(show) {
		show ? this.$component.css('display', 'flex') : this.$component.css('display', 'none');
	}
}
