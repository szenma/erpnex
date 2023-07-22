# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.controllers.queries import product_query

test_dependencies = ["Product", "Customer", "Supplier"]


def create_party_specific_product(**args):
	psi = frappe.new_doc("Party Specific Product")
	psi.party_type = args.get("party_type")
	psi.party = args.get("party")
	psi.restrict_based_on = args.get("restrict_based_on")
	psi.based_on_value = args.get("based_on_value")
	psi.insert()


class TestPartySpecificProduct(FrappeTestCase):
	def setUp(self):
		self.customer = frappe.get_last_doc("Customer")
		self.supplier = frappe.get_last_doc("Supplier")
		self.product = frappe.get_last_doc("Product")

	def test_product_query_for_customer(self):
		create_party_specific_product(
			party_type="Customer",
			party=self.customer.name,
			restrict_based_on="Product",
			based_on_value=self.product.name,
		)
		filters = {"is_sales_product": 1, "customer": self.customer.name}
		products = product_query(
			doctype="Product", txt="", searchfield="name", start=0, page_len=20, filters=filters, as_dict=False
		)
		for product in products:
			self.assertEqual(product[0], self.product.name)

	def test_product_query_for_supplier(self):
		create_party_specific_product(
			party_type="Supplier",
			party=self.supplier.name,
			restrict_based_on="Product Group",
			based_on_value=self.product.product_group,
		)
		filters = {"supplier": self.supplier.name, "is_purchase_product": 1}
		products = product_query(
			doctype="Product", txt="", searchfield="name", start=0, page_len=20, filters=filters, as_dict=False
		)
		for product in products:
			self.assertEqual(product[2], self.product.product_group)
