# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import copy
from collections import defaultdict

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.buying.doctype.purchase_order.purchase_order import get_mapped_subcontracting_order
from erpnext.controllers.subcontracting_controller import (
	get_materials_from_supplier,
	make_rm_stock_entry,
)
from erpnext.controllers.tests.test_subcontracting_controller import (
	get_rm_products,
	get_subcontracting_order,
	make_bom_for_subcontracted_products,
	make_raw_materials,
	make_service_products,
	make_stock_in_entry,
	make_stock_transfer_entry,
	make_subcontracted_product,
	make_subcontracted_products,
	set_backflush_based_on,
)
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry
from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
	make_subcontracting_receipt,
)


class TestSubcontractingOrder(FrappeTestCase):
	def setUp(self):
		make_subcontracted_products()
		make_raw_materials()
		make_service_products()
		make_bom_for_subcontracted_products()

	def test_populate_products_table(self):
		sco = get_subcontracting_order()
		sco.products = None
		sco.populate_products_table()
		self.assertEqual(len(sco.service_products), len(sco.products))

	def test_set_missing_values(self):
		sco = get_subcontracting_order()
		before = {sco.total_qty, sco.total, sco.total_additional_costs}
		sco.total_qty = sco.total = sco.total_additional_costs = 0
		sco.set_missing_values()
		after = {sco.total_qty, sco.total, sco.total_additional_costs}
		self.assertSetEqual(before, after)

	def test_update_status(self):
		# Draft
		sco = get_subcontracting_order(do_not_submit=1)
		self.assertEqual(sco.status, "Draft")

		# Open
		sco.submit()
		sco.load_from_db()
		self.assertEqual(sco.status, "Open")

		# Partial Material Transferred
		rm_products = get_rm_products(sco.supplied_products)
		rm_products[0]["qty"] -= 1
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		sco.load_from_db()
		self.assertEqual(sco.status, "Partial Material Transferred")

		# Material Transferred
		rm_products[0]["qty"] = 1
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		sco.load_from_db()
		self.assertEqual(sco.status, "Material Transferred")

		# Partially Received
		scr = make_subcontracting_receipt(sco.name)
		scr.products[0].qty -= 1
		scr.save()
		scr.submit()
		sco.load_from_db()
		self.assertEqual(sco.status, "Partially Received")

		# Closed
		ste = get_materials_from_supplier(sco.name, [d.name for d in sco.supplied_products])
		ste.save()
		ste.submit()
		sco.load_from_db()
		self.assertEqual(sco.status, "Closed")
		ste.cancel()
		sco.load_from_db()
		self.assertEqual(sco.status, "Partially Received")

		# Completed
		scr = make_subcontracting_receipt(sco.name)
		scr.save()
		scr.submit()
		sco.load_from_db()
		self.assertEqual(sco.status, "Completed")

		# Partially Received (scr cancelled)
		scr.load_from_db()
		scr.cancel()
		sco.load_from_db()
		self.assertEqual(sco.status, "Partially Received")

	def test_make_rm_stock_entry(self):
		sco = get_subcontracting_order()
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		ste = make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		self.assertEqual(len(ste.products), len(rm_products))

	def test_make_rm_stock_entry_for_serial_products(self):
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 2",
				"qty": 5,
				"rate": 100,
				"fg_product": "Subcontracted Product SA2",
				"fg_product_qty": 5,
			},
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 5",
				"qty": 6,
				"rate": 100,
				"fg_product": "Subcontracted Product SA5",
				"fg_product_qty": 6,
			},
		]

		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		ste = make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		self.assertEqual(len(ste.products), len(rm_products))

	def test_make_rm_stock_entry_for_batch_products(self):
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 4",
				"qty": 5,
				"rate": 100,
				"fg_product": "Subcontracted Product SA4",
				"fg_product_qty": 5,
			},
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 6",
				"qty": 6,
				"rate": 100,
				"fg_product": "Subcontracted Product SA6",
				"fg_product_qty": 6,
			},
		]

		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		ste = make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		self.assertEqual(len(ste.products), len(rm_products))

	def test_make_rm_stock_entry_for_batch_products_with_less_transfer(self):
		set_backflush_based_on("BOM")

		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 4",
				"qty": 5,
				"rate": 100,
				"fg_product": "Subcontracted Product SA4",
				"fg_product_qty": 5,
			}
		]

		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		productwise_transfer_qty = defaultdict(int)
		for product in rm_products:
			product["qty"] -= 1
			productwise_transfer_qty[product["product_code"]] += product["qty"]

		ste = make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr = make_subcontracting_receipt(sco.name)

		for row in scr.supplied_products:
			self.assertEqual(row.consumed_qty, productwise_transfer_qty.get(row.rm_product_code) + 1)

	def test_update_reserved_qty_for_subcontracting(self):
		# Create RM Material Receipt
		make_stock_entry(target="_Test Warehouse - _TC", product_code="_Test Product", qty=10, basic_rate=100)
		make_stock_entry(
			target="_Test Warehouse - _TC", product_code="_Test Product Home Desktop 100", qty=20, basic_rate=100
		)

		bin_before_sco = frappe.db.get_value(
			"Bin",
			filters={"warehouse": "_Test Warehouse - _TC", "product_code": "_Test Product"},
			fieldname=["reserved_qty_for_sub_contract", "projected_qty", "modified"],
			as_dict=1,
		)

		# Create SCO
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 10,
				"rate": 100,
				"fg_product": "_Test FG Product",
				"fg_product_qty": 10,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)

		bin_after_sco = frappe.db.get_value(
			"Bin",
			filters={"warehouse": "_Test Warehouse - _TC", "product_code": "_Test Product"},
			fieldname=["reserved_qty_for_sub_contract", "projected_qty", "modified"],
			as_dict=1,
		)

		# reserved_qty_for_sub_contract should be increased by 10
		self.assertEqual(
			bin_after_sco.reserved_qty_for_sub_contract, bin_before_sco.reserved_qty_for_sub_contract + 10
		)

		# projected_qty should be decreased by 10
		self.assertEqual(bin_after_sco.projected_qty, bin_before_sco.projected_qty - 10)

		self.assertNotEqual(bin_before_sco.modified, bin_after_sco.modified)

		# Create Stock Entry(Send to Subcontractor)
		rm_products = [
			{
				"product_code": "_Test FG Product",
				"rm_product_code": "_Test Product",
				"product_name": "_Test Product",
				"qty": 10,
				"warehouse": "_Test Warehouse - _TC",
				"rate": 100,
				"amount": 1000,
				"stock_uom": "Nos",
			},
			{
				"product_code": "_Test FG Product",
				"rm_product_code": "_Test Product Home Desktop 100",
				"product_name": "_Test Product Home Desktop 100",
				"qty": 20,
				"warehouse": "_Test Warehouse - _TC",
				"rate": 100,
				"amount": 2000,
				"stock_uom": "Nos",
			},
		]
		ste = frappe.get_doc(make_rm_stock_entry(sco.name, rm_products))
		ste.to_warehouse = "_Test Warehouse 1 - _TC"
		ste.save()
		ste.submit()

		bin_after_rm_transfer = frappe.db.get_value(
			"Bin",
			filters={"warehouse": "_Test Warehouse - _TC", "product_code": "_Test Product"},
			fieldname="reserved_qty_for_sub_contract",
			as_dict=1,
		)

		# reserved_qty_for_sub_contract should be decreased by 10
		self.assertEqual(
			bin_after_rm_transfer.reserved_qty_for_sub_contract,
			bin_after_sco.reserved_qty_for_sub_contract - 10,
		)

		# Cancel Stock Entry(Send to Subcontractor)
		ste.cancel()
		bin_after_cancel_ste = frappe.db.get_value(
			"Bin",
			filters={"warehouse": "_Test Warehouse - _TC", "product_code": "_Test Product"},
			fieldname="reserved_qty_for_sub_contract",
			as_dict=1,
		)

		# reserved_qty_for_sub_contract should be increased by 10
		self.assertEqual(
			bin_after_cancel_ste.reserved_qty_for_sub_contract,
			bin_after_rm_transfer.reserved_qty_for_sub_contract + 10,
		)

		# Cancel SCO
		sco.reload()
		sco.cancel()
		bin_after_cancel_sco = frappe.db.get_value(
			"Bin",
			filters={"warehouse": "_Test Warehouse - _TC", "product_code": "_Test Product"},
			fieldname="reserved_qty_for_sub_contract",
			as_dict=1,
		)

		# reserved_qty_for_sub_contract should be decreased by 10
		self.assertEqual(
			bin_after_cancel_sco.reserved_qty_for_sub_contract,
			bin_after_cancel_ste.reserved_qty_for_sub_contract - 10,
		)
		self.assertEqual(
			bin_after_cancel_sco.reserved_qty_for_sub_contract, bin_before_sco.reserved_qty_for_sub_contract
		)

	def test_exploded_products(self):
		product_code = "_Test Subcontracted FG Product 11"
		make_subcontracted_product(product_code=product_code)

		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 1,
				"rate": 100,
				"fg_product": product_code,
				"fg_product_qty": 1,
			},
		]

		sco1 = get_subcontracting_order(service_products=service_products, include_exploded_products=1)
		product_name = frappe.db.get_value("BOM", {"product": product_code}, "name")
		bom = frappe.get_doc("BOM", product_name)
		exploded_products = sorted([product.product_code for product in bom.exploded_products])
		supplied_products = sorted([product.rm_product_code for product in sco1.supplied_products])
		self.assertEqual(exploded_products, supplied_products)

		sco2 = get_subcontracting_order(service_products=service_products, include_exploded_products=0)
		supplied_products1 = sorted([product.rm_product_code for product in sco2.supplied_products])
		bom_products = sorted([product.product_code for product in bom.products])
		self.assertEqual(supplied_products1, bom_products)

	def test_backflush_based_on_stock_entry(self):
		product_code = "_Test Subcontracted FG Product 1"
		make_subcontracted_product(product_code=product_code)
		make_product("Sub Contracted Raw Material 1", {"is_stock_product": 1, "is_sub_contracted_product": 1})

		set_backflush_based_on("Material Transferred for Subcontract")

		order_qty = 5
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": order_qty,
				"rate": 100,
				"fg_product": product_code,
				"fg_product_qty": order_qty,
			},
		]

		sco = get_subcontracting_order(service_products=service_products)

		make_stock_entry(
			target="_Test Warehouse - _TC", product_code="_Test Product Home Desktop 100", qty=20, basic_rate=100
		)
		make_stock_entry(
			target="_Test Warehouse - _TC", product_code="Test Extra Product 1", qty=100, basic_rate=100
		)
		make_stock_entry(
			target="_Test Warehouse - _TC", product_code="Test Extra Product 2", qty=10, basic_rate=100
		)
		make_stock_entry(
			target="_Test Warehouse - _TC",
			product_code="Sub Contracted Raw Material 1",
			qty=10,
			basic_rate=100,
		)

		rm_products = [
			{
				"product_code": product_code,
				"rm_product_code": "Sub Contracted Raw Material 1",
				"product_name": "_Test Product",
				"qty": 10,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
			},
			{
				"product_code": product_code,
				"rm_product_code": "_Test Product Home Desktop 100",
				"product_name": "_Test Product Home Desktop 100",
				"qty": 20,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
			},
			{
				"product_code": product_code,
				"rm_product_code": "Test Extra Product 1",
				"product_name": "Test Extra Product 1",
				"qty": 10,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
			},
			{
				"product_code": product_code,
				"rm_product_code": "Test Extra Product 2",
				"stock_uom": "Nos",
				"qty": 10,
				"warehouse": "_Test Warehouse - _TC",
				"product_name": "Test Extra Product 2",
			},
		]

		ste = frappe.get_doc(make_rm_stock_entry(sco.name, rm_products))
		ste.submit()

		scr = make_subcontracting_receipt(sco.name)
		received_qty = 2

		# partial receipt
		scr.get("products")[0].qty = received_qty
		scr.save()
		scr.submit()

		transferred_products = sorted(
			[product.product_code for product in ste.get("products") if ste.subcontracting_order == sco.name]
		)
		issued_products = sorted([product.rm_product_code for product in scr.get("supplied_products")])

		self.assertEqual(transferred_products, issued_products)
		self.assertEqual(scr.get_supplied_products_cost(scr.get("products")[0].name), 2000)

		transferred_rm_map = frappe._dict()
		for product in rm_products:
			transferred_rm_map[product.get("rm_product_code")] = product

		set_backflush_based_on("BOM")

	def test_supplied_qty(self):
		product_code = "_Test Subcontracted FG Product 5"
		make_product("Sub Contracted Raw Material 4", {"is_stock_product": 1, "is_sub_contracted_product": 1})

		make_subcontracted_product(product_code=product_code, raw_materials=["Sub Contracted Raw Material 4"])

		set_backflush_based_on("Material Transferred for Subcontract")

		order_qty = 250
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": order_qty,
				"rate": 100,
				"fg_product": product_code,
				"fg_product_qty": order_qty,
			},
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": order_qty,
				"rate": 100,
				"fg_product": product_code,
				"fg_product_qty": order_qty,
			},
		]

		sco = get_subcontracting_order(service_products=service_products)

		# Material receipt entry for the raw materials which will be send to supplier
		make_stock_entry(
			target="_Test Warehouse - _TC",
			product_code="Sub Contracted Raw Material 4",
			qty=500,
			basic_rate=100,
		)

		rm_products = [
			{
				"product_code": product_code,
				"rm_product_code": "Sub Contracted Raw Material 4",
				"product_name": "_Test Product",
				"qty": 250,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
				"name": sco.supplied_products[0].name,
			},
			{
				"product_code": product_code,
				"rm_product_code": "Sub Contracted Raw Material 4",
				"product_name": "_Test Product",
				"qty": 250,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
			},
		]

		# Raw Materials transfer entry from stores to supplier's warehouse
		ste = frappe.get_doc(make_rm_stock_entry(sco.name, rm_products))
		ste.submit()

		# Test sco_rm_detail field has value or not
		for product_row in ste.products:
			self.assertEqual(product_row.sco_rm_detail, sco.supplied_products[product_row.idx - 1].name)

		sco.load_from_db()
		for row in sco.supplied_products:
			# Valid that whether transferred quantity is matching with supplied qty or not in the subcontracting order
			self.assertEqual(row.supplied_qty, 250.0)

		set_backflush_based_on("BOM")

	def test_get_materials_from_supplier(self):
		# Create SCO
		sco = get_subcontracting_order()

		# Transfer RM
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		# Create SCR (Partial)
		scr = make_subcontracting_receipt(sco.name)
		scr.products[0].qty -= 5
		scr.save()
		scr.submit()

		# Get RM from Supplier
		ste = get_materials_from_supplier(sco.name, [d.name for d in sco.supplied_products])
		ste.save()
		ste.submit()

		sco.load_from_db()

		self.assertEqual(sco.status, "Closed")
		self.assertEqual(sco.supplied_products[0].returned_qty, 5)


def create_subcontracting_order(**args):
	args = frappe._dict(args)
	sco = get_mapped_subcontracting_order(source_name=args.po_name)

	for product in sco.products:
		product.include_exploded_products = args.get("include_exploded_products", 1)

	if args.warehouse:
		for product in sco.products:
			product.warehouse = args.warehouse
	else:
		warehouse = frappe.get_value("Purchase Order", args.po_name, "set_warehouse")
		if warehouse:
			for product in sco.products:
				product.warehouse = warehouse
		else:
			po = frappe.get_doc("Purchase Order", args.po_name)
			warehouses = []
			for product in po.products:
				warehouses.append(product.warehouse)
			else:
				for idx, val in enumerate(sco.products):
					val.warehouse = warehouses[idx]

	if not args.do_not_save:
		sco.insert()
		if not args.do_not_submit:
			sco.submit()

	return sco
