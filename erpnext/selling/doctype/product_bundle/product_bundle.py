# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_link_to_form


class ProductBundle(Document):
	def autoname(self):
		self.name = self.new_product_code

	def validate(self):
		self.validate_main_product()
		self.validate_child_products()
		from erpnext.utilities.transaction_base import validate_uom_is_integer

		validate_uom_is_integer(self, "uom", "qty")

	def on_trash(self):
		linked_doctypes = [
			"Delivery Note",
			"Sales Invoice",
			"POS Invoice",
			"Purchase Receipt",
			"Purchase Invoice",
			"Stock Entry",
			"Stock Reconciliation",
			"Sales Order",
			"Purchase Order",
			"Material Request",
		]

		invoice_links = []
		for doctype in linked_doctypes:
			product_doctype = doctype + " Product"

			if doctype == "Stock Entry":
				product_doctype = doctype + " Detail"

			invoices = frappe.db.get_all(
				product_doctype, {"product_code": self.new_product_code, "docstatus": 1}, ["parent"]
			)

			for invoice in invoices:
				invoice_links.append(get_link_to_form(doctype, invoice["parent"]))

		if len(invoice_links):
			frappe.throw(
				"This Product Bundle is linked with {0}. You will have to cancel these documents in order to delete this Product Bundle".format(
					", ".join(invoice_links)
				),
				title=_("Not Allowed"),
			)

	def validate_main_product(self):
		"""Validates, main Product is not a stock product"""
		if frappe.db.get_value("Product", self.new_product_code, "is_stock_product"):
			frappe.throw(_("Parent Product {0} must not be a Stock Product").format(self.new_product_code))

	def validate_child_products(self):
		for product in self.products:
			if frappe.db.exists("Product Bundle", product.product_code):
				frappe.throw(
					_(
						"Row #{0}: Child Product should not be a Product Bundle. Please remove Product {1} and Save"
					).format(product.idx, frappe.bold(product.product_code))
				)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_new_product_code(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_match_cond

	return frappe.db.sql(
		"""select name, product_name, description from tabProduct
		where is_stock_product=0 and name not in (select name from `tabProduct Bundle`)
		and %s like %s %s limit %s offset %s"""
		% (searchfield, "%s", get_match_cond(doctype), "%s", "%s"),
		("%%%s%%" % txt, page_len, start),
	)
