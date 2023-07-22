# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document


class ProductManufacturer(Document):
	def validate(self):
		self.validate_duplicate_entry()
		self.manage_default_product_manufacturer()

	def on_trash(self):
		self.manage_default_product_manufacturer(delete=True)

	def validate_duplicate_entry(self):
		if self.is_new():
			filters = {
				"product_code": self.product_code,
				"manufacturer": self.manufacturer,
				"manufacturer_part_no": self.manufacturer_part_no,
			}

			if frappe.db.exists("Product Manufacturer", filters):
				frappe.throw(
					_("Duplicate entry against the product code {0} and manufacturer {1}").format(
						self.product_code, self.manufacturer
					)
				)

	def manage_default_product_manufacturer(self, delete=False):
		from frappe.model.utils import set_default

		product = frappe.get_doc("Product", self.product_code)
		default_manufacturer = product.default_product_manufacturer
		default_part_no = product.default_manufacturer_part_no

		if not self.is_default:
			# if unchecked and default in Product master, clear it.
			if default_manufacturer == self.manufacturer and default_part_no == self.manufacturer_part_no:
				frappe.db.set_value(
					"Product", product.name, {"default_product_manufacturer": None, "default_manufacturer_part_no": None}
				)

		elif self.is_default:
			set_default(self, "product_code")
			manufacturer, manufacturer_part_no = default_manufacturer, default_part_no

			if delete:
				manufacturer, manufacturer_part_no = None, None

			elif (default_manufacturer != self.manufacturer) or (
				default_manufacturer == self.manufacturer and default_part_no != self.manufacturer_part_no
			):
				manufacturer = self.manufacturer
				manufacturer_part_no = self.manufacturer_part_no

			frappe.db.set_value(
				"Product",
				product.name,
				{
					"default_product_manufacturer": manufacturer,
					"default_manufacturer_part_no": manufacturer_part_no,
				},
			)


@frappe.whitelist()
def get_product_manufacturer_part_no(product_code, manufacturer):
	return frappe.db.get_value(
		"Product Manufacturer",
		{"product_code": product_code, "manufacturer": manufacturer},
		"manufacturer_part_no",
	)
