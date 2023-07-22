erpnext.ProductGrid = class {
	/* Options:
		- products: Products
		- settings: E Commerce Settings
		- products_section: Products Wrapper
		- preference: If preference is not grid view, render but hide
	*/
	constructor(options) {
		Object.assign(this, options);

		if (this.preference !== "Grid View") {
			this.products_section.addClass("hidden");
		}

		this.products_section.empty();
		this.make();
	}

	make() {
		let me = this;
		let html = ``;

		this.products.forEach(product => {
			let title = product.web_product_name || product.product_name || product.product_code || "";
			title =  title.length > 90 ? title.substr(0, 90) + "..." : title;

			html += `<div class="col-sm-4 product-card"><div class="card text-left">`;
			html += me.get_image_html(product, title);
			html += me.get_card_body_html(product, title, me.settings);
			html += `</div></div>`;
		});

		let $product_wrapper = this.products_section;
		$product_wrapper.append(html);
	}

	get_image_html(product, title) {
		let image = product.website_image;

		if (image) {
			return `
				<div class="card-img-container">
					<a href="/${ product.route || '#' }" style="text-decoration: none;">
						<img class="card-img" src="${ image }" alt="${ title }">
					</a>
				</div>
			`;
		} else {
			return `
				<div class="card-img-container">
					<a href="/${ product.route || '#' }" style="text-decoration: none;">
						<div class="card-img-top no-image">
							${ frappe.get_abbr(title) }
						</div>
					</a>
				</div>
			`;
		}
	}

	get_card_body_html(product, title, settings) {
		let body_html = `
			<div class="card-body text-left card-body-flex" style="width:100%">
				<div style="margin-top: 1rem; display: flex;">
		`;
		body_html += this.get_title(product, title);

		// get floating elements
		if (!product.has_variants) {
			if (settings.enable_wishlist) {
				body_html += this.get_wishlist_icon(product);
			}
			if (settings.enabled) {
				body_html += this.get_cart_indicator(product);
			}

		}

		body_html += `</div>`;
		body_html += `<div class="product-category">${ product.product_group || '' }</div>`;

		if (product.formatted_price) {
			body_html += this.get_price_html(product);
		}

		body_html += this.get_stock_availability(product, settings);
		body_html += this.get_primary_button(product, settings);
		body_html += `</div>`; // close div on line 49

		return body_html;
	}

	get_title(product, title) {
		let title_html = `
			<a href="/${ product.route || '#' }">
				<div class="product-title">
					${ title || '' }
				</div>
			</a>
		`;
		return title_html;
	}

	get_wishlist_icon(product) {
		let icon_class = product.wished ? "wished" : "not-wished";
		return `
			<div class="like-action ${ product.wished ? "like-action-wished" : ''}"
				data-product-code="${ product.product_code }">
				<svg class="icon sm">
					<use class="${ icon_class } wish-icon" href="#icon-heart"></use>
				</svg>
			</div>
		`;
	}

	get_cart_indicator(product) {
		return `
			<div class="cart-indicator ${product.in_cart ? '' : 'hidden'}" data-product-code="${ product.product_code }">
				1
			</div>
		`;
	}

	get_price_html(product) {
		let price_html = `
			<div class="product-price">
				${ product.formatted_price || '' }
		`;

		if (product.formatted_mrp) {
			price_html += `
				<small class="striked-price">
					<s>${ product.formatted_mrp ? product.formatted_mrp.replace(/ +/g, "") : "" }</s>
				</small>
				<small class="ml-1 product-info-green">
					${ product.discount } OFF
				</small>
			`;
		}
		price_html += `</div>`;
		return price_html;
	}

	get_stock_availability(product, settings) {
		if (settings.show_stock_availability && !product.has_variants) {
			if (product.on_backorder) {
				return `
					<span class="out-of-stock mb-2 mt-1" style="color: var(--primary-color)">
						${ __("Available on backorder") }
					</span>
				`;
			} else if (!product.in_stock) {
				return `
					<span class="out-of-stock mb-2 mt-1">
						${ __("Out of stock") }
					</span>
				`;
			}
		}

		return ``;
	}

	get_primary_button(product, settings) {
		if (product.has_variants) {
			return `
				<a href="/${ product.route || '#' }">
					<div class="btn btn-sm btn-explore-variants w-100 mt-4">
						${ __('Explore') }
					</div>
				</a>
			`;
		} else if (settings.enabled && (settings.allow_products_not_in_stock || product.in_stock)) {
			return `
				<div id="${ product.name }" class="btn
					btn-sm btn-primary btn-add-to-cart-list
					w-100 mt-2 ${ product.in_cart ? 'hidden' : '' }"
					data-product-code="${ product.product_code }">
					<span class="mr-2">
						<svg class="icon icon-md">
							<use href="#icon-assets"></use>
						</svg>
					</span>
					${ settings.enable_checkout ? __('Add to Cart') :  __('Add to Quote') }
				</div>

				<a href="/cart">
					<div id="${ product.name }" class="btn
						btn-sm btn-primary btn-add-to-cart-list
						w-100 mt-4 go-to-cart-grid
						${ product.in_cart ? '' : 'hidden' }"
						data-product-code="${ product.product_code }">
						${ settings.enable_checkout ? __('Go to Cart') :  __('Go to Quote') }
					</div>
				</a>
			`;
		} else {
			return ``;
		}
	}
};