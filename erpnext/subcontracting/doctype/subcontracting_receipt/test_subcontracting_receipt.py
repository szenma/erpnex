# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt


import copy

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, cint, cstr, flt, today

import erpnext
from erpnext.accounts.doctype.account.test_account import get_inventory_account
from erpnext.controllers.sales_and_purchase_return import make_return_doc
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
from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import get_gl_entries
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry
from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import (
	create_stock_reconciliation,
)
from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
	make_subcontracting_receipt,
)


class TestSubcontractingReceipt(FrappeTestCase):
	def setUp(self):
		make_subcontracted_products()
		make_raw_materials()
		make_service_products()
		make_bom_for_subcontracted_products()

	def test_subcontracting(self):
		set_backflush_based_on("BOM")
		make_stock_entry(
			product_code="_Test Product", qty=100, target="_Test Warehouse 1 - _TC", basic_rate=100
		)
		make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			qty=100,
			target="_Test Warehouse 1 - _TC",
			basic_rate=100,
		)
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
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		scr = make_subcontracting_receipt(sco.name)
		scr.save()
		scr.submit()
		rm_supp_cost = sum(product.amount for product in scr.get("supplied_products"))
		self.assertEqual(scr.get("products")[0].rm_supp_cost, flt(rm_supp_cost))

	def test_available_qty_for_consumption(self):
		make_stock_entry(
			product_code="_Test Product", qty=100, target="_Test Warehouse 1 - _TC", basic_rate=100
		)
		make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			qty=100,
			target="_Test Warehouse 1 - _TC",
			basic_rate=100,
		)
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
		rm_products = [
			{
				"main_product_code": "_Test FG Product",
				"product_code": "_Test Product",
				"qty": 5.0,
				"rate": 100.0,
				"stock_uom": "_Test UOM",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "_Test FG Product",
				"product_code": "_Test Product Home Desktop 100",
				"qty": 10.0,
				"rate": 100.0,
				"stock_uom": "_Test UOM",
				"warehouse": "_Test Warehouse - _TC",
			},
		]
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		scr = make_subcontracting_receipt(sco.name)
		scr.save()
		self.assertRaises(frappe.ValidationError, scr.submit)

	def test_subcontracting_gle_fg_product_rate_zero(self):
		from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import get_gl_entries

		set_backflush_based_on("BOM")
		make_stock_entry(
			product_code="_Test Product",
			target="Work In Progress - TCP1",
			qty=100,
			basic_rate=100,
			company="_Test Company with perpetual inventory",
		)
		make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			target="Work In Progress - TCP1",
			qty=100,
			basic_rate=100,
			company="_Test Company with perpetual inventory",
		)
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 10,
				"rate": 0,
				"fg_product": "_Test FG Product",
				"fg_product_qty": 10,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		scr = make_subcontracting_receipt(sco.name)
		scr.save()
		scr.submit()

		gl_entries = get_gl_entries("Subcontracting Receipt", scr.name)
		self.assertFalse(gl_entries)

	def test_subcontracting_over_receipt(self):
		"""
		Behaviour: Raise multiple SCRs against one SCO that in total
		        receive more than the required qty in the SCO.
		Expected Result: Error Raised for Over Receipt against SCO.
		"""
		from erpnext.controllers.subcontracting_controller import (
			make_rm_stock_entry as make_subcontract_transfer_entry,
		)
		from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
			make_subcontracting_receipt,
		)
		from erpnext.subcontracting.doctype.subcontracting_order.test_subcontracting_order import (
			make_subcontracted_product,
		)

		set_backflush_based_on("Material Transferred for Subcontract")
		product_code = "_Test Subcontracted FG Product 1"
		make_subcontracted_product(product_code=product_code)
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 1,
				"rate": 100,
				"fg_product": "_Test Subcontracted FG Product 1",
				"fg_product_qty": 1,
			},
		]
		sco = get_subcontracting_order(
			service_products=service_products,
			include_exploded_products=0,
		)

		# stock raw materials in a warehouse before transfer
		make_stock_entry(
			target="_Test Warehouse - _TC", product_code="Test Extra Product 1", qty=10, basic_rate=100
		)
		make_stock_entry(
			target="_Test Warehouse - _TC", product_code="_Test FG Product", qty=1, basic_rate=100
		)
		make_stock_entry(
			target="_Test Warehouse - _TC", product_code="Test Extra Product 2", qty=1, basic_rate=100
		)

		rm_products = [
			{
				"product_code": product_code,
				"rm_product_code": sco.supplied_products[0].rm_product_code,
				"product_name": "_Test FG Product",
				"qty": sco.supplied_products[0].required_qty,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
			},
			{
				"product_code": product_code,
				"rm_product_code": sco.supplied_products[1].rm_product_code,
				"product_name": "Test Extra Product 1",
				"qty": sco.supplied_products[1].required_qty,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
			},
		]
		ste = frappe.get_doc(make_subcontract_transfer_entry(sco.name, rm_products))
		ste.to_warehouse = "_Test Warehouse 1 - _TC"
		ste.save()
		ste.submit()

		scr1 = make_subcontracting_receipt(sco.name)
		scr2 = make_subcontracting_receipt(sco.name)

		scr1.submit()
		self.assertRaises(frappe.ValidationError, scr2.submit)

	def test_subcontracted_scr_for_multi_transfer_batches(self):
		from erpnext.controllers.subcontracting_controller import make_rm_stock_entry
		from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
			make_subcontracting_receipt,
		)

		set_backflush_based_on("Material Transferred for Subcontract")
		product_code = "_Test Subcontracted FG Product 3"

		make_product(
			"Sub Contracted Raw Material 3",
			{"is_stock_product": 1, "is_sub_contracted_product": 1, "has_batch_no": 1, "create_new_batch": 1},
		)

		make_subcontracted_product(
			product_code=product_code, has_batch_no=1, raw_materials=["Sub Contracted Raw Material 3"]
		)

		order_qty = 500
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 3",
				"qty": order_qty,
				"rate": 100,
				"fg_product": "_Test Subcontracted FG Product 3",
				"fg_product_qty": order_qty,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)

		ste1 = make_stock_entry(
			target="_Test Warehouse - _TC",
			product_code="Sub Contracted Raw Material 3",
			qty=300,
			basic_rate=100,
		)
		ste2 = make_stock_entry(
			target="_Test Warehouse - _TC",
			product_code="Sub Contracted Raw Material 3",
			qty=200,
			basic_rate=100,
		)

		transferred_batch = {ste1.products[0].batch_no: 300, ste2.products[0].batch_no: 200}

		rm_products = [
			{
				"product_code": product_code,
				"rm_product_code": "Sub Contracted Raw Material 3",
				"product_name": "_Test Product",
				"qty": 300,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
				"name": sco.supplied_products[0].name,
			},
			{
				"product_code": product_code,
				"rm_product_code": "Sub Contracted Raw Material 3",
				"product_name": "_Test Product",
				"qty": 200,
				"warehouse": "_Test Warehouse - _TC",
				"stock_uom": "Nos",
				"name": sco.supplied_products[0].name,
			},
		]

		se = frappe.get_doc(make_rm_stock_entry(sco.name, rm_products))
		self.assertEqual(len(se.products), 2)
		se.products[0].batch_no = ste1.products[0].batch_no
		se.products[1].batch_no = ste2.products[0].batch_no
		se.submit()

		supplied_qty = frappe.db.get_value(
			"Subcontracting Order Supplied Product",
			{"parent": sco.name, "rm_product_code": "Sub Contracted Raw Material 3"},
			"supplied_qty",
		)

		self.assertEqual(supplied_qty, 500.00)

		scr = make_subcontracting_receipt(sco.name)
		scr.save()
		self.assertEqual(len(scr.supplied_products), 2)

		for row in scr.supplied_products:
			self.assertEqual(transferred_batch.get(row.batch_no), row.consumed_qty)

	def test_subcontracting_receipt_partial_return(self):
		sco = get_subcontracting_order()
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		scr1 = make_subcontracting_receipt(sco.name)
		scr1.save()
		scr1.submit()

		scr1_return = make_return_subcontracting_receipt(scr_name=scr1.name, qty=-3)
		scr1.load_from_db()
		self.assertEqual(scr1_return.status, "Return")
		self.assertIsNotNone(scr1_return.products[0].bom)
		self.assertEqual(scr1.products[0].returned_qty, 3)

		scr2_return = make_return_subcontracting_receipt(scr_name=scr1.name, qty=-7)
		scr1.load_from_db()
		self.assertEqual(scr2_return.status, "Return")
		self.assertIsNotNone(scr2_return.products[0].bom)
		self.assertEqual(scr1.status, "Return Issued")
		self.assertEqual(scr1.products[0].returned_qty, 10)

	def test_subcontracting_receipt_over_return(self):
		sco = get_subcontracting_order()
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		scr1 = make_subcontracting_receipt(sco.name)
		scr1.save()
		scr1.submit()

		from erpnext.controllers.status_updater import OverAllowanceError

		args = frappe._dict(scr_name=scr1.name, qty=-15)
		self.assertRaises(OverAllowanceError, make_return_subcontracting_receipt, **args)

	def test_subcontracting_receipt_no_gl_entry(self):
		sco = get_subcontracting_order()
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr = make_subcontracting_receipt(sco.name)
		scr.append(
			"additional_costs",
			{
				"expense_account": "Expenses Included In Valuation - _TC",
				"description": "Test Additional Costs",
				"amount": 100,
			},
		)
		scr.save()
		scr.submit()

		stock_value_difference = frappe.db.get_value(
			"Stock Ledger Entry",
			{
				"voucher_type": "Subcontracting Receipt",
				"voucher_no": scr.name,
				"product_code": "Subcontracted Product SA7",
				"warehouse": "_Test Warehouse - _TC",
			},
			"stock_value_difference",
		)

		# Service Cost(100 * 10) + Raw Materials Cost(100 * 10) + Additional Costs(10 * 10) = 2100
		self.assertEqual(stock_value_difference, 2100)
		self.assertFalse(get_gl_entries("Subcontracting Receipt", scr.name))

	def test_subcontracting_receipt_gl_entry(self):
		sco = get_subcontracting_order(
			company="_Test Company with perpetual inventory",
			warehouse="Stores - TCP1",
			supplier_warehouse="Work In Progress - TCP1",
		)
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr = make_subcontracting_receipt(sco.name)
		additional_costs_expense_account = "Expenses Included In Valuation - TCP1"
		scr.append(
			"additional_costs",
			{
				"expense_account": additional_costs_expense_account,
				"description": "Test Additional Costs",
				"amount": 100,
				"base_amount": 100,
			},
		)
		scr.save()
		scr.submit()

		self.assertEqual(cint(erpnext.is_perpetual_inventory_enabled(scr.company)), 1)

		gl_entries = get_gl_entries("Subcontracting Receipt", scr.name)

		self.assertTrue(gl_entries)

		fg_warehouse_ac = get_inventory_account(scr.company, scr.products[0].warehouse)
		supplier_warehouse_ac = get_inventory_account(scr.company, scr.supplier_warehouse)
		expense_account = scr.products[0].expense_account

		if fg_warehouse_ac == supplier_warehouse_ac:
			expected_values = {
				fg_warehouse_ac: [2100.0, 1000.0],  # FG Amount (D), RM Cost (C)
				expense_account: [0.0, 1000.0],  # Service Cost (C)
				additional_costs_expense_account: [0.0, 100.0],  # Additional Cost (C)
			}
		else:
			expected_values = {
				fg_warehouse_ac: [2100.0, 0.0],  # FG Amount (D)
				supplier_warehouse_ac: [0.0, 1000.0],  # RM Cost (C)
				expense_account: [0.0, 1000.0],  # Service Cost (C)
				additional_costs_expense_account: [0.0, 100.0],  # Additional Cost (C)
			}

		for gle in gl_entries:
			self.assertEqual(expected_values[gle.account][0], gle.debit)
			self.assertEqual(expected_values[gle.account][1], gle.credit)

		scr.reload()
		scr.cancel()
		self.assertTrue(get_gl_entries("Subcontracting Receipt", scr.name))

	def test_supplied_products_consumed_qty(self):
		# Set Backflush Based On as "Material Transferred for Subcontracting" to transfer RM's more than the required qty
		set_backflush_based_on("Material Transferred for Subcontract")

		# Create Material Receipt for RM's
		make_stock_entry(
			product_code="_Test Product", qty=100, target="_Test Warehouse 1 - _TC", basic_rate=100
		)
		make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			qty=100,
			target="_Test Warehouse 1 - _TC",
			basic_rate=100,
		)

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

		# Create Subcontracting Order
		sco = get_subcontracting_order(service_products=service_products)

		# Transfer RM's
		rm_products = get_rm_products(sco.supplied_products)
		rm_products[0]["qty"] = 20  # Extra 10 Qty
		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		# Create Subcontracting Receipt
		scr = make_subcontracting_receipt(sco.name)
		scr.rejected_warehouse = "_Test Warehouse 1 - _TC"

		scr.products[0].qty = 5  # Accepted Qty
		scr.products[0].rejected_qty = 3
		scr.save()

		# consumed_qty should be (accepted_qty * (transfered_qty / qty)) = (5 * (20 / 10)) = 10
		self.assertEqual(scr.supplied_products[0].consumed_qty, 10)

		# Set Backflush Based On as "BOM"
		set_backflush_based_on("BOM")

		scr.products[0].qty = 6  # Accepted Qty
		scr.products[0].rejected_qty = 4
		scr.save()

		# consumed_qty should be (accepted_qty * qty_consumed_per_unit) = (6 * 1) = 6
		self.assertEqual(scr.supplied_products[0].consumed_qty, 6)

	def test_supplied_products_cost_after_reposting(self):
		# Set Backflush Based On as "BOM"
		set_backflush_based_on("BOM")

		# Create Material Receipt for RM's
		make_stock_entry(
			product_code="_Test Product",
			qty=100,
			target="_Test Warehouse 1 - _TC",
			basic_rate=100,
			posting_date=add_days(today(), -2),
		)
		make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			qty=100,
			target="_Test Warehouse 1 - _TC",
			basic_rate=100,
		)

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

		# Create Subcontracting Order
		sco = get_subcontracting_order(service_products=service_products)

		# Transfer RM's
		rm_products = get_rm_products(sco.supplied_products)

		productwise_details = make_stock_in_entry(rm_products=rm_products)
		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		# Create Subcontracting Receipt
		scr = make_subcontracting_receipt(sco.name)
		scr.save()
		scr.submit()

		# Create Backdated Stock Reconciliation
		sr = create_stock_reconciliation(
			product_code=rm_products[0].get("product_code"),
			warehouse="_Test Warehouse 1 - _TC",
			qty=100,
			rate=50,
			posting_date=add_days(today(), -1),
		)

		# Cost should be updated in Subcontracting Receipt after reposting
		prev_cost = scr.supplied_products[0].rate
		scr.load_from_db()
		self.assertNotEqual(scr.supplied_products[0].rate, prev_cost)
		self.assertEqual(scr.supplied_products[0].rate, sr.products[0].valuation_rate)


