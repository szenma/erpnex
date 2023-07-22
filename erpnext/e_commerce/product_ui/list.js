erpnext.ProductList = class {
	/* Options:
		- products: Products
		- settings: E Commerce Settings
		- products_section: Products Wrapper
		- preference: If preference is not list view, render but hide
	*/
	constructor(options) {
		Object.assign(this, options);

		if (this.preference !== "List View") {
			this.products_section.addClass("hidden");
		}

		this.products_section.empty();
		this.make();
	}

	make() {
		let me = this;
		let html = `<br><br>`;

		this.products.forEach(product => {
			let title = product.web_product_name || product.product_name || product.product_code || "";
			title =  title.length > 200 ? title.substr(0, 200) + "..." : title;

			html += `<div class='row list-row w-100 mb-4'>`;
			html += me.get_image_html(product, title, me.settings);
			html += me.get_row_body_html(product, title, me.settings);
			html += `</div>`;
		});

		let $product_wrapper = this.products_section;
		$product_wrapper.append(html);
	}

	get_image_html(product, title, settings) {
		let image = product.website_image;
		let wishlist_enabled = !product.has_variants && settings.enable_wishlist;
		let image_html = ``;

		if (image) {
			image_html += `
				<div class="col-2 border text-center rounded list-image">
					<a class="product-link product-list-link" href="/${ product.route || '#' }">
						<img productprop="image" class="website-image h-100 w-100" alt="${ title }"
							src="${ image }">
					</a>
					${ wishlist_enabled ? this.get_wishlist_icon(product): '' }
				</div>
			`;
		} else {
			image_html += `
				<div class="col-2 border text-center rounded list-image">
					<a class="product-link product-list-link" href="/${ product.route || '#' }"
						style="text-decoration: none">
						<div class="card-img-top no-image-list">
							${ frappe.get_abbr(title) }
						</div>
					</a>
					${ wishlist_enabled ? this.get_wishlist_icon(product): '' }
				</div>
			`;
		}

		return image_html;
	}

	get_row_body_html(product, title, settings) {
		let body_html = `<div class='col-10 text-left'>`;
		body_html += this.get_title_html(product, title, settings);
		body_html += this.get_product_details(product, settings);
		body_html += `</div>`;
		return body_html;
	}

	get_title_html(product, title, settings) {
		let title_html = `<div style="display: flex; margin-left: -15px;">`;
		title_html += `
			<div class="col-8" style="margin-right: -15px;">
				<a href="/${ product.route || '#' }">
					<div class="product-title">
					${ title }
					</div>
				</a>
			</div>
		`;

		if (settings.enabled) {
			title_html += `<div class="col-4 cart-action-container ${product.in_cart ? 'd-flex' : ''}">`;
			title_html += this.get_primary_button(product, settings);
			title_html += `</div>`;
		}
		title_html += `</div>`;

		return title_html;
	}

	get_product_details(product, settings) {
		let details = `
			<p class="product-code">
				${ product.product_group } | Product Code : ${ product.product_code }
			</p>
			<div class="mt-2" style="color: var(--gray-600) !important; font-size: 13px;">
				${ product.short_description || '' }
			</div>
			<div class="product-price">
				${ product.formatted_price || '' }
		`;

		if (product.formatted_mrp) {
			details += `
				<small class="striked-price">
					<s>${ product.formatted_mrp ? product.formatted_mrp.replace(/ +/g, "") : "" }</s>
				</small>
				<small class="ml-1 product-info-green">
					${ product.discount } OFF
				</small>
			`;
		}

		details += this.get_stock_availability(product, settings);
		details += `</div>`;

		return details;
	}

	get_stock_availability(product, settings) {
		if (settings.show_stock_availability && !product.has_variants) {
			if (product.on_backorder) {
				return `
					<br>
					<span class="out-of-stock mt-2" style="color: var(--primary-color)">
						${ __("Available on backorder") }
					</span>
				`;
			} else if (!product.in_stock) {
				return `
					<br>
					<span class="out-of-stock mt-2">${ __("Out of stock") }</span>
				`;
			}
		}
		return ``;
	}

	get_wishlist_icon(product) {
		let icon_class = product.wished ? "wished" : "not-wished";

		return `
			<div class="like-action-list ${ product.wished ? "like-action-wished" : ''}"
				data-product-code="${ product.product_code }">
				<svg class="icon sm">
					<use class="${ icon_class } wish-icon" href="#icon-heart"></use>
				</svg>
			</div>
		`;
	}

	get_primary_button(product, settings) {
		if (product.has_variants) {
			return `
				<a href="/${ product.route || '#' }">
					<div class="btn btn-sm btn-explore-variants btn mb-0 mt-0">
						${ __('Explore') }
					</div>
				</a>
			`;
		} else if (settings.enabled && (settings.allow_products_not_in_stock || product.in_stock)) {
			return `
				<div id="${ product.name }" class="btn
					btn-sm btn-primary btn-add-to-cart-list mb-0
					${ product.in_cart ? 'hidden' : '' }"
					data-product-code="${ product.product_code }"
					style="margin-top: 0px !important; max-height: 30px; float: right;
						padding: 0.25rem 1rem; min-width: 135px;">
					<span class="mr-2">
						<svg class="icon icon-md">
							<use href="#icon-assets"></use>
						</svg>
					</span>
					${ settings.enable_checkout ? __('Add to Cart') :  __('Add to Quote') }
				</div>

				<div class="cart-indicator list-indicator ${product.in_cart ? '' : 'hidden'}">
					1
				</div>

				<a href="/cart">
					<div id="${ product.name }" class="btn
						btn-sm btn-primary btn-add-to-cart-list
						ml-4 go-to-cart mb-0 mt-0
						${ product.in_cart ? '' : 'hidden' }"
						data-product-code="${ product.product_code }"
						style="padding: 0.25rem 1rem; min-width: 135px;">
						${ settings.enable_checkout ? __('Go to Cart') :  __('Go to Quote') }
					</div>
				</a>
			`;
		} else {
			return ``;
		}
	}

};
