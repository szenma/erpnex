import json
from typing import List, Union

import frappe

from erpnext.e_commerce.doctype.website_product.website_product import make_website_product


def execute():
	"""
	Convert all Product links to Website Product link values in
	exisitng 'Product Card Group' Web Page Block data.
	"""
	frappe.reload_doc("e_commerce", "web_template", "product_card_group")

	blocks = frappe.db.get_all(
		"Web Page Block",
		filters={"web_template": "Product Card Group"},
		fields=["parent", "web_template_values", "name"],
	)

	fields = generate_fields_to_edit()

	for block in blocks:
		web_template_value = json.loads(block.get("web_template_values"))

		for field in fields:
			product = web_template_value.get(field)
			if not product:
				continue

			if frappe.db.exists("Website Product", {"product_code": product}):
				website_product = frappe.db.get_value("Website Product", {"product_code": product})
			else:
				website_product = make_new_website_product(product)

			if website_product:
				web_template_value[field] = website_product

		frappe.db.set_value(
			"Web Page Block", block.name, "web_template_values", json.dumps(web_template_value)
		)


def generate_fields_to_edit() -> List:
	fields = []
	for i in range(1, 13):
		fields.append(f"card_{i}_product")  # fields like 'card_1_product', etc.

	return fields


def make_new_website_product(product: str) -> Union[str, None]:
	try:
		doc = frappe.get_doc("Product", product)
		web_product = make_website_product(doc)  # returns [website_product.name, product_name]
		return web_product[0]
	except Exception:
		doc.log_error("Website Product creation failed")
		return None