def make_return_subcontracting_receipt(**args):
	args = frappe._dict(args)
	return_doc = make_return_doc("Subcontracting Receipt", args.scr_name)
	return_doc.supplier_warehouse = (
		args.supplier_warehouse or args.warehouse or "_Test Warehouse 1 - _TC"
	)

	if args.qty:
		for product in return_doc.products:
			product.qty = args.qty

	if not args.do_not_save:
		return_doc.save()
		if not args.do_not_submit:
			return_doc.submit()

	return_doc.load_from_db()
	return return_doc


def get_products(**args):
	args = frappe._dict(args)
	return [
		{
			"conversion_factor": 1.0,
			"description": "_Test Product",
			"doctype": "Subcontracting Receipt Product",
			"product_code": "_Test Product",
			"product_name": "_Test Product",
			"parentfield": "products",
			"qty": 5.0,
			"rate": 50.0,
			"received_qty": 5.0,
			"rejected_qty": 0.0,
			"stock_uom": "_Test UOM",
			"warehouse": args.warehouse or "_Test Warehouse - _TC",
			"cost_center": args.cost_center or "Main - _TC",
		},
		{
			"conversion_factor": 1.0,
			"description": "_Test Product Home Desktop 100",
			"doctype": "Subcontracting Receipt Product",
			"product_code": "_Test Product Home Desktop 100",
			"product_name": "_Test Product Home Desktop 100",
			"parentfield": "products",
			"qty": 5.0,
			"rate": 50.0,
			"received_qty": 5.0,
			"rejected_qty": 0.0,
			"stock_uom": "_Test UOM",
			"warehouse": args.warehouse or "_Test Warehouse 1 - _TC",
			"cost_center": args.cost_center or "Main - _TC",
		},
	]
