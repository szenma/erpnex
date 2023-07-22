# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from erpnext.controllers.product_variant import (
	InvalidProductAttributeValueError,
	validate_is_incremental,
	validate_product_attribute_value,
)


class ProductAttributeIncrementError(frappe.ValidationError):
	pass


class ProductAttribute(Document):
	def __setup__(self):
		self.flags.ignore_these_exceptions_in_test = [InvalidProductAttributeValueError]

	def validate(self):
		frappe.flags.attribute_values = None
		self.validate_numeric()
		self.validate_duplication()

	def on_update(self):
		self.validate_exising_products()

	def validate_exising_products(self):
		"""Validate that if there are existing products with attributes, they are valid"""
		attributes_list = [d.attribute_value for d in self.product_attribute_values]

		# Get Product Variant Attribute details of variant products
		products = frappe.db.sql(
			"""
			select
				i.name, iva.attribute_value as value
			from
				`tabProduct Variant Attribute` iva, `tabProduct` i
			where
				iva.attribute = %(attribute)s
				and iva.parent = i.name and
				i.variant_of is not null and i.variant_of != ''""",
			{"attribute": self.name},
			as_dict=1,
		)

		for product in products:
			if self.numeric_values:
				validate_is_incremental(self, self.name, product.value, product.name)
			else:
				validate_product_attribute_value(
					attributes_list, self.name, product.value, product.name, from_variant=False
				)

	def validate_numeric(self):
		if self.numeric_values:
			self.set("product_attribute_values", [])
			if self.from_range is None or self.to_range is None:
				frappe.throw(_("Please specify from/to range"))

			elif flt(self.from_range) >= flt(self.to_range):
				frappe.throw(_("From Range has to be less than To Range"))

			if not self.increment:
				frappe.throw(_("Increment cannot be 0"), ProductAttributeIncrementError)
		else:
			self.from_range = self.to_range = self.increment = 0

	def validate_duplication(self):
		values, abbrs = [], []
		for d in self.product_attribute_values:
			if d.attribute_value.lower() in map(str.lower, values):
				frappe.throw(_("Attribute value: {0} must appear only once").format(d.attribute_value.title()))
			values.append(d.attribute_value)

			if d.abbr.lower() in map(str.lower, abbrs):
				frappe.throw(_("Abbreviation: {0} must appear only once").format(d.abbr.title()))
			abbrs.append(d.abbr)
