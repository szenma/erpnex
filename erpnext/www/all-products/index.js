$(() => {
	class ProductListing {
		constructor() {
			let me = this;
			let is_product_group_page = $(".product-group-content").data("product-group");
			this.product_group = is_product_group_page || null;

			let view_type = localStorage.getProduct("product_view") || "List View";

			// Render Product Views, Filters & Search
			new erpnext.ProductView({
				view_type: view_type,
				products_section: $('#product-listing'),
				product_group: me.product_group
			});

			this.bind_card_actions();
		}

		bind_card_actions() {
			erpnext.e_commerce.shopping_cart.bind_add_to_cart_action();
			erpnext.e_commerce.wishlist.bind_wishlist_action();
		}
	}

	new ProductListing();
});
