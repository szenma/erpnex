# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt
import frappe

from erpnext.e_commerce.doctype.e_commerce_settings.e_commerce_settings import (
	get_shopping_cart_settings,
)
from erpnext.e_commerce.shopping_cart.cart import _set_price_list
from erpnext.utilities.product import get_price


def get_context(context):
	is_guest = frappe.session.user == "Guest"

	settings = get_shopping_cart_settings()
	products = get_wishlist_products() if not is_guest else []
	selling_price_list = _set_price_list(settings) if not is_guest else None

	products = set_stock_price_details(products, settings, selling_price_list)

	context.body_class = "product-page"
	context.products = products
	context.settings = settings
	context.no_cache = 1


def get_stock_availability(product_code, warehouse):
	stock_qty = frappe.utils.flt(
		frappe.db.get_value("Bin", {"product_code": product_code, "warehouse": warehouse}, "actual_qty")
	)
	return bool(stock_qty)


def get_wishlist_products():
	if not frappe.db.exists("Wishlist", frappe.session.user):
		return []

	return frappe.db.get_all(
		"Wishlist Product",
		filters={"parent": frappe.session.user},
		fields=[
			"web_product_name",
			"product_code",
			"product_name",
			"website_product",
			"warehouse",
			"image",
			"product_group",
			"route",
		],
	)


def set_stock_price_details(products, settings, selling_price_list):
	for product in products:
		if settings.show_stock_availability:
			product.available = get_stock_availability(product.product_code, product.get("warehouse"))

		price_details = get_price(
			product.product_code, selling_price_list, settings.default_customer_group, settings.company
		)

		if price_details:
			product.formatted_price = price_details.get("formatted_price")
			product.formatted_mrp = price_details.get("formatted_mrp")
			if product.formatted_mrp:
				product.discount = price_details.get("formatted_discount_percent") or price_details.get(
					"formatted_discount_rate"
				)

	return products
