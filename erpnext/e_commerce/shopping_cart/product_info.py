# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe

from erpnext.e_commerce.doctype.e_commerce_settings.e_commerce_settings import (
	get_shopping_cart_settings,
	show_quantity_in_website,
)
from erpnext.e_commerce.shopping_cart.cart import _get_cart_quotation, _set_price_list
from erpnext.utilities.product import (
	get_non_stock_product_status,
	get_price,
	get_web_product_qty_in_stock,
)


@frappe.whitelist(allow_guest=True)
def get_product_info_for_website(product_code, skip_quotation_creation=False):
	"""get product price / stock info for website"""

	cart_settings = get_shopping_cart_settings()
	if not cart_settings.enabled:
		# return settings even if cart is disabled
		return frappe._dict({"product_info": {}, "cart_settings": cart_settings})

	cart_quotation = frappe._dict()
	if not skip_quotation_creation:
		cart_quotation = _get_cart_quotation()

	selling_price_list = (
		cart_quotation.get("selling_price_list")
		if cart_quotation
		else _set_price_list(cart_settings, None)
	)

	price = {}
	if cart_settings.show_price:
		is_guest = frappe.session.user == "Guest"
		# Show Price if logged in.
		# If not logged in, check if price is hidden for guest.
		if not is_guest or not cart_settings.hide_price_for_guest:
			price = get_price(
				product_code, selling_price_list, cart_settings.default_customer_group, cart_settings.company
			)

	stock_status = None

	if cart_settings.show_stock_availability:
		on_backorder = frappe.get_cached_value("Website Product", {"product_code": product_code}, "on_backorder")
		if on_backorder:
			stock_status = frappe._dict({"on_backorder": True})
		else:
			stock_status = get_web_product_qty_in_stock(product_code, "website_warehouse")

	product_info = {
		"price": price,
		"qty": 0,
		"uom": frappe.db.get_value("Product", product_code, "stock_uom"),
		"sales_uom": frappe.db.get_value("Product", product_code, "sales_uom"),
	}

	if stock_status:
		if stock_status.on_backorder:
			product_info["on_backorder"] = True
		else:
			product_info["stock_qty"] = stock_status.stock_qty
			product_info["in_stock"] = (
				stock_status.in_stock
				if stock_status.is_stock_product
				else get_non_stock_product_status(product_code, "website_warehouse")
			)
			product_info["show_stock_qty"] = show_quantity_in_website()

	if product_info["price"]:
		if frappe.session.user != "Guest":
			product = cart_quotation.get({"product_code": product_code}) if cart_quotation else None
			if product:
				product_info["qty"] = product[0].qty

	return frappe._dict({"product_info": product_info, "cart_settings": cart_settings})


def set_product_info_for_website(product):
	"""set product price uom for website"""
	product_info = get_product_info_for_website(product.product_code, skip_quotation_creation=True).get(
		"product_info"
	)

	if product_info:
		product.update(product_info)
		product["stock_uom"] = product_info.get("uom")
		product["sales_uom"] = product_info.get("sales_uom")
		if product_info.get("price"):
			product["price_stock_uom"] = product_info.get("price").get("formatted_price")
			product["price_sales_uom"] = product_info.get("price").get("formatted_price_sales_uom")
		else:
			product["price_stock_uom"] = ""
			product["price_sales_uom"] = ""
