# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors and Contributors
# See license.txt


import frappe

test_records = frappe.get_test_records("Product Attribute")

from frappe.tests.utils import FrappeTestCase

from erpnext.stock.doctype.product_attribute.product_attribute import ProductAttributeIncrementError


class TestProductAttribute(FrappeTestCase):
	def setUp(self):
		super().setUp()
		if frappe.db.exists("Product Attribute", "_Test_Length"):
			frappe.delete_doc("Product Attribute", "_Test_Length")

	def test_numeric_product_attribute(self):
		product_attribute = frappe.get_doc(
			{
				"doctype": "Product Attribute",
				"attribute_name": "_Test_Length",
				"numeric_values": 1,
				"from_range": 0.0,
				"to_range": 100.0,
				"increment": 0,
			}
		)

		self.assertRaises(ProductAttributeIncrementError, product_attribute.save)

		product_attribute.increment = 0.5
		product_attribute.save()
