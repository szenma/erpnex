# -*- coding: utf-8 -*-
# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Wishlist(Document):
	pass


@frappe.whitelist()
def add_to_wishlist(product_code):
	"""Insert Product into wishlist."""

	if frappe.db.exists("Wishlist Product", {"product_code": product_code, "parent": frappe.session.user}):
		return

	web_product_data = frappe.db.get_value(
		"Website Product",
		{"product_code": product_code},
		[
			"website_image",
			"website_warehouse",
			"name",
			"web_product_name",
			"product_name",
			"product_group",
			"route",
		],
		as_dict=1,
	)

	wished_product_dict = {
		"product_code": product_code,
		"product_name": web_product_data.get("product_name"),
		"product_group": web_product_data.get("product_group"),
		"website_product": web_product_data.get("name"),
		"web_product_name": web_product_data.get("web_product_name"),
		"image": web_product_data.get("website_image"),
		"warehouse": web_product_data.get("website_warehouse"),
		"route": web_product_data.get("route"),
	}

	if not frappe.db.exists("Wishlist", frappe.session.user):
		# initialise wishlist
		wishlist = frappe.get_doc({"doctype": "Wishlist"})
		wishlist.user = frappe.session.user
		wishlist.append("products", wished_product_dict)
		wishlist.save(ignore_permissions=True)
	else:
		wishlist = frappe.get_doc("Wishlist", frappe.session.user)
		product = wishlist.append("products", wished_product_dict)
		product.db_insert()

	if hasattr(frappe.local, "cookie_manager"):
		frappe.local.cookie_manager.set_cookie("wish_count", str(len(wishlist.products)))


@frappe.whitelist()
def remove_from_wishlist(product_code):
	if frappe.db.exists("Wishlist Product", {"product_code": product_code, "parent": frappe.session.user}):
		frappe.db.delete("Wishlist Product", {"product_code": product_code, "parent": frappe.session.user})
		frappe.db.commit()  # nosemgrep

		wishlist_products = frappe.db.get_values("Wishlist Product", filters={"parent": frappe.session.user})

		if hasattr(frappe.local, "cookie_manager"):
			frappe.local.cookie_manager.set_cookie("wish_count", str(len(wishlist_products)))
