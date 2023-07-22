# -*- coding: utf-8 -*-
# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt
import unittest

import frappe
from frappe.core.doctype.user_permission.test_user_permission import create_user

from erpnext.e_commerce.doctype.e_commerce_settings.test_e_commerce_settings import (
	setup_e_commerce_settings,
)
from erpnext.e_commerce.doctype.product_review.product_review import (
	UnverifiedReviewer,
	add_product_review,
	get_product_reviews,
)
from erpnext.e_commerce.doctype.website_product.website_product import make_website_product
from erpnext.e_commerce.shopping_cart.cart import get_party
from erpnext.stock.doctype.product.test_product import make_product


class TestProductReview(unittest.TestCase):
	def setUp(self):
		product = make_product("Test Mobile Phone")
		if not frappe.db.exists("Website Product", {"product_code": "Test Mobile Phone"}):
			make_website_product(product, save=True)

		setup_e_commerce_settings({"enable_reviews": 1})
		frappe.local.shopping_cart_settings = None

	def tearDown(self):
		frappe.get_cached_doc("Website Product", {"product_code": "Test Mobile Phone"}).delete()
		setup_e_commerce_settings({"enable_reviews": 0})

	def test_add_and_get_product_reviews_from_customer(self):
		"Add / Get Reviews from a User that is a valid customer (has added to cart or purchased in the past)"
		# create user
		web_product = frappe.db.get_value("Website Product", {"product_code": "Test Mobile Phone"})
		test_user = create_user("test_reviewer@example.com", "Customer")
		frappe.set_user(test_user.name)

		# create customer and contact against user
		customer = get_party()

		# post review on "Test Mobile Phone"
		try:
			add_product_review(web_product, "Great Product", 3, "Would recommend this product")
			review_name = frappe.db.get_value("Product Review", {"website_product": web_product})
		except Exception:
			self.fail(f"Error while publishing review for {web_product}")

		review_data = get_product_reviews(web_product, 0, 10)

		self.assertEqual(len(review_data.reviews), 1)
		self.assertEqual(review_data.average_rating, 3)
		self.assertEqual(review_data.reviews_per_rating[2], 100)

		# tear down
		frappe.set_user("Administrator")
		frappe.delete_doc("Product Review", review_name)
		customer.delete()

	def test_add_product_review_from_non_customer(self):
		"Check if logged in user (who is not a customer yet) is blocked from posting reviews."
		web_product = frappe.db.get_value("Website Product", {"product_code": "Test Mobile Phone"})
		test_user = create_user("test_reviewer@example.com", "Customer")
		frappe.set_user(test_user.name)

		with self.assertRaises(UnverifiedReviewer):
			add_product_review(web_product, "Great Product", 3, "Would recommend this product")

		# tear down
		frappe.set_user("Administrator")

	def test_add_product_reviews_from_guest_user(self):
		"Check if Guest user is blocked from posting reviews."
		web_product = frappe.db.get_value("Website Product", {"product_code": "Test Mobile Phone"})
		frappe.set_user("Guest")

		with self.assertRaises(UnverifiedReviewer):
			add_product_review(web_product, "Great Product", 3, "Would recommend this product")

		# tear down
		frappe.set_user("Administrator")
