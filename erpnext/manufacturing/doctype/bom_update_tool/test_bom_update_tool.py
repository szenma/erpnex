# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase, timeout

from erpnext.manufacturing.doctype.bom_update_log.test_bom_update_log import (
	update_cost_in_all_boms_in_test,
)
from erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool import enqueue_replace_bom
from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
from erpnext.stock.doctype.product.test_product import create_product

test_records = frappe.get_test_records("BOM")


class TestBOMUpdateTool(FrappeTestCase):
	"Test major functions run via BOM Update Tool."

	def tearDown(self):
		frappe.db.rollback()

	@timeout
	def test_replace_bom(self):
		current_bom = "BOM-_Test Product Home Desktop Manufactured-001"

		bom_doc = frappe.copy_doc(test_records[0])
		bom_doc.products[1].product_code = "_Test Product"
		bom_doc.insert()

		boms = frappe._dict(current_bom=current_bom, new_bom=bom_doc.name)
		enqueue_replace_bom(boms=boms)

		self.assertFalse(frappe.db.exists("BOM Product", {"bom_no": current_bom, "docstatus": 1}))
		self.assertTrue(frappe.db.exists("BOM Product", {"bom_no": bom_doc.name, "docstatus": 1}))

	@timeout
	def test_bom_cost(self):
		for product in ["BOM Cost Test Product 1", "BOM Cost Test Product 2", "BOM Cost Test Product 3"]:
			product_doc = create_product(product, valuation_rate=100)
			if product_doc.valuation_rate != 100.00:
				frappe.db.set_value("Product", product_doc.name, "valuation_rate", 100)

		bom_no = frappe.db.get_value("BOM", {"product": "BOM Cost Test Product 1"}, "name")
		if not bom_no:
			doc = make_bom(
				product="BOM Cost Test Product 1",
				raw_materials=["BOM Cost Test Product 2", "BOM Cost Test Product 3"],
				currency="INR",
			)
		else:
			doc = frappe.get_doc("BOM", bom_no)

		self.assertEqual(doc.total_cost, 200)

		frappe.db.set_value("Product", "BOM Cost Test Product 2", "valuation_rate", 200)
		update_cost_in_all_boms_in_test()

		doc.load_from_db()
		self.assertEqual(doc.total_cost, 300)

		frappe.db.set_value("Product", "BOM Cost Test Product 2", "valuation_rate", 100)
		update_cost_in_all_boms_in_test()

		doc.load_from_db()
		self.assertEqual(doc.total_cost, 200)
