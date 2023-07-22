# -*- coding: utf-8 -*-
# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt
import unittest

import frappe
from frappe.core.doctype.user_permission.test_user_permission import create_user

from erpnext.e_commerce.doctype.website_product.website_product import make_website_product
from erpnext.e_commerce.doctype.wishlist.wishlist import add_to_wishlist, remove_from_wishlist
from erpnext.stock.doctype.product.test_product import make_product


class TestWishlist(unittest.TestCase):
	def setUp(self):
		product = make_product("Test Phone Series X")
		if not frappe.db.exists("Website Product", {"product_code": "Test Phone Series X"}):
			make_website_product(product, save=True)

		product = make_product("Test Phone Series Y")
		if not frappe.db.exists("Website Product", {"product_code": "Test Phone Series Y"}):
			make_website_product(product, save=True)

	def tearDown(self):
		frappe.get_cached_doc("Website Product", {"product_code": "Test Phone Series X"}).delete()
		frappe.get_cached_doc("Website Product", {"product_code": "Test Phone Series Y"}).delete()
		frappe.get_cached_doc("Product", "Test Phone Series X").delete()
		frappe.get_cached_doc("Product", "Test Phone Series Y").delete()

	def test_add_remove_products_in_wishlist(self):
		"Check if products are added and removed from user's wishlist."
		# add first product
		add_to_wishlist("Test Phone Series X")

		# check if wishlist was created and product was added
		self.assertTrue(frappe.db.exists("Wishlist", {"user": frappe.session.user}))
		self.assertTrue(
			frappe.db.exists(
				"Wishlist Product", {"product_code": "Test Phone Series X", "parent": frappe.session.user}
			)
		)

		# add second product to wishlist
		add_to_wishlist("Test Phone Series Y")
		wishlist_length = frappe.db.get_value(
			"Wishlist Product", {"parent": frappe.session.user}, "count(*)"
		)
		self.assertEqual(wishlist_length, 2)

		remove_from_wishlist("Test Phone Series X")
		remove_from_wishlist("Test Phone Series Y")

		wishlist_length = frappe.db.get_value(
			"Wishlist Product", {"parent": frappe.session.user}, "count(*)"
		)
		self.assertIsNone(frappe.db.exists("Wishlist Product", {"parent": frappe.session.user}))
		self.assertEqual(wishlist_length, 0)

		# tear down
		frappe.get_doc("Wishlist", {"user": frappe.session.user}).delete()

	def test_add_remove_in_wishlist_multiple_users(self):
		"Check if products are added and removed from the correct user's wishlist."
		test_user = create_user("test_reviewer@example.com", "Customer")
		test_user_1 = create_user("test_reviewer_1@example.com", "Customer")

		# add to wishlist for first user
		frappe.set_user(test_user.name)
		add_to_wishlist("Test Phone Series X")

		# add to wishlist for second user
		frappe.set_user(test_user_1.name)
		add_to_wishlist("Test Phone Series X")

		# check wishlist and its content for users
		self.assertTrue(frappe.db.exists("Wishlist", {"user": test_user.name}))
		self.assertTrue(
			frappe.db.exists(
				"Wishlist Product", {"product_code": "Test Phone Series X", "parent": test_user.name}
			)
		)

		self.assertTrue(frappe.db.exists("Wishlist", {"user": test_user_1.name}))
		self.assertTrue(
			frappe.db.exists(
				"Wishlist Product", {"product_code": "Test Phone Series X", "parent": test_user_1.name}
			)
		)

		# remove product for second user
		remove_from_wishlist("Test Phone Series X")

		# make sure product was removed for second user and not first
		self.assertFalse(
			frappe.db.exists(
				"Wishlist Product", {"product_code": "Test Phone Series X", "parent": test_user_1.name}
			)
		)
		self.assertTrue(
			frappe.db.exists(
				"Wishlist Product", {"product_code": "Test Phone Series X", "parent": test_user.name}
			)
		)

		# remove product for first user
		frappe.set_user(test_user.name)
		remove_from_wishlist("Test Phone Series X")
		self.assertFalse(
			frappe.db.exists(
				"Wishlist Product", {"product_code": "Test Phone Series X", "parent": test_user.name}
			)
		)

		# tear down
		frappe.set_user("Administrator")
		frappe.get_doc("Wishlist", {"user": test_user.name}).delete()
		frappe.get_doc("Wishlist", {"user": test_user_1.name}).delete()
