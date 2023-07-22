import onScan from 'onscan.js';

erpnext.PointOfSale.ProductSelector = class {
	// eslint-disable-next-line no-unused-vars
	constructor({ frm, wrapper, events, pos_profile, settings }) {
		this.wrapper = wrapper;
		this.events = events;
		this.pos_profile = pos_profile;
		this.hide_images = settings.hide_images;
		this.auto_add_product = settings.auto_add_product_to_cart;

		this.inti_component();
	}

	inti_component() {
		this.prepare_dom();
		this.make_search_bar();
		this.load_products_data();
		this.bind_events();
		this.attach_shortcuts();
	}

	prepare_dom() {
		this.wrapper.append(
			`<section class="products-selector">
				<div class="filter-section">
					<div class="label">${__('All Products')}</div>
					<div class="search-field"></div>
					<div class="product-group-field"></div>
				</div>
				<div class="products-container"></div>
			</section>`
		);

		this.$component = this.wrapper.find('.products-selector');
		this.$products_container = this.$component.find('.products-container');
	}

	async load_products_data() {
		if (!this.product_group) {
			const res = await frappe.db.get_value("Product Group", {lft: 1, is_group: 1}, "name");
			this.parent_product_group = res.message.name;
		}
		if (!this.price_list) {
			const res = await frappe.db.get_value("POS Profile", this.pos_profile, "selling_price_list");
			this.price_list = res.message.selling_price_list;
		}

		this.get_products({}).then(({message}) => {
			this.render_product_list(message.products);
		});
	}

	get_products({start = 0, page_length = 40, search_term=''}) {
		const doc = this.events.get_frm().doc;
		const price_list = (doc && doc.selling_price_list) || this.price_list;
		let { product_group, pos_profile } = this;

		!product_group && (product_group = this.parent_product_group);

		return frappe.call({
			method: "erpnext.selling.page.point_of_sale.point_of_sale.get_products",
			freeze: true,
			args: { start, page_length, price_list, product_group, search_term, pos_profile },
		});
	}


	render_product_list(products) {
		this.$products_container.html('');

		products.forEach(product => {
			const product_html = this.get_product_html(product);
			this.$products_container.append(product_html);
		});
	}

	get_product_html(product) {
		const me = this;
		// eslint-disable-next-line no-unused-vars
		const { product_image, serial_no, batch_no, barcode, actual_qty, stock_uom, price_list_rate } = product;
		const precision = flt(price_list_rate, 2) % 1 != 0 ? 2 : 0;
		let indicator_color;
		let qty_to_display = actual_qty;

		if (product.is_stock_product) {
			indicator_color = (actual_qty > 10 ? "green" : actual_qty <= 0 ? "red" : "orange");

			if (Math.round(qty_to_display) > 999) {
				qty_to_display = Math.round(qty_to_display)/1000;
				qty_to_display = qty_to_display.toFixed(1) + 'K';
			}
		} else {
			indicator_color = '';
			qty_to_display = '';
		}

		function get_product_image_html() {
			if (!me.hide_images && product_image) {
				return `<div class="product-qty-pill">
							<span class="indicator-pill whitespace-nowrap ${indicator_color}">${qty_to_display}</span>
						</div>
						<div class="flex products-center justify-center h-32 border-b-grey text-6xl text-grey-100">
							<img
								onerror="cur_pos.product_selector.handle_broken_image(this)"
								class="h-full product-img" src="${product_image}"
								alt="${frappe.get_abbr(product.product_name)}"
							>
						</div>`;
			} else {
				return `<div class="product-qty-pill">
							<span class="indicator-pill whitespace-nowrap ${indicator_color}">${qty_to_display}</span>
						</div>
						<div class="product-display abbr">${frappe.get_abbr(product.product_name)}</div>`;
			}
		}

		return (
			`<div class="product-wrapper"
				data-product-code="${escape(product.product_code)}" data-serial-no="${escape(serial_no)}"
				data-batch-no="${escape(batch_no)}" data-uom="${escape(stock_uom)}"
				data-rate="${escape(price_list_rate || 0)}"
				title="${product.product_name}">

				${get_product_image_html()}

				<div class="product-detail">
					<div class="product-name">
						${frappe.ellipsis(product.product_name, 18)}
					</div>
					<div class="product-rate">${format_currency(price_list_rate, product.currency, precision) || 0}</div>
				</div>
			</div>`
		);
	}

	handle_broken_image($img) {
		const product_abbr = $($img).attr('alt');
		$($img).parent().replaceWith(`<div class="product-display abbr">${product_abbr}</div>`);
	}

	make_search_bar() {
		const me = this;
		const doc = me.events.get_frm().doc;
		this.$component.find('.search-field').html('');
		this.$component.find('.product-group-field').html('');

		this.search_field = frappe.ui.form.make_control({
			df: {
				label: __('Search'),
				fieldtype: 'Data',
				placeholder: __('Search by product code, serial number or barcode')
			},
			parent: this.$component.find('.search-field'),
			render_input: true,
		});
		this.product_group_field = frappe.ui.form.make_control({
			df: {
				label: __('Product Group'),
				fieldtype: 'Link',
				options: 'Product Group',
				placeholder: __('Select product group'),
				onchange: function() {
					me.product_group = this.value;
					!me.product_group && (me.product_group = me.parent_product_group);
					me.filter_products();
				},
				get_query: function () {
					return {
						query: 'erpnext.selling.page.point_of_sale.point_of_sale.product_group_query',
						filters: {
							pos_profile: doc ? doc.pos_profile : ''
						}
					};
				},
			},
			parent: this.$component.find('.product-group-field'),
			render_input: true,
		});
		this.search_field.toggle_label(false);
		this.product_group_field.toggle_label(false);

		this.attach_clear_btn();
	}

	attach_clear_btn() {
		this.search_field.$wrapper.find('.control-input').append(
			`<span class="link-btn" style="top: 2px;">
				<a class="btn-open no-decoration" title="${__("Clear")}">
					${frappe.utils.icon('close', 'sm')}
				</a>
			</span>`
		);

		this.$clear_search_btn = this.search_field.$wrapper.find('.link-btn');

		this.$clear_search_btn.on('click', 'a', () => {
			this.set_search_value('');
			this.search_field.set_focus();
		});
	}

	set_search_value(value) {
		$(this.search_field.$input[0]).val(value).trigger("input");
	}

	bind_events() {
		const me = this;
		window.onScan = onScan;

		onScan.decodeKeyEvent = function (oEvent) {
			var iCode = this._getNormalizedKeyNum(oEvent);
			switch (true) {
				case iCode >= 48 && iCode <= 90: // numbers and letters
				case iCode >= 106 && iCode <= 111: // operations on numeric keypad (+, -, etc.)
				case (iCode >= 160 && iCode <= 164) || iCode == 170: // ^ ! # $ *
				case iCode >= 186 && iCode <= 194: // (; = , - . / `)
				case iCode >= 219 && iCode <= 222: // ([ \ ] ')
				case iCode == 32: // spacebar
					if (oEvent.key !== undefined && oEvent.key !== '') {
						return oEvent.key;
					}

					var sDecoded = String.fromCharCode(iCode);
					switch (oEvent.shiftKey) {
						case false: sDecoded = sDecoded.toLowerCase(); break;
						case true: sDecoded = sDecoded.toUpperCase(); break;
					}
					return sDecoded;
				case iCode >= 96 && iCode <= 105: // numbers on numeric keypad
					return 0 + (iCode - 96);
			}
			return '';
		};

		onScan.attachTo(document, {
			onScan: (sScancode) => {
				if (this.search_field && this.$component.is(':visible')) {
					this.search_field.set_focus();
					this.set_search_value(sScancode);
					this.barcode_scanned = true;
				}
			}
		});

		this.$component.on('click', '.product-wrapper', function() {
			const $product = $(this);
			const product_code = unescape($product.attr('data-product-code'));
			let batch_no = unescape($product.attr('data-batch-no'));
			let serial_no = unescape($product.attr('data-serial-no'));
			let uom = unescape($product.attr('data-uom'));
			let rate = unescape($product.attr('data-rate'));

			// escape(undefined) returns "undefined" then unescape returns "undefined"
			batch_no = batch_no === "undefined" ? undefined : batch_no;
			serial_no = serial_no === "undefined" ? undefined : serial_no;
			uom = uom === "undefined" ? undefined : uom;
			rate = rate === "undefined" ? undefined : rate;

			me.events.product_selected({
				field: 'qty',
				value: "+1",
				product: { product_code, batch_no, serial_no, uom, rate }
			});
			me.search_field.set_focus();
		});

		this.search_field.$input.on('input', (e) => {
			clearTimeout(this.last_search);
			this.last_search = setTimeout(() => {
				const search_term = e.target.value;
				this.filter_products({ search_term });
			}, 300);

			this.$clear_search_btn.toggle(
				Boolean(this.search_field.$input.val())
			);
		});

		this.search_field.$input.on('focus', () => {
			this.$clear_search_btn.toggle(
				Boolean(this.search_field.$input.val())
			);
		});
	}

	attach_shortcuts() {
		const ctrl_label = frappe.utils.is_mac() ? '⌘' : 'Ctrl';
		this.search_field.parent.attr("title", `${ctrl_label}+I`);
		frappe.ui.keys.add_shortcut({
			shortcut: "ctrl+i",
			action: () => this.search_field.set_focus(),
			condition: () => this.$component.is(':visible'),
			description: __("Focus on search input"),
			ignore_inputs: true,
			page: cur_page.page.page
		});
		this.product_group_field.parent.attr("title", `${ctrl_label}+G`);
		frappe.ui.keys.add_shortcut({
			shortcut: "ctrl+g",
			action: () => this.product_group_field.set_focus(),
			condition: () => this.$component.is(':visible'),
			description: __("Focus on Product Group filter"),
			ignore_inputs: true,
			page: cur_page.page.page
		});

		// for selecting the last filtered product on search
		frappe.ui.keys.on("enter", () => {
			const selector_is_visible = this.$component.is(':visible');
			if (!selector_is_visible || this.search_field.get_value() === "") return;

			if (this.products.length == 1) {
				this.$products_container.find(".product-wrapper").click();
				frappe.utils.play_sound("submit");
				this.set_search_value('');
			} else if (this.products.length == 0 && this.barcode_scanned) {
				// only show alert of barcode is scanned and enter is pressed
				frappe.show_alert({
					message: __("No products found. Scan barcode again."),
					indicator: 'orange'
				});
				frappe.utils.play_sound("error");
				this.barcode_scanned = false;
				this.set_search_value('');
			}
		});
	}

	filter_products({ search_term='' }={}) {
		if (search_term) {
			search_term = search_term.toLowerCase();

			// memoize
			this.search_index = this.search_index || {};
			if (this.search_index[search_term]) {
				const products = this.search_index[search_term];
				this.products = products;
				this.render_product_list(products);
				this.auto_add_product && this.products.length == 1 && this.add_filtered_product_to_cart();
				return;
			}
		}

		this.get_products({ search_term })
			.then(({ message }) => {
				// eslint-disable-next-line no-unused-vars
				const { products, serial_no, batch_no, barcode } = message;
				if (search_term && !barcode) {
					this.search_index[search_term] = products;
				}
				this.products = products;
				this.render_product_list(products);
				this.auto_add_product && this.products.length == 1 && this.add_filtered_product_to_cart();
			});
	}

	add_filtered_product_to_cart() {
		this.$products_container.find(".product-wrapper").click();
		this.set_search_value('');
	}

	resize_selector(minimize) {
		minimize ?
			this.$component.find('.filter-section').css('grid-template-columns', 'repeat(1, minmax(0, 1fr))') :
			this.$component.find('.filter-section').css('grid-template-columns', 'repeat(12, minmax(0, 1fr))');

		minimize ?
			this.$component.find('.search-field').css('margin', 'var(--margin-sm) 0px') :
			this.$component.find('.search-field').css('margin', '0px var(--margin-sm)');

		minimize ?
			this.$component.css('grid-column', 'span 2 / span 2') :
			this.$component.css('grid-column', 'span 6 / span 6');

		minimize ?
			this.$products_container.css('grid-template-columns', 'repeat(1, minmax(0, 1fr))') :
			this.$products_container.css('grid-template-columns', 'repeat(4, minmax(0, 1fr))');
	}

	toggle_component(show) {
		this.set_search_value('');
		this.$component.css('display', show ? 'flex': 'none');
	}
};
