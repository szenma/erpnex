# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _, bold
from frappe.model.document import Document
from frappe.query_builder import Criterion
from frappe.query_builder.functions import Cast_
from frappe.utils import getdate


class ProductPriceDuplicateProduct(frappe.ValidationError):
	pass


class ProductPrice(Document):
	def validate(self):
		self.validate_product()
		self.validate_dates()
		self.update_price_list_details()
		self.update_product_details()
		self.check_duplicates()
		self.validate_product_template()

	def validate_product(self):
		if not frappe.db.exists("Product", self.product_code):
			frappe.throw(_("Product {0} not found.").format(self.product_code))

	def validate_dates(self):
		if self.valid_from and self.valid_upto:
			if getdate(self.valid_from) > getdate(self.valid_upto):
				frappe.throw(_("Valid From Date must be lesser than Valid Upto Date."))

	def update_price_list_details(self):
		if self.price_list:
			price_list_details = frappe.db.get_value(
				"Price List", {"name": self.price_list, "enabled": 1}, ["buying", "selling", "currency"]
			)

			if not price_list_details:
				link = frappe.utils.get_link_to_form("Price List", self.price_list)
				frappe.throw("The price list {0} does not exist or is disabled".format(link))

			self.buying, self.selling, self.currency = price_list_details

	def update_product_details(self):
		if self.product_code:
			self.product_name, self.product_description = frappe.db.get_value(
				"Product", self.product_code, ["product_name", "description"]
			)

	def validate_product_template(self):
		if frappe.get_cached_value("Product", self.product_code, "has_variants"):
			msg = f"Product Price cannot be created for the template product {bold(self.product_code)}"

			frappe.throw(_(msg))

	def check_duplicates(self):

		product_price = frappe.qb.DocType("Product Price")

		query = (
			frappe.qb.from_(product_price)
			.select(product_price.price_list_rate)
			.where(
				(product_price.product_code == self.product_code)
				& (product_price.price_list == self.price_list)
				& (product_price.name != self.name)
			)
		)
		data_fields = (
			"uom",
			"valid_from",
			"valid_upto",
			"customer",
			"supplier",
			"batch_no",
		)

		number_fields = ["packing_unit"]

		for field in data_fields:
			if self.get(field):
				query = query.where(product_price[field] == self.get(field))
			else:
				query = query.where(
					Criterion.any(
						[
							product_price[field].isnull(),
							Cast_(product_price[field], "varchar") == "",
						]
					)
				)

		for field in number_fields:
			if self.get(field):
				query = query.where(product_price[field] == self.get(field))
			else:
				query = query.where(
					Criterion.any(
						[
							product_price[field].isnull(),
							product_price[field] == 0,
						]
					)
				)

		price_list_rate = query.run(as_dict=True)

		if price_list_rate:
			frappe.throw(
				_(
					"Product Price appears multiple times based on Price List, Supplier/Customer, Currency, Product, Batch, UOM, Qty, and Dates."
				),
				ProductPriceDuplicateProduct,
			)

	def before_save(self):
		if self.selling:
			self.reference = self.customer
		if self.buying:
			self.reference = self.supplier

		if self.selling and not self.buying:
			# if only selling then remove supplier
			self.supplier = None
		if self.buying and not self.selling:
			# if only buying then remove customer
			self.customer = None
