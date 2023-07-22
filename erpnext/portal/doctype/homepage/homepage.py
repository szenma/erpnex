# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe.model.document import Document
from frappe.website.utils import delete_page_cache


class Homepage(Document):
	def validate(self):
		if not self.description:
			self.description = frappe._("This is an example website auto-generated from ERPNext")
		delete_page_cache("home")

	def setup_products(self):
		for d in frappe.get_all(
			"Website Product",
			fields=["name", "product_name", "description", "website_image", "route"],
			filters={"published": 1},
			limit=3,
		):

			doc = frappe.get_doc("Website Product", d.name)
			if not doc.route:
				# set missing route
				doc.save()
			self.append(
				"products",
				dict(
					product_code=d.name,
					product_name=d.product_name,
					description=d.description,
					image=d.website_image,
					route=d.route,
				),
			)
