import json
import unittest

import frappe

from erpnext.controllers.product_variant import copy_attributes_to_variant, make_variant_product_code
from erpnext.stock.doctype.product.test_product import set_product_variant_settings
from erpnext.stock.doctype.quality_inspection.test_quality_inspection import (
	create_quality_inspection_parameter,
)


class TestProductVariant(unittest.TestCase):
	def test_tables_in_template_copied_to_variant(self):
		fields = [{"field_name": "quality_inspection_template"}]
		set_product_variant_settings(fields)
		variant = make_product_variant()
		self.assertEqual(variant.get("quality_inspection_template"), "_Test QC Template")


def create_variant_with_tables(product, args):
	if isinstance(args, str):
		args = json.loads(args)

	qc_name = make_quality_inspection_template()
	template = frappe.get_doc("Product", product)
	template.quality_inspection_template = qc_name
	template.save()

	variant = frappe.new_doc("Product")
	variant.variant_based_on = "Product Attribute"
	variant_attributes = []

	for d in template.attributes:
		variant_attributes.append({"attribute": d.attribute, "attribute_value": args.get(d.attribute)})

	variant.set("attributes", variant_attributes)
	copy_attributes_to_variant(template, variant)
	make_variant_product_code(template.product_code, template.product_name, variant)

	return variant


def make_product_variant():
	frappe.delete_doc_if_exists("Product", "_Test Variant Product-XSL", force=1)
	variant = create_variant_with_tables("_Test Variant Product", '{"Test Size": "Extra Small"}')
	variant.product_code = "_Test Variant Product-XSL"
	variant.product_name = "_Test Variant Product-XSL"
	variant.save()
	return variant


def make_quality_inspection_template():
	qc_template = "_Test QC Template"
	if frappe.db.exists("Quality Inspection Template", qc_template):
		return qc_template

	qc = frappe.new_doc("Quality Inspection Template")
	qc.quality_inspection_template_name = qc_template

	create_quality_inspection_parameter("Moisture")
	qc.append(
		"product_quality_inspection_parameter",
		{
			"specification": "Moisture",
			"value": "&lt; 5%",
		},
	)

	qc.insert()
	return qc.name
