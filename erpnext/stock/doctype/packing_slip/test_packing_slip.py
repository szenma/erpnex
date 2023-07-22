# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.selling.doctype.product_bundle.test_product_bundle import make_product_bundle
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from erpnext.stock.doctype.delivery_note.test_delivery_note import create_delivery_note
from erpnext.stock.doctype.product.test_product import make_product


class TestPackingSlip(FrappeTestCase):
	def test_packing_slip(self):
		# Step - 1: Create a Product Bundle
		products = create_products()
		make_product_bundle(products[0], products[1:], 5)

		# Step - 2: Create a Delivery Note (Draft) with Product Bundle
		dn = create_delivery_note(
			product_code=products[0],
			qty=2,
			do_not_save=True,
		)
		dn.append(
			"products",
			{
				"product_code": products[1],
				"warehouse": "_Test Warehouse - _TC",
				"qty": 10,
			},
		)
		dn.save()

		# Step - 3: Make a Packing Slip from Delivery Note for 4 Qty
		ps1 = make_packing_slip(dn.name)
		for product in ps1.products:
			product.qty = 4
		ps1.save()
		ps1.submit()

		# Test - 1: `Packed Qty` should be updated to 4 in Delivery Note Products and Packed Products.
		dn.load_from_db()
		for product in dn.products:
			if not frappe.db.exists("Product Bundle", {"new_product_code": product.product_code}):
				self.assertEqual(product.packed_qty, 4)

		for product in dn.packed_products:
			self.assertEqual(product.packed_qty, 4)

		# Step - 4: Make another Packing Slip from Delivery Note for 6 Qty
		ps2 = make_packing_slip(dn.name)
		ps2.save()
		ps2.submit()

		# Test - 2: `Packed Qty` should be updated to 10 in Delivery Note Products and Packed Products.
		dn.load_from_db()
		for product in dn.products:
			if not frappe.db.exists("Product Bundle", {"new_product_code": product.product_code}):
				self.assertEqual(product.packed_qty, 10)

		for product in dn.packed_products:
			self.assertEqual(product.packed_qty, 10)

		# Step - 5: Cancel Packing Slip [1]
		ps1.cancel()

		# Test - 3: `Packed Qty` should be updated to 4 in Delivery Note Products and Packed Products.
		dn.load_from_db()
		for product in dn.products:
			if not frappe.db.exists("Product Bundle", {"new_product_code": product.product_code}):
				self.assertEqual(product.packed_qty, 6)

		for product in dn.packed_products:
			self.assertEqual(product.packed_qty, 6)

		# Step - 6: Cancel Packing Slip [2]
		ps2.cancel()

		# Test - 4: `Packed Qty` should be updated to 0 in Delivery Note Products and Packed Products.
		dn.load_from_db()
		for product in dn.products:
			if not frappe.db.exists("Product Bundle", {"new_product_code": product.product_code}):
				self.assertEqual(product.packed_qty, 0)

		for product in dn.packed_products:
			self.assertEqual(product.packed_qty, 0)

		# Step - 7: Make Packing Slip for more Qty than Delivery Note
		ps3 = make_packing_slip(dn.name)
		ps3.products[0].qty = 20

		# Test - 5: Should throw an ValidationError, as Packing Slip Qty is more than Delivery Note Qty
		self.assertRaises(frappe.exceptions.ValidationError, ps3.save)

		# Step - 8: Make Packing Slip for less Qty than Delivery Note
		ps4 = make_packing_slip(dn.name)
		ps4.products[0].qty = 5
		ps4.save()
		ps4.submit()

		# Test - 6: Delivery Note should throw a ValidationError on Submit, as Packed Qty and Delivery Note Qty are not the same
		dn.load_from_db()
		self.assertRaises(frappe.exceptions.ValidationError, dn.submit)


def create_products():
	products_properties = [
		{"is_stock_product": 0},
		{"is_stock_product": 1, "stock_uom": "Nos"},
		{"is_stock_product": 1, "stock_uom": "Box"},
	]

	products = []
	for properties in products_properties:
		products.append(make_product(properties=properties).name)

	return products
