# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from erpnext.controllers.subcontracting_controller import make_rm_stock_entry
from erpnext.controllers.tests.test_subcontracting_controller import (
	get_subcontracting_order,
	make_service_product,
	set_backflush_based_on,
)
from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
from erpnext.manufacturing.doctype.work_order.test_work_order import make_wo_order_test_record
from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
from erpnext.stock.doctype.product.test_product import create_product
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import (
	EmptyStockReconciliationProductsError,
)
from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import (
	create_stock_reconciliation,
)
from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
	make_subcontracting_receipt,
)


class TestProductAlternative(FrappeTestCase):
	def setUp(self):
		super().setUp()
		make_products()

	def test_alternative_product_for_subcontract_rm(self):
		set_backflush_based_on("BOM")

		create_stock_reconciliation(
			product_code="Alternate Product For A RW 1", warehouse="_Test Warehouse - _TC", qty=5, rate=2000
		)
		create_stock_reconciliation(
			product_code="Test FG A RW 2", warehouse="_Test Warehouse - _TC", qty=5, rate=2000
		)

		supplier_warehouse = "Test Supplier Warehouse - _TC"

		make_service_product("Subcontracted Service Product 1")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 5,
				"rate": 3000,
				"fg_product": "Test Finished Goods - A",
				"fg_product_qty": 5,
			},
		]
		sco = get_subcontracting_order(
			service_products=service_products, supplier_warehouse=supplier_warehouse
		)
		rm_products = [
			{
				"product_code": "Test Finished Goods - A",
				"rm_product_code": "Test FG A RW 1",
				"product_name": "Test FG A RW 1",
				"qty": 5,
				"warehouse": "_Test Warehouse - _TC",
				"rate": 2000,
				"amount": 10000,
				"stock_uom": "Nos",
			},
			{
				"product_code": "Test Finished Goods - A",
				"rm_product_code": "Test FG A RW 2",
				"product_name": "Test FG A RW 2",
				"qty": 5,
				"warehouse": "_Test Warehouse - _TC",
				"rate": 2000,
				"amount": 10000,
				"stock_uom": "Nos",
			},
		]

		reserved_qty_for_sub_contract = frappe.db.get_value(
			"Bin",
			{"product_code": "Test FG A RW 1", "warehouse": "_Test Warehouse - _TC"},
			"reserved_qty_for_sub_contract",
		)

		se = frappe.get_doc(make_rm_stock_entry(sco.name, rm_products))
		se.to_warehouse = supplier_warehouse
		se.insert()

		doc = frappe.get_doc("Stock Entry", se.name)
		for product in doc.products:
			if product.product_code == "Test FG A RW 1":
				product.product_code = "Alternate Product For A RW 1"
				product.product_name = "Alternate Product For A RW 1"
				product.description = "Alternate Product For A RW 1"
				product.original_product = "Test FG A RW 1"

		doc.save()
		doc.submit()
		after_transfer_reserved_qty_for_sub_contract = frappe.db.get_value(
			"Bin",
			{"product_code": "Test FG A RW 1", "warehouse": "_Test Warehouse - _TC"},
			"reserved_qty_for_sub_contract",
		)

		self.assertEqual(
			after_transfer_reserved_qty_for_sub_contract, flt(reserved_qty_for_sub_contract - 5)
		)

		scr = make_subcontracting_receipt(sco.name)
		scr.save()

		scr = frappe.get_doc("Subcontracting Receipt", scr.name)
		status = False
		for product in scr.supplied_products:
			if product.rm_product_code == "Alternate Product For A RW 1":
				status = True

		self.assertEqual(status, True)
		set_backflush_based_on("Material Transferred for Subcontract")

	def test_alternative_product_for_production_rm(self):
		create_stock_reconciliation(
			product_code="Alternate Product For A RW 1", warehouse="_Test Warehouse - _TC", qty=5, rate=2000
		)
		create_stock_reconciliation(
			product_code="Test FG A RW 2", warehouse="_Test Warehouse - _TC", qty=5, rate=2000
		)
		pro_order = make_wo_order_test_record(
			production_product="Test Finished Goods - A",
			qty=5,
			source_warehouse="_Test Warehouse - _TC",
			wip_warehouse="Test Supplier Warehouse - _TC",
		)

		reserved_qty_for_production = frappe.db.get_value(
			"Bin",
			{"product_code": "Test FG A RW 1", "warehouse": "_Test Warehouse - _TC"},
			"reserved_qty_for_production",
		)

		ste = frappe.get_doc(make_stock_entry(pro_order.name, "Material Transfer for Manufacture", 5))
		ste.insert()

		for product in ste.products:
			if product.product_code == "Test FG A RW 1":
				product.product_code = "Alternate Product For A RW 1"
				product.product_name = "Alternate Product For A RW 1"
				product.description = "Alternate Product For A RW 1"
				product.original_product = "Test FG A RW 1"

		ste.submit()
		reserved_qty_for_production_after_transfer = frappe.db.get_value(
			"Bin",
			{"product_code": "Test FG A RW 1", "warehouse": "_Test Warehouse - _TC"},
			"reserved_qty_for_production",
		)

		self.assertEqual(
			reserved_qty_for_production_after_transfer, flt(reserved_qty_for_production - 5)
		)
		ste1 = frappe.get_doc(make_stock_entry(pro_order.name, "Manufacture", 5))

		status = False
		for d in ste1.products:
			if d.product_code == "Alternate Product For A RW 1":
				status = True

		self.assertEqual(status, True)
		ste1.submit()


def make_products():
	products = [
		"Test Finished Goods - A",
		"Test FG A RW 1",
		"Test FG A RW 2",
		"Alternate Product For A RW 1",
	]
	for product_code in products:
		if not frappe.db.exists("Product", product_code):
			create_product(product_code)

	try:
		create_stock_reconciliation(
			product_code="Test FG A RW 1", warehouse="_Test Warehouse - _TC", qty=10, rate=2000
		)
	except EmptyStockReconciliationProductsError:
		pass

	if frappe.db.exists("Product", "Test FG A RW 1"):
		doc = frappe.get_doc("Product", "Test FG A RW 1")
		doc.allow_alternative_product = 1
		doc.save()

	if frappe.db.exists("Product", "Test Finished Goods - A"):
		doc = frappe.get_doc("Product", "Test Finished Goods - A")
		doc.is_sub_contracted_product = 1
		doc.save()

	if not frappe.db.get_value("BOM", {"product": "Test Finished Goods - A", "docstatus": 1}):
		make_bom(product="Test Finished Goods - A", raw_materials=["Test FG A RW 1", "Test FG A RW 2"])

	if not frappe.db.get_value("Warehouse", {"warehouse_name": "Test Supplier Warehouse"}):
		frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": "Test Supplier Warehouse",
				"company": "_Test Company",
			}
		).insert(ignore_permissions=True)
