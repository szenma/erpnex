# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document


class ProductAlternative(Document):
	def validate(self):
		self.has_alternative_product()
		self.validate_alternative_product()
		self.validate_duplicate()

	def has_alternative_product(self):
		if self.product_code and not frappe.db.get_value("Product", self.product_code, "allow_alternative_product"):
			frappe.throw(_("Not allow to set alternative product for the product {0}").format(self.product_code))

	def validate_alternative_product(self):
		if self.product_code == self.alternative_product_code:
			frappe.throw(_("Alternative product must not be same as product code"))

		product_meta = frappe.get_meta("Product")
		fields = [
			"is_stock_product",
			"include_product_in_manufacturing",
			"has_serial_no",
			"has_batch_no",
			"allow_alternative_product",
		]
		product_data = frappe.db.get_value("Product", self.product_code, fields, as_dict=1)
		alternative_product_data = frappe.db.get_value(
			"Product", self.alternative_product_code, fields, as_dict=1
		)

		for field in fields:
			if product_data.get(field) != alternative_product_data.get(field):
				raise_exception, alert = [1, False] if field == "is_stock_product" else [0, True]

				frappe.msgprint(
					_("The value of {0} differs between Products {1} and {2}").format(
						frappe.bold(product_meta.get_label(field)),
						frappe.bold(self.alternative_product_code),
						frappe.bold(self.product_code),
					),
					alert=alert,
					raise_exception=raise_exception,
					indicator="Orange",
				)

		alternate_product_check_msg = _("Allow Alternative Product must be checked on Product {}")

		if not product_data.allow_alternative_product:
			frappe.throw(alternate_product_check_msg.format(self.product_code))
		if self.two_way and not alternative_product_data.allow_alternative_product:
			frappe.throw(alternate_product_check_msg.format(self.alternative_product_code))

	def validate_duplicate(self):
		if frappe.db.get_value(
			"Product Alternative",
			{
				"product_code": self.product_code,
				"alternative_product_code": self.alternative_product_code,
				"name": ("!=", self.name),
			},
		):
			frappe.throw(_("Already record exists for the product {0}").format(self.product_code))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_alternative_products(doctype, txt, searchfield, start, page_len, filters):
	return frappe.db.sql(
		""" (select alternative_product_code from `tabProduct Alternative`
			where product_code = %(product_code)s and alternative_product_code like %(txt)s)
		union
			(select product_code from `tabProduct Alternative`
			where alternative_product_code = %(product_code)s and product_code like %(txt)s
			and two_way = 1) limit {1} offset {0}
		""".format(
			start, page_len
		),
		{"product_code": filters.get("product_code"), "txt": "%" + txt + "%"},
	)
