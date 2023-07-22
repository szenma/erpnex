# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, flt, now_datetime, nowdate

from erpnext.controllers.product_variant import create_variant
from erpnext.manufacturing.doctype.production_plan.production_plan import (
	get_products_for_material_requests,
	get_sales_orders,
	get_warehouse_list,
)
from erpnext.manufacturing.doctype.work_order.work_order import OverProductionError
from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry as make_se_from_wo
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.product.test_product import create_product, make_product
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry
from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import (
	create_stock_reconciliation,
)


class TestProductionPlan(FrappeTestCase):
	def setUp(self):
		for product in [
			"Test Production Product 1",
			"Subassembly Product 1",
			"Raw Material Product 1",
			"Raw Material Product 2",
		]:
			create_product(product, valuation_rate=100)

			sr = frappe.db.get_value(
				"Stock Reconciliation Product", {"product_code": product, "docstatus": 1}, "parent"
			)
			if sr:
				sr_doc = frappe.get_doc("Stock Reconciliation", sr)
				sr_doc.cancel()

		create_product("Test Non Stock Raw Material", is_stock_product=0)
		for product, raw_materials in {
			"Subassembly Product 1": ["Raw Material Product 1", "Raw Material Product 2"],
			"Test Production Product 1": [
				"Raw Material Product 1",
				"Subassembly Product 1",
				"Test Non Stock Raw Material",
			],
		}.products():
			if not frappe.db.get_value("BOM", {"product": product}):
				make_bom(product=product, raw_materials=raw_materials)

	def tearDown(self) -> None:
		frappe.db.rollback()

	def test_production_plan_mr_creation(self):
		"Test if MRs are created for unavailable raw materials."
		pln = create_production_plan(product_code="Test Production Product 1")
		self.assertTrue(len(pln.mr_products), 2)

		pln.make_material_request()
		pln.reload()
		self.assertTrue(pln.status, "Material Requested")

		material_requests = frappe.get_all(
			"Material Request Product",
			fields=["distinct parent"],
			filters={"production_plan": pln.name},
			as_list=1,
		)

		self.assertTrue(len(material_requests), 2)

		pln.make_work_order()
		work_orders = frappe.get_all(
			"Work Order", fields=["name"], filters={"production_plan": pln.name}, as_list=1
		)

		pln.make_work_order()
		nwork_orders = frappe.get_all(
			"Work Order", fields=["name"], filters={"production_plan": pln.name}, as_list=1
		)

		self.assertTrue(len(work_orders), len(nwork_orders))

		self.assertTrue(len(work_orders), len(pln.po_products))

		for name in material_requests:
			mr = frappe.get_doc("Material Request", name[0])
			if mr.docstatus != 0:
				mr.cancel()

		for name in work_orders:
			mr = frappe.delete_doc("Work Order", name[0])

		pln = frappe.get_doc("Production Plan", pln.name)
		pln.cancel()

	def test_production_plan_start_date(self):
		"Test if Work Order has same Planned Start Date as Prod Plan."
		planned_date = add_to_date(date=None, days=3)
		plan = create_production_plan(
			product_code="Test Production Product 1", planned_start_date=planned_date
		)
		plan.make_work_order()

		work_orders = frappe.get_all(
			"Work Order", fields=["name", "planned_start_date"], filters={"production_plan": plan.name}
		)

		self.assertEqual(work_orders[0].planned_start_date, planned_date)

		for wo in work_orders:
			frappe.delete_doc("Work Order", wo.name)

		plan.reload()
		plan.cancel()

	def test_production_plan_for_existing_ordered_qty(self):
		"""
		- Enable 'ignore_existing_ordered_qty'.
		- Test if MR Planning table pulls Raw Material Qty even if it is in stock.
		"""
		sr1 = create_stock_reconciliation(
			product_code="Raw Material Product 1", target="_Test Warehouse - _TC", qty=1, rate=110
		)
		sr2 = create_stock_reconciliation(
			product_code="Raw Material Product 2", target="_Test Warehouse - _TC", qty=1, rate=120
		)

		pln = create_production_plan(product_code="Test Production Product 1", ignore_existing_ordered_qty=1)
		self.assertTrue(len(pln.mr_products))
		self.assertTrue(flt(pln.mr_products[0].quantity), 1.0)

		sr1.cancel()
		sr2.cancel()
		pln.cancel()

	def test_production_plan_with_non_stock_product(self):
		"Test if MR Planning table includes Non Stock RM."
		pln = create_production_plan(product_code="Test Production Product 1", include_non_stock_products=1)
		self.assertTrue(len(pln.mr_products), 3)
		pln.cancel()

	def test_production_plan_without_multi_level(self):
		"Test MR Planning table for non exploded BOM."
		pln = create_production_plan(product_code="Test Production Product 1", use_multi_level_bom=0)
		self.assertTrue(len(pln.mr_products), 2)
		pln.cancel()

	def test_production_plan_without_multi_level_for_existing_ordered_qty(self):
		"""
		- Disable 'ignore_existing_ordered_qty'.
		- Test if MR Planning table avoids pulling Raw Material Qty as it is in stock for
		non exploded BOM.
		"""
		sr1 = create_stock_reconciliation(
			product_code="Raw Material Product 1", target="_Test Warehouse - _TC", qty=1, rate=130
		)
		sr2 = create_stock_reconciliation(
			product_code="Subassembly Product 1", target="_Test Warehouse - _TC", qty=1, rate=140
		)

		pln = create_production_plan(
			product_code="Test Production Product 1", use_multi_level_bom=0, ignore_existing_ordered_qty=0
		)
		self.assertFalse(len(pln.mr_products))

		sr1.cancel()
		sr2.cancel()
		pln.cancel()

	def test_production_plan_sales_orders(self):
		"Test if previously fulfilled SO (with WO) is pulled into Prod Plan."
		product = "Test Production Product 1"
		so = make_sales_order(product_code=product, qty=1)
		sales_order = so.name
		sales_order_product = so.products[0].name

		pln = frappe.new_doc("Production Plan")
		pln.company = so.company
		pln.get_products_from = "Sales Order"

		pln.append(
			"sales_orders",
			{
				"sales_order": so.name,
				"sales_order_date": so.transaction_date,
				"customer": so.customer,
				"grand_total": so.grand_total,
			},
		)

		pln.get_so_products()
		pln.submit()
		pln.make_work_order()

		work_order = frappe.db.get_value(
			"Work Order",
			{"sales_order": sales_order, "production_plan": pln.name, "sales_order_product": sales_order_product},
			"name",
		)

		wo_doc = frappe.get_doc("Work Order", work_order)
		wo_doc.update(
			{"wip_warehouse": "Work In Progress - _TC", "fg_warehouse": "Finished Goods - _TC"}
		)
		wo_doc.submit()

		so_wo_qty = frappe.db.get_value("Sales Order Product", sales_order_product, "work_order_qty")
		self.assertTrue(so_wo_qty, 5)

		pln = frappe.new_doc("Production Plan")
		pln.update(
			{
				"from_date": so.transaction_date,
				"to_date": so.transaction_date,
				"customer": so.customer,
				"product_code": product,
				"sales_order_status": so.status,
			}
		)
		sales_orders = get_sales_orders(pln) or {}
		sales_orders = [d.get("name") for d in sales_orders if d.get("name") == sales_order]

		self.assertEqual(sales_orders, [])

	def test_production_plan_combine_products(self):
		"Test combining FG products in Production Plan."
		product = "Test Production Product 1"
		so1 = make_sales_order(product_code=product, qty=1)

		pln = frappe.new_doc("Production Plan")
		pln.company = so1.company
		pln.get_products_from = "Sales Order"
		pln.append(
			"sales_orders",
			{
				"sales_order": so1.name,
				"sales_order_date": so1.transaction_date,
				"customer": so1.customer,
				"grand_total": so1.grand_total,
			},
		)
		so2 = make_sales_order(product_code=product, qty=2)
		pln.append(
			"sales_orders",
			{
				"sales_order": so2.name,
				"sales_order_date": so2.transaction_date,
				"customer": so2.customer,
				"grand_total": so2.grand_total,
			},
		)
		pln.combine_products = 1
		pln.get_products()
		pln.submit()

		self.assertTrue(pln.po_products[0].planned_qty, 3)

		pln.make_work_order()
		work_order = frappe.db.get_value(
			"Work Order",
			{"production_plan_product": pln.po_products[0].name, "production_plan": pln.name},
			"name",
		)

		wo_doc = frappe.get_doc("Work Order", work_order)
		wo_doc.update(
			{
				"wip_warehouse": "Work In Progress - _TC",
			}
		)

		wo_doc.submit()
		so_products = []
		for plan_reference in pln.prod_plan_references:
			so_products.append(plan_reference.sales_order_product)
			so_wo_qty = frappe.db.get_value(
				"Sales Order Product", plan_reference.sales_order_product, "work_order_qty"
			)
			self.assertEqual(so_wo_qty, plan_reference.qty)

		wo_doc.cancel()
		for so_product in so_products:
			so_wo_qty = frappe.db.get_value("Sales Order Product", so_product, "work_order_qty")
			self.assertEqual(so_wo_qty, 0.0)

		pln.reload()
		pln.cancel()

	def test_production_plan_subassembly_default_supplier(self):
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		bom_tree_1 = {"Test Laptop": {"Test Motherboard": {"Test Motherboard Wires": {}}}}
		bom = create_nested_bom(bom_tree_1, prefix="")

		product_doc = frappe.get_doc("Product", "Test Motherboard")
		company = "_Test Company"

		product_doc.is_sub_contracted_product = 1
		for row in product_doc.product_defaults:
			if row.company == company and not row.default_supplier:
				row.default_supplier = "_Test Supplier"

		if not product_doc.product_defaults:
			product_doc.append("product_defaults", {"company": company, "default_supplier": "_Test Supplier"})

		product_doc.save()

		plan = create_production_plan(product_code="Test Laptop", use_multi_level_bom=1, do_not_submit=True)
		plan.get_sub_assembly_products()
		plan.set_default_supplier_for_subcontracting_order()

		self.assertEqual(plan.sub_assembly_products[0].supplier, "_Test Supplier")

	def test_production_plan_for_subcontracting_po(self):
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		bom_tree_1 = {"Test Laptop 1": {"Test Motherboard 1": {"Test Motherboard Wires 1": {}}}}
		create_nested_bom(bom_tree_1, prefix="")

		product_doc = frappe.get_doc("Product", "Test Motherboard 1")
		company = "_Test Company"

		product_doc.is_sub_contracted_product = 1
		for row in product_doc.product_defaults:
			if row.company == company and not row.default_supplier:
				row.default_supplier = "_Test Supplier"

		if not product_doc.product_defaults:
			product_doc.append("product_defaults", {"company": company, "default_supplier": "_Test Supplier"})

		product_doc.save()

		plan = create_production_plan(
			product_code="Test Laptop 1", planned_qty=10, use_multi_level_bom=1, do_not_submit=True
		)
		plan.get_sub_assembly_products()
		plan.set_default_supplier_for_subcontracting_order()
		plan.submit()

		self.assertEqual(plan.sub_assembly_products[0].supplier, "_Test Supplier")
		plan.make_work_order()

		po = frappe.db.get_value("Purchase Order Product", {"production_plan": plan.name}, "parent")
		po_doc = frappe.get_doc("Purchase Order", po)
		self.assertEqual(po_doc.supplier, "_Test Supplier")
		self.assertEqual(po_doc.products[0].qty, 10.0)
		self.assertEqual(po_doc.products[0].fg_product_qty, 10.0)
		self.assertEqual(po_doc.products[0].fg_product_qty, 10.0)
		self.assertEqual(po_doc.products[0].fg_product, "Test Motherboard 1")

	def test_production_plan_combine_subassembly(self):
		"""
		Test combining Sub assembly products belonging to the same BOM in Prod Plan.
		1) Red-Car -> Wheel (sub assembly) > BOM-WHEEL-001
		2) Green-Car -> Wheel (sub assembly) > BOM-WHEEL-001
		"""
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		bom_tree_1 = {"Red-Car": {"Wheel": {"Rubber": {}}}}
		bom_tree_2 = {"Green-Car": {"Wheel": {"Rubber": {}}}}

		parent_bom_1 = create_nested_bom(bom_tree_1, prefix="")
		parent_bom_2 = create_nested_bom(bom_tree_2, prefix="")

		# make sure both boms use same subassembly bom
		subassembly_bom = parent_bom_1.products[0].bom_no
		frappe.db.set_value("BOM Product", parent_bom_2.products[0].name, "bom_no", subassembly_bom)

		plan = create_production_plan(product_code="Red-Car", use_multi_level_bom=1, do_not_save=True)
		plan.append(
			"po_products",
			{  # Add Green-Car to Prod Plan
				"use_multi_level_bom": 1,
				"product_code": "Green-Car",
				"bom_no": frappe.db.get_value("Product", "Green-Car", "default_bom"),
				"planned_qty": 1,
				"planned_start_date": now_datetime(),
			},
		)
		plan.get_sub_assembly_products()
		self.assertTrue(len(plan.sub_assembly_products), 2)

		plan.combine_sub_products = 1
		plan.get_sub_assembly_products()

		self.assertTrue(len(plan.sub_assembly_products), 1)  # check if sub-assembly products merged
		self.assertEqual(plan.sub_assembly_products[0].qty, 2.0)
		self.assertEqual(plan.sub_assembly_products[0].stock_qty, 2.0)

		# change warehouse in one row, sub-assemblies should not merge
		plan.po_products[0].warehouse = "Finished Goods - _TC"
		plan.get_sub_assembly_products()
		self.assertTrue(len(plan.sub_assembly_products), 2)

	def test_pp_to_mr_customer_provided(self):
		"Test Material Request from Production Plan for Customer Provided Product."
		create_product(
			"CUST-0987", is_customer_provided_product=1, customer="_Test Customer", is_purchase_product=0
		)
		create_product("Production Product CUST")

		for product, raw_materials in {
			"Production Product CUST": ["Raw Material Product 1", "CUST-0987"]
		}.products():
			if not frappe.db.get_value("BOM", {"product": product}):
				make_bom(product=product, raw_materials=raw_materials)
		production_plan = create_production_plan(product_code="Production Product CUST")
		production_plan.make_material_request()

		material_request = frappe.db.get_value(
			"Material Request Product",
			{"production_plan": production_plan.name, "product_code": "CUST-0987"},
			"parent",
		)
		mr = frappe.get_doc("Material Request", material_request)

		self.assertTrue(mr.material_request_type, "Customer Provided")
		self.assertTrue(mr.customer, "_Test Customer")

	def test_production_plan_with_multi_level_bom(self):
		"""
		Product Code	|	Qty	|
		|Test BOM 1	|	1	|
		|Test BOM 2	|	2	|
		|Test BOM 3	|	3	|
		"""

		for product_code in ["Test BOM 1", "Test BOM 2", "Test BOM 3", "Test RM BOM 1"]:
			create_product(product_code, is_stock_product=1)

		# created bom upto 3 level
		if not frappe.db.get_value("BOM", {"product": "Test BOM 3"}):
			make_bom(product="Test BOM 3", raw_materials=["Test RM BOM 1"], rm_qty=3)

		if not frappe.db.get_value("BOM", {"product": "Test BOM 2"}):
			make_bom(product="Test BOM 2", raw_materials=["Test BOM 3"], rm_qty=3)

		if not frappe.db.get_value("BOM", {"product": "Test BOM 1"}):
			make_bom(product="Test BOM 1", raw_materials=["Test BOM 2"], rm_qty=2)

		product_code = "Test BOM 1"
		pln = frappe.new_doc("Production Plan")
		pln.company = "_Test Company"
		pln.append(
			"po_products",
			{
				"product_code": product_code,
				"bom_no": frappe.db.get_value("BOM", {"product": "Test BOM 1"}),
				"planned_qty": 3,
			},
		)

		pln.get_sub_assembly_products("In House")
		pln.submit()
		pln.make_work_order()

		# last level sub-assembly work order produce qty
		to_produce_qty = frappe.db.get_value(
			"Work Order", {"production_plan": pln.name, "production_product": "Test BOM 3"}, "qty"
		)

		self.assertEqual(to_produce_qty, 18.0)
		pln.cancel()
		frappe.delete_doc("Production Plan", pln.name)

	def test_get_warehouse_list_group(self):
		"Check if required child warehouses are returned."
		warehouse_json = '[{"warehouse":"_Test Warehouse Group - _TC"}]'

		warehouses = set(get_warehouse_list(warehouse_json))
		expected_warehouses = {"_Test Warehouse Group-C1 - _TC", "_Test Warehouse Group-C2 - _TC"}

		missing_warehouse = expected_warehouses - warehouses

		self.assertTrue(
			len(missing_warehouse) == 0,
			msg=f"Following warehouses were expected {', '.join(missing_warehouse)}",
		)

	def test_get_warehouse_list_single(self):
		"Check if same warehouse is returned in absence of child warehouses."
		warehouse_json = '[{"warehouse":"_Test Scrap Warehouse - _TC"}]'

		warehouses = set(get_warehouse_list(warehouse_json))
		expected_warehouses = {
			"_Test Scrap Warehouse - _TC",
		}

		self.assertEqual(warehouses, expected_warehouses)

	def test_get_sales_order_with_variant(self):
		"Check if Template BOM is fetched in absence of Variant BOM."
		rm_product = create_product("PIV_RM", valuation_rate=100)
		if not frappe.db.exists("Product", {"product_code": "PIV"}):
			product = create_product("PIV", valuation_rate=100)
			variant_settings = {
				"attributes": [
					{"attribute": "Colour"},
				],
				"has_variants": 1,
			}
			product.update(variant_settings)
			product.save()
			parent_bom = make_bom(product="PIV", raw_materials=[rm_product.product_code])
		if not frappe.db.exists("BOM", {"product": "PIV"}):
			parent_bom = make_bom(product="PIV", raw_materials=[rm_product.product_code])
		else:
			parent_bom = frappe.get_doc("BOM", {"product": "PIV"})

		if not frappe.db.exists("Product", {"product_code": "PIV-RED"}):
			variant = create_variant("PIV", {"Colour": "Red"})
			variant.save()
			variant_bom = make_bom(product=variant.product_code, raw_materials=[rm_product.product_code])
		else:
			variant = frappe.get_doc("Product", "PIV-RED")
		if not frappe.db.exists("BOM", {"product": "PIV-RED"}):
			variant_bom = make_bom(product=variant.product_code, raw_materials=[rm_product.product_code])

		"""Testing when product variant has a BOM"""
		so = make_sales_order(product_code="PIV-RED", qty=5)
		pln = frappe.new_doc("Production Plan")
		pln.company = so.company
		pln.get_products_from = "Sales Order"
		pln.product_code = "PIV-RED"
		pln.get_open_sales_orders()
		self.assertEqual(pln.sales_orders[0].sales_order, so.name)
		pln.get_so_products()
		self.assertEqual(pln.po_products[0].product_code, "PIV-RED")
		self.assertEqual(pln.po_products[0].bom_no, variant_bom.name)
		so.cancel()
		frappe.delete_doc("Sales Order", so.name)
		variant_bom.cancel()
		frappe.delete_doc("BOM", variant_bom.name)

		"""Testing when product variant doesn't have a BOM"""
		so = make_sales_order(product_code="PIV-RED", qty=5)
		pln.get_open_sales_orders()
		self.assertEqual(pln.sales_orders[0].sales_order, so.name)
		pln.po_products = []
		pln.get_so_products()
		self.assertEqual(pln.po_products[0].product_code, "PIV-RED")
		self.assertEqual(pln.po_products[0].bom_no, parent_bom.name)

		frappe.db.rollback()

	def test_subassmebly_sorting(self):
		"Test subassembly sorting in case of multiple products with nested BOMs."
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		prefix = "_TestLevel_"
		boms = {
			"Assembly": {
				"SubAssembly1": {
					"ChildPart1": {},
					"ChildPart2": {},
				},
				"ChildPart6": {},
				"SubAssembly4": {"SubSubAssy2": {"ChildPart7": {}}},
			},
			"MegaDeepAssy": {
				"SecretSubassy": {
					"SecretPart": {"VerySecret": {"SuperSecret": {"Classified": {}}}},
				},
				# ^ assert that this is
				# first product in subassy table
			},
		}
		create_nested_bom(boms, prefix=prefix)

		products = [prefix + product_code for product_code in boms.keys()]
		plan = create_production_plan(product_code=products[0], do_not_save=True)
		plan.append(
			"po_products",
			{
				"use_multi_level_bom": 1,
				"product_code": products[1],
				"bom_no": frappe.db.get_value("Product", products[1], "default_bom"),
				"planned_qty": 1,
				"planned_start_date": now_datetime(),
			},
		)
		plan.get_sub_assembly_products()

		bom_level_order = [d.bom_level for d in plan.sub_assembly_products]
		self.assertEqual(bom_level_order, sorted(bom_level_order, reverse=True))
		# lowest most level of subassembly should be first
		self.assertIn("SuperSecret", plan.sub_assembly_products[0].production_product)

	def test_multiple_work_order_for_production_plan_product(self):
		"Test producing Prod Plan (making WO) in parts."

		def create_work_order(product, pln, qty):
			# Get Production Products
			products_data = pln.get_production_products()

			# Update qty
			products_data[(product, None, None)]["qty"] = qty

			# Create and Submit Work Order for each product in products_data
			for key, product in products_data.products():
				if pln.sub_assembly_products:
					product["use_multi_level_bom"] = 0

				wo_name = pln.create_work_order(product)
				wo_doc = frappe.get_doc("Work Order", wo_name)
				wo_doc.update(
					{"wip_warehouse": "Work In Progress - _TC", "fg_warehouse": "Finished Goods - _TC"}
				)
				wo_doc.submit()
				wo_list.append(wo_name)

		product = "Test Production Product 1"
		raw_materials = ["Raw Material Product 1", "Raw Material Product 2"]

		# Create BOM
		bom = make_bom(product=product, raw_materials=raw_materials)

		# Create Production Plan
		pln = create_production_plan(product_code=bom.product, planned_qty=5)

		# All the created Work Orders
		wo_list = []

		# Create and Submit 1st Work Order for 3 qty
		create_work_order(product, pln, 3)
		pln.reload()
		self.assertEqual(pln.po_products[0].ordered_qty, 3)

		# Create and Submit 2nd Work Order for 2 qty
		create_work_order(product, pln, 2)
		pln.reload()
		self.assertEqual(pln.po_products[0].ordered_qty, 5)

		# Overproduction
		self.assertRaises(OverProductionError, create_work_order, product=product, pln=pln, qty=2)

		# Cancel 1st Work Order
		wo1 = frappe.get_doc("Work Order", wo_list[0])
		wo1.cancel()
		pln.reload()
		self.assertEqual(pln.po_products[0].ordered_qty, 2)

		# Cancel 2nd Work Order
		wo2 = frappe.get_doc("Work Order", wo_list[1])
		wo2.cancel()
		pln.reload()
		self.assertEqual(pln.po_products[0].ordered_qty, 0)

	def test_production_plan_pending_qty_with_sales_order(self):
		"""
		Test Prod Plan impact via: SO -> Prod Plan -> WO -> SE -> SE (cancel)
		"""
		from erpnext.manufacturing.doctype.work_order.test_work_order import make_wo_order_test_record

		make_stock_entry(product_code="_Test Product", target="Work In Progress - _TC", qty=2, basic_rate=100)
		make_stock_entry(
			product_code="_Test Product Home Desktop 100", target="Work In Progress - _TC", qty=4, basic_rate=100
		)

		product = "_Test FG Product"

		make_stock_entry(product_code=product, target="_Test Warehouse - _TC", qty=1)

		so = make_sales_order(product_code=product, qty=2)

		dn = make_delivery_note(so.name)
		dn.products[0].qty = 1
		dn.save()
		dn.submit()

		pln = create_production_plan(
			company=so.company, get_products_from="Sales Order", sales_order=so, skip_getting_mr_products=True
		)
		self.assertEqual(pln.po_products[0].pending_qty, 1)

		wo = make_wo_order_test_record(
			product_code=product,
			qty=1,
			company=so.company,
			wip_warehouse="Work In Progress - _TC",
			fg_warehouse="Finished Goods - _TC",
			skip_transfer=1,
			use_multi_level_bom=1,
			do_not_submit=True,
		)
		wo.production_plan = pln.name
		wo.production_plan_product = pln.po_products[0].name
		wo.submit()

		se = frappe.get_doc(make_se_from_wo(wo.name, "Manufacture", 1))
		se.submit()

		pln.reload()
		self.assertEqual(pln.po_products[0].pending_qty, 0)

		se.cancel()
		pln.reload()
		self.assertEqual(pln.po_products[0].pending_qty, 1)

	def test_production_plan_pending_qty_independent_products(self):
		"Test Prod Plan impact if products are added independently (no from SO or MR)."
		from erpnext.manufacturing.doctype.work_order.test_work_order import make_wo_order_test_record

		make_stock_entry(
			product_code="Raw Material Product 1", target="Work In Progress - _TC", qty=2, basic_rate=100
		)
		make_stock_entry(
			product_code="Raw Material Product 2", target="Work In Progress - _TC", qty=2, basic_rate=100
		)

		pln = create_production_plan(product_code="Test Production Product 1", skip_getting_mr_products=True)
		self.assertEqual(pln.po_products[0].pending_qty, 1)

		wo = make_wo_order_test_record(
			product_code="Test Production Product 1",
			qty=1,
			company=pln.company,
			wip_warehouse="Work In Progress - _TC",
			fg_warehouse="Finished Goods - _TC",
			skip_transfer=1,
			use_multi_level_bom=1,
			do_not_submit=True,
		)
		wo.production_plan = pln.name
		wo.production_plan_product = pln.po_products[0].name
		wo.submit()

		se = frappe.get_doc(make_se_from_wo(wo.name, "Manufacture", 1))
		se.submit()

		pln.reload()
		self.assertEqual(pln.po_products[0].pending_qty, 0)

		se.cancel()
		pln.reload()
		self.assertEqual(pln.po_products[0].pending_qty, 1)

	def test_qty_based_status(self):
		pp = frappe.new_doc("Production Plan")
		pp.po_products = [frappe._dict(planned_qty=5, produce_qty=4)]
		self.assertFalse(pp.all_products_completed())

		pp.po_products = [
			frappe._dict(planned_qty=5, produce_qty=10),
			frappe._dict(planned_qty=5, produce_qty=4),
		]
		self.assertFalse(pp.all_products_completed())

	def test_production_plan_planned_qty(self):
		# Case 1: When Planned Qty is non-integer and UOM is integer.
		from erpnext.utilities.transaction_base import UOMMustBeIntegerError

		self.assertRaises(
			UOMMustBeIntegerError, create_production_plan, product_code="_Test FG Product", planned_qty=0.55
		)

		# Case 2: When Planned Qty is non-integer and UOM is also non-integer.
		from erpnext.stock.doctype.product.test_product import make_product

		fg_product = make_product(properties={"is_stock_product": 1, "stock_uom": "_Test UOM 1"}).name
		bom_product = make_product().name

		make_bom(product=fg_product, raw_materials=[bom_product], source_warehouse="_Test Warehouse - _TC")

		pln = create_production_plan(product_code=fg_product, planned_qty=0.55, stock_uom="_Test UOM 1")
		self.assertEqual(pln.po_products[0].planned_qty, 0.55)

	def test_temporary_name_relinking(self):

		pp = frappe.new_doc("Production Plan")

		# this can not be unittested so mocking data that would be expected
		# from client side.
		for _ in range(10):
			po_product = pp.append(
				"po_products",
				{
					"name": frappe.generate_hash(length=10),
					"temporary_name": frappe.generate_hash(length=10),
				},
			)
			pp.append("sub_assembly_products", {"production_plan_product": po_product.temporary_name})
		pp._rename_temporary_references()

		for po_product, subassy_product in zip(pp.po_products, pp.sub_assembly_products):
			self.assertEqual(po_product.name, subassy_product.production_plan_product)

		# bad links should be erased
		pp.append("sub_assembly_products", {"production_plan_product": frappe.generate_hash(length=16)})
		pp._rename_temporary_references()
		self.assertIsNone(pp.sub_assembly_products[-1].production_plan_product)
		pp.sub_assembly_products.pop()

		# reattempting on same doc shouldn't change anything
		pp._rename_temporary_references()
		for po_product, subassy_product in zip(pp.po_products, pp.sub_assembly_products):
			self.assertEqual(po_product.name, subassy_product.production_plan_product)

	def test_produced_qty_for_multi_level_bom_product(self):
		# Create Products and BOMs
		rm_product = make_product(properties={"is_stock_product": 1}).name
		sub_assembly_product = make_product(properties={"is_stock_product": 1}).name
		fg_product = make_product(properties={"is_stock_product": 1}).name

		make_stock_entry(
			product_code=rm_product,
			qty=60,
			to_warehouse="Work In Progress - _TC",
			rate=99,
			purpose="Material Receipt",
		)

		make_bom(product=sub_assembly_product, raw_materials=[rm_product], rm_qty=3)
		make_bom(product=fg_product, raw_materials=[sub_assembly_product], rm_qty=4)

		# Step - 1: Create Production Plan
		pln = create_production_plan(product_code=fg_product, planned_qty=5, skip_getting_mr_products=1)
		pln.get_sub_assembly_products()

		# Step - 2: Create Work Orders
		pln.make_work_order()
		work_orders = frappe.get_all("Work Order", filters={"production_plan": pln.name}, pluck="name")
		sa_wo = fg_wo = None
		for work_order in work_orders:
			wo_doc = frappe.get_doc("Work Order", work_order)
			if wo_doc.production_plan_product:
				wo_doc.update(
					{"wip_warehouse": "Work In Progress - _TC", "fg_warehouse": "Finished Goods - _TC"}
				)
				fg_wo = wo_doc.name
			else:
				wo_doc.update(
					{"wip_warehouse": "Work In Progress - _TC", "fg_warehouse": "Work In Progress - _TC"}
				)
				sa_wo = wo_doc.name
			wo_doc.submit()

		# Step - 3: Complete Work Orders
		se = frappe.get_doc(make_se_from_wo(sa_wo, "Manufacture"))
		se.submit()

		se = frappe.get_doc(make_se_from_wo(fg_wo, "Manufacture"))
		se.submit()

		# Step - 4: Check Production Plan Product Produced Qty
		pln.load_from_db()
		self.assertEqual(pln.status, "Completed")
		self.assertEqual(pln.po_products[0].produced_qty, 5)

	def test_material_request_product_for_purchase_uom(self):
		from erpnext.stock.doctype.product.test_product import make_product

		fg_product = make_product(properties={"is_stock_product": 1, "stock_uom": "_Test UOM 1"}).name
		bom_product = make_product(
			properties={"is_stock_product": 1, "stock_uom": "_Test UOM 1", "purchase_uom": "Nos"}
		).name

		if not frappe.db.exists("UOM Conversion Detail", {"parent": bom_product, "uom": "Nos"}):
			doc = frappe.get_doc("Product", bom_product)
			doc.append("uoms", {"uom": "Nos", "conversion_factor": 10})
			doc.save()

		make_bom(product=fg_product, raw_materials=[bom_product], source_warehouse="_Test Warehouse - _TC")

		pln = create_production_plan(
			product_code=fg_product, planned_qty=10, ignore_existing_ordered_qty=1, stock_uom="_Test UOM 1"
		)

		pln.make_material_request()

		for row in pln.mr_products:
			self.assertEqual(row.uom, "Nos")
			self.assertEqual(row.quantity, 1)

		for row in frappe.get_all(
			"Material Request Product",
			filters={"production_plan": pln.name},
			fields=["product_code", "uom", "qty"],
		):
			self.assertEqual(row.product_code, bom_product)
			self.assertEqual(row.uom, "Nos")
			self.assertEqual(row.qty, 1)

	def test_material_request_for_sub_assembly_products(self):
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		bom_tree = {
			"Fininshed Goods1 For MR": {
				"SubAssembly1 For MR": {"SubAssembly1-1 For MR": {"ChildPart1 For MR": {}}}
			}
		}

		parent_bom = create_nested_bom(bom_tree, prefix="")
		plan = create_production_plan(
			product_code=parent_bom.product, planned_qty=10, ignore_existing_ordered_qty=1, do_not_submit=1
		)

		plan.get_sub_assembly_products()

		mr_products = []
		for row in plan.sub_assembly_products:
			mr_products.append(row.production_product)
			row.type_of_manufacturing = "Material Request"

		plan.save()
		products = get_products_for_material_requests(plan.as_dict())

		validate_mr_products = [d.get("product_code") for d in products]
		for product_code in mr_products:
			self.assertTrue(product_code in validate_mr_products)

	def test_resered_qty_for_production_plan_for_material_requests(self):
		from erpnext.stock.utils import get_or_make_bin

		bin_name = get_or_make_bin("Raw Material Product 1", "_Test Warehouse - _TC")
		before_qty = flt(frappe.db.get_value("Bin", bin_name, "reserved_qty_for_production_plan"))

		pln = create_production_plan(product_code="Test Production Product 1")

		bin_name = get_or_make_bin("Raw Material Product 1", "_Test Warehouse - _TC")
		after_qty = flt(frappe.db.get_value("Bin", bin_name, "reserved_qty_for_production_plan"))

		self.assertEqual(after_qty - before_qty, 1)

		pln = frappe.get_doc("Production Plan", pln.name)
		pln.cancel()

		bin_name = get_or_make_bin("Raw Material Product 1", "_Test Warehouse - _TC")
		after_qty = flt(frappe.db.get_value("Bin", bin_name, "reserved_qty_for_production_plan"))

		self.assertEqual(after_qty, before_qty)

	def test_skip_available_qty_for_sub_assembly_products(self):
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		bom_tree = {
			"Fininshed Goods1 For SUB Test": {
				"SubAssembly1 For SUB Test": {"ChildPart1 For SUB Test": {}},
				"SubAssembly2 For SUB Test": {},
			}
		}

		parent_bom = create_nested_bom(bom_tree, prefix="")
		plan = create_production_plan(
			product_code=parent_bom.product,
			planned_qty=10,
			ignore_existing_ordered_qty=1,
			do_not_submit=1,
			skip_available_sub_assembly_product=1,
			warehouse="_Test Warehouse - _TC",
		)

		make_stock_entry(
			product_code="SubAssembly1 For SUB Test",
			qty=5,
			rate=100,
			target="_Test Warehouse - _TC",
		)

		self.assertTrue(plan.skip_available_sub_assembly_product)

		plan.get_sub_assembly_products()

		for row in plan.sub_assembly_products:
			if row.production_product == "SubAssembly1 For SUB Test":
				self.assertEqual(row.qty, 5)

		mr_products = get_products_for_material_requests(plan.as_dict())
		for row in mr_products:
			row = frappe._dict(row)
			if row.product_code == "ChildPart1 For SUB Test":
				self.assertEqual(row.quantity, 5)

			if row.product_code == "SubAssembly2 For SUB Test":
				self.assertEqual(row.quantity, 10)


def create_production_plan(**args):
	"""
	sales_order (obj): Sales Order Doc Object
	get_products_from (str): Sales Order/Material Request
	skip_getting_mr_products (bool): Whether or not to plan for new MRs
	"""
	args = frappe._dict(args)

	pln = frappe.get_doc(
		{
			"doctype": "Production Plan",
			"company": args.company or "_Test Company",
			"customer": args.customer or "_Test Customer",
			"posting_date": nowdate(),
			"include_non_stock_products": args.include_non_stock_products or 0,
			"include_subcontracted_products": args.include_subcontracted_products or 0,
			"ignore_existing_ordered_qty": args.ignore_existing_ordered_qty or 0,
			"get_products_from": "Sales Order",
			"skip_available_sub_assembly_product": args.skip_available_sub_assembly_product or 0,
		}
	)

	if not args.get("sales_order"):
		pln.append(
			"po_products",
			{
				"use_multi_level_bom": args.use_multi_level_bom or 1,
				"product_code": args.product_code,
				"bom_no": frappe.db.get_value("Product", args.product_code, "default_bom"),
				"planned_qty": args.planned_qty or 1,
				"planned_start_date": args.planned_start_date or now_datetime(),
				"stock_uom": args.stock_uom or "Nos",
				"warehouse": args.warehouse,
			},
		)

	if args.get("get_products_from") == "Sales Order" and args.get("sales_order"):
		so = args.get("sales_order")
		pln.append(
			"sales_orders",
			{
				"sales_order": so.name,
				"sales_order_date": so.transaction_date,
				"customer": so.customer,
				"grand_total": so.grand_total,
			},
		)
		pln.get_products()

	if not args.get("skip_getting_mr_products"):
		mr_products = get_products_for_material_requests(pln.as_dict())
		for d in mr_products:
			pln.append("mr_products", d)

	if not args.do_not_save:
		pln.insert()
		if not args.do_not_submit:
			pln.submit()

	return pln


def make_bom(**args):
	args = frappe._dict(args)

	bom = frappe.get_doc(
		{
			"doctype": "BOM",
			"is_default": 1,
			"product": args.product,
			"currency": args.currency or "USD",
			"quantity": args.quantity or 1,
			"company": args.company or "_Test Company",
			"routing": args.routing,
			"with_operations": args.with_operations or 0,
		}
	)

	for product in args.raw_materials:
		product_doc = frappe.get_doc("Product", product)
		bom.append(
			"products",
			{
				"product_code": product,
				"qty": args.rm_qty or 1.0,
				"uom": product_doc.stock_uom,
				"stock_uom": product_doc.stock_uom,
				"rate": product_doc.valuation_rate or args.rate,
				"source_warehouse": args.source_warehouse,
			},
		)

	if not args.do_not_save:
		bom.insert(ignore_permissions=True)

		if not args.do_not_submit:
			bom.submit()

	return bom
