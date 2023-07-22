# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import copy
from collections import defaultdict

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint

from erpnext.buying.doctype.purchase_order.test_purchase_order import create_purchase_order
from erpnext.controllers.subcontracting_controller import (
	get_materials_from_supplier,
	make_rm_stock_entry,
)
from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry
from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
	make_subcontracting_receipt,
)


class TestSubcontractingController(FrappeTestCase):
	def setUp(self):
		make_subcontracted_products()
		make_raw_materials()
		make_service_products()
		make_bom_for_subcontracted_products()

	def test_remove_empty_rows(self):
		sco = get_subcontracting_order()
		len_before = len(sco.service_products)
		sco.service_products[0].product_code = None
		sco.remove_empty_rows()
		self.assertEqual((len_before - 1), len(sco.service_products))

	def test_calculate_additional_costs(self):
		sco = get_subcontracting_order(do_not_submit=1)

		rate_without_additional_cost = sco.products[0].rate
		amount_without_additional_cost = sco.products[0].amount

		additional_amount = 120
		sco.append(
			"additional_costs",
			{
				"expense_account": "Cost of Goods Sold - _TC",
				"description": "Test",
				"amount": additional_amount,
			},
		)
		sco.save()

		additional_cost_per_qty = additional_amount / sco.products[0].qty

		self.assertEqual(sco.products[0].additional_cost_per_qty, additional_cost_per_qty)
		self.assertEqual(rate_without_additional_cost + additional_cost_per_qty, sco.products[0].rate)
		self.assertEqual(amount_without_additional_cost + additional_amount, sco.products[0].amount)

		sco.additional_costs = []
		sco.save()

		self.assertEqual(sco.products[0].additional_cost_per_qty, 0)
		self.assertEqual(rate_without_additional_cost, sco.products[0].rate)
		self.assertEqual(amount_without_additional_cost, sco.products[0].amount)

	def test_create_raw_materials_supplied(self):
		sco = get_subcontracting_order()
		sco.supplied_products = None
		sco.create_raw_materials_supplied()
		self.assertIsNotNone(sco.supplied_products)

	def test_sco_with_bom(self):
		"""
		- Set backflush based on BOM.
		- Create SCO for the product Subcontracted Product SA1 and add same product two times.
		- Transfer the components from Stores to Supplier warehouse with batch no and serial nos.
		- Create SCR against the SCO and check serial nos and batch no.
		"""

		set_backflush_based_on("BOM")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 5,
				"rate": 100,
				"fg_product": "Subcontracted Product SA1",
				"fg_product_qty": 5,
			},
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 6,
				"rate": 100,
				"fg_product": "Subcontracted Product SA1",
				"fg_product_qty": 6,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name if product.get("qty") == 5 else sco.products[1].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)
		scr = make_subcontracting_receipt(sco.name)
		scr.save()
		scr.submit()

		for key, value in get_supplied_products(scr).products():
			transferred_detais = productwise_details.get(key)

			for field in ["qty", "serial_no", "batch_no"]:
				if value.get(field):
					transfer, consumed = (transferred_detais.get(field), value.get(field))
					if field == "serial_no":
						transfer, consumed = (sorted(transfer), sorted(consumed))

					self.assertEqual(transfer, consumed)

	def test_sco_with_material_transfer(self):
		"""
		- Set backflush based on Material Transfer.
		- Create SCO for the product Subcontracted Product SA1 and Subcontracted Product SA5.
		- Transfer the components from Stores to Supplier warehouse with batch no and serial nos.
		- Transfer extra product Subcontracted SRM Product 4 for the subcontract product Subcontracted Product SA5.
		- Create partial SCR against the SCO and check serial nos and batch no.
		"""

		set_backflush_based_on("Material Transferred for Subcontract")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 5,
				"rate": 100,
				"fg_product": "Subcontracted Product SA1",
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
		rm_products.append(
			{
				"main_product_code": "Subcontracted Product SA5",
				"product_code": "Subcontracted SRM Product 4",
				"qty": 6,
			}
		)
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name if product.get("qty") == 5 else sco.products[1].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.remove(scr1.products[1])
		scr1.save()
		scr1.submit()

		for key, value in get_supplied_products(scr1).products():
			transferred_detais = productwise_details.get(key)

			for field in ["qty", "serial_no", "batch_no"]:
				if value.get(field):
					self.assertEqual(value.get(field), transferred_detais.get(field))

		scr2 = make_subcontracting_receipt(sco.name)
		scr2.save()
		scr2.submit()

		for key, value in get_supplied_products(scr2).products():
			transferred_detais = productwise_details.get(key)

			for field in ["qty", "serial_no", "batch_no"]:
				if value.get(field):
					self.assertEqual(value.get(field), transferred_detais.get(field))

	def test_subcontracting_with_same_components_different_fg(self):
		"""
		- Set backflush based on Material Transfer.
		- Create SCO for the product Subcontracted Product SA2 and Subcontracted Product SA3.
		- Transfer the components from Stores to Supplier warehouse with serial nos.
		- Transfer extra qty of components for the product Subcontracted Product SA2.
		- Create partial SCR against the SCO and check serial nos.
		"""

		set_backflush_based_on("Material Transferred for Subcontract")
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
				"product_code": "Subcontracted Service Product 3",
				"qty": 6,
				"rate": 100,
				"fg_product": "Subcontracted Product SA3",
				"fg_product_qty": 6,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		rm_products[0]["qty"] += 1
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name if product.get("qty") == 5 else sco.products[1].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.products[0].qty = 3
		scr1.remove(scr1.products[1])
		scr1.save()
		scr1.submit()

		for key, value in get_supplied_products(scr1).products():
			transferred_detais = productwise_details.get(key)

			self.assertEqual(value.qty, 4)
			self.assertEqual(sorted(value.serial_no), sorted(transferred_detais.get("serial_no")[0:4]))

		scr2 = make_subcontracting_receipt(sco.name)
		scr2.products[0].qty = 2
		scr2.remove(scr2.products[1])
		scr2.save()
		scr2.submit()

		for key, value in get_supplied_products(scr2).products():
			transferred_detais = productwise_details.get(key)

			self.assertEqual(value.qty, 2)
			self.assertEqual(sorted(value.serial_no), sorted(transferred_detais.get("serial_no")[4:6]))

		scr3 = make_subcontracting_receipt(sco.name)
		scr3.save()
		scr3.submit()

		for key, value in get_supplied_products(scr3).products():
			transferred_detais = productwise_details.get(key)

			self.assertEqual(value.qty, 6)
			self.assertEqual(sorted(value.serial_no), sorted(transferred_detais.get("serial_no")[6:12]))

	def test_return_non_consumed_materials(self):
		"""
		- Set backflush based on Material Transfer.
		- Create SCO for product Subcontracted Product SA2.
		- Transfer the components from Stores to Supplier warehouse with serial nos.
		- Transfer extra qty of component for the subcontracted product Subcontracted Product SA2.
		- Create SCR for full qty against the SCO and change the qty of raw material.
		- After that return the non consumed material back to the store from supplier's warehouse.
		"""

		set_backflush_based_on("Material Transferred for Subcontract")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 2",
				"qty": 5,
				"rate": 100,
				"fg_product": "Subcontracted Product SA2",
				"fg_product_qty": 5,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		rm_products[0]["qty"] += 1
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.save()
		scr1.supplied_products[0].consumed_qty = 5
		scr1.supplied_products[0].serial_no = "\n".join(
			sorted(productwise_details.get("Subcontracted SRM Product 2").get("serial_no")[0:5])
		)
		scr1.submit()

		for key, value in get_supplied_products(scr1).products():
			transferred_detais = productwise_details.get(key)
			self.assertEqual(value.qty, 5)
			self.assertEqual(sorted(value.serial_no), sorted(transferred_detais.get("serial_no")[0:5]))

		sco.load_from_db()
		self.assertEqual(sco.supplied_products[0].consumed_qty, 5)
		doc = get_materials_from_supplier(sco.name, [d.name for d in sco.supplied_products])
		self.assertEqual(doc.products[0].qty, 1)
		self.assertEqual(doc.products[0].s_warehouse, "_Test Warehouse 1 - _TC")
		self.assertEqual(doc.products[0].t_warehouse, "_Test Warehouse - _TC")
		self.assertEqual(
			get_serial_nos(doc.products[0].serial_no),
			productwise_details.get(doc.products[0].product_code)["serial_no"][5:6],
		)

	def test_product_with_batch_based_on_bom(self):
		"""
		- Set backflush based on BOM.
		- Create SCO for product Subcontracted Product SA4 (has batch no).
		- Transfer the components from Stores to Supplier warehouse with batch no and serial nos.
		- Transfer the components in multiple batches.
		- Create the 3 SCR against the SCO and split Subcontracted Products into two batches.
		- Keep the qty as 2 for Subcontracted Product in the SCR.
		"""

		set_backflush_based_on("BOM")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 4",
				"qty": 10,
				"rate": 100,
				"fg_product": "Subcontracted Product SA4",
				"fg_product_qty": 10,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = [
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 1",
				"qty": 10.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 2",
				"qty": 10.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 3",
				"qty": 3.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 3",
				"qty": 3.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 3",
				"qty": 3.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 3",
				"qty": 1.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
		]
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.products[0].qty = 2
		add_second_row_in_scr(scr1)
		scr1.flags.ignore_mandatory = True
		scr1.save()
		scr1.set_missing_values()
		scr1.submit()

		for key, value in get_supplied_products(scr1).products():
			self.assertEqual(value.qty, 4)

		scr2 = make_subcontracting_receipt(sco.name)
		scr2.products[0].qty = 2
		add_second_row_in_scr(scr2)
		scr2.flags.ignore_mandatory = True
		scr2.save()
		scr2.set_missing_values()
		scr2.submit()

		for key, value in get_supplied_products(scr2).products():
			self.assertEqual(value.qty, 4)

		scr3 = make_subcontracting_receipt(sco.name)
		scr3.products[0].qty = 2
		scr3.flags.ignore_mandatory = True
		scr3.save()
		scr3.set_missing_values()
		scr3.submit()

		for key, value in get_supplied_products(scr3).products():
			self.assertEqual(value.qty, 2)

	def test_product_with_batch_based_on_material_transfer(self):
		"""
		- Set backflush based on Material Transferred for Subcontract.
		- Create SCO for product Subcontracted Product SA4 (has batch no).
		- Transfer the components from Stores to Supplier warehouse with batch no and serial nos.
		- Transfer the components in multiple batches with extra 2 qty for the batched product.
		- Create the 3 SCR against the SCO and split Subcontracted Products into two batches.
		- Keep the qty as 2 for Subcontracted Product in the SCR.
		- In the first SCR the batched raw materials will be consumed 2 extra qty.
		"""

		set_backflush_based_on("Material Transferred for Subcontract")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 4",
				"qty": 10,
				"rate": 100,
				"fg_product": "Subcontracted Product SA4",
				"fg_product_qty": 10,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = [
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 1",
				"qty": 10.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 2",
				"qty": 10.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 3",
				"qty": 3.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 3",
				"qty": 3.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 3",
				"qty": 3.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
			{
				"main_product_code": "Subcontracted Product SA4",
				"product_code": "Subcontracted SRM Product 3",
				"qty": 3.0,
				"rate": 100.0,
				"stock_uom": "Nos",
				"warehouse": "_Test Warehouse - _TC",
			},
		]
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.products[0].qty = 2
		add_second_row_in_scr(scr1)
		scr1.flags.ignore_mandatory = True
		scr1.save()
		scr1.set_missing_values()
		scr1.submit()

		for key, value in get_supplied_products(scr1).products():
			qty = 4 if key != "Subcontracted SRM Product 3" else 6
			self.assertEqual(value.qty, qty)

		scr2 = make_subcontracting_receipt(sco.name)
		scr2.products[0].qty = 2
		add_second_row_in_scr(scr2)
		scr2.flags.ignore_mandatory = True
		scr2.save()
		scr2.set_missing_values()
		scr2.submit()

		for key, value in get_supplied_products(scr2).products():
			self.assertEqual(value.qty, 4)

		scr3 = make_subcontracting_receipt(sco.name)
		scr3.products[0].qty = 2
		scr3.flags.ignore_mandatory = True
		scr3.save()
		scr3.set_missing_values()
		scr3.submit()

		for key, value in get_supplied_products(scr3).products():
			self.assertEqual(value.qty, 1)

	def test_partial_transfer_serial_no_components_based_on_material_transfer(self):
		"""
		- Set backflush based on Material Transferred for Subcontract.
		- Create SCO for the product Subcontracted Product SA2.
		- Transfer the partial components from Stores to Supplier warehouse with serial nos.
		- Create partial SCR against the SCO and change the qty manually.
		- Transfer the remaining components from Stores to Supplier warehouse with serial nos.
		- Create SCR for remaining qty against the SCO and change the qty manually.
		"""

		set_backflush_based_on("Material Transferred for Subcontract")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 2",
				"qty": 10,
				"rate": 100,
				"fg_product": "Subcontracted Product SA2",
				"fg_product_qty": 10,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		rm_products[0]["qty"] = 5
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.products[0].qty = 5
		scr1.flags.ignore_mandatory = True
		scr1.save()
		scr1.set_missing_values()

		for key, value in get_supplied_products(scr1).products():
			details = productwise_details.get(key)
			self.assertEqual(value.qty, 3)
			self.assertEqual(sorted(value.serial_no), sorted(details.serial_no[0:3]))

		scr1.load_from_db()
		scr1.supplied_products[0].consumed_qty = 5
		scr1.supplied_products[0].serial_no = "\n".join(
			productwise_details[scr1.supplied_products[0].rm_product_code]["serial_no"]
		)
		scr1.save()
		scr1.submit()

		for key, value in get_supplied_products(scr1).products():
			details = productwise_details.get(key)
			self.assertEqual(value.qty, details.qty)
			self.assertEqual(sorted(value.serial_no), sorted(details.serial_no))

		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr2 = make_subcontracting_receipt(sco.name)
		scr2.submit()

		for key, value in get_supplied_products(scr2).products():
			details = productwise_details.get(key)
			self.assertEqual(value.qty, details.qty)
			self.assertEqual(sorted(value.serial_no), sorted(details.serial_no))

	def test_incorrect_serial_no_components_based_on_material_transfer(self):
		"""
		- Set backflush based on Material Transferred for Subcontract.
		- Create SCO for the product Subcontracted Product SA2.
		- Transfer the serialized componenets to the supplier.
		- Create SCR and change the serial no which is not transferred.
		- System should throw the error and not allowed to save the SCR.
		"""

		set_backflush_based_on("Material Transferred for Subcontract")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 2",
				"qty": 10,
				"rate": 100,
				"fg_product": "Subcontracted Product SA2",
				"fg_product_qty": 10,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.save()
		scr1.supplied_products[0].serial_no = "ABCD"
		self.assertRaises(frappe.ValidationError, scr1.save)
		scr1.delete()

	def test_partial_transfer_batch_based_on_material_transfer(self):
		"""
		- Set backflush based on Material Transferred for Subcontract.
		- Create SCO for the product Subcontracted Product SA6.
		- Transfer the partial components from Stores to Supplier warehouse with batch.
		- Create partial SCR against the SCO and change the qty manually.
		- Transfer the remaining components from Stores to Supplier warehouse with batch.
		- Create SCR for remaining qty against the SCO and change the qty manually.
		"""

		set_backflush_based_on("Material Transferred for Subcontract")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 6",
				"qty": 10,
				"rate": 100,
				"fg_product": "Subcontracted Product SA6",
				"fg_product_qty": 10,
			},
		]
		sco = get_subcontracting_order(service_products=service_products)
		rm_products = get_rm_products(sco.supplied_products)
		rm_products[0]["qty"] = 5
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.products[0].qty = 5
		scr1.save()

		transferred_batch_no = ""
		for key, value in get_supplied_products(scr1).products():
			details = productwise_details.get(key)
			self.assertEqual(value.qty, 3)
			transferred_batch_no = details.batch_no
			self.assertEqual(value.batch_no, details.batch_no)

		scr1.load_from_db()
		scr1.supplied_products[0].consumed_qty = 5
		scr1.supplied_products[0].batch_no = list(transferred_batch_no.keys())[0]
		scr1.save()
		scr1.submit()

		for key, value in get_supplied_products(scr1).products():
			details = productwise_details.get(key)
			self.assertEqual(value.qty, details.qty)
			self.assertEqual(value.batch_no, details.batch_no)

		productwise_details = make_stock_in_entry(rm_products=rm_products)
		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		scr1 = make_subcontracting_receipt(sco.name)
		scr1.submit()

		for key, value in get_supplied_products(scr1).products():
			details = productwise_details.get(key)
			self.assertEqual(value.qty, details.qty)
			self.assertEqual(value.batch_no, details.batch_no)

	def test_sco_supplied_qty(self):
		"""
		Check if 'Supplied Qty' in SCO's Supplied Products table is reset on submit/cancel.
		"""
		set_backflush_based_on("Material Transferred for Subcontract")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 5,
				"rate": 100,
				"fg_product": "Subcontracted Product SA1",
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
		rm_products = [
			{"product_code": "Subcontracted SRM Product 1", "qty": 5, "main_product_code": "Subcontracted Product SA1"},
			{"product_code": "Subcontracted SRM Product 2", "qty": 5, "main_product_code": "Subcontracted Product SA1"},
			{"product_code": "Subcontracted SRM Product 3", "qty": 5, "main_product_code": "Subcontracted Product SA1"},
			{"product_code": "Subcontracted SRM Product 5", "qty": 6, "main_product_code": "Subcontracted Product SA5"},
			{"product_code": "Subcontracted SRM Product 4", "qty": 6, "main_product_code": "Subcontracted Product SA5"},
		]
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name if product.get("qty") == 5 else sco.products[1].name

		se = make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		sco.reload()
		for product in sco.get("supplied_products"):
			self.assertIn(product.supplied_qty, [5.0, 6.0])

		se.cancel()
		sco.reload()
		for product in sco.get("supplied_products"):
			self.assertEqual(product.supplied_qty, 0.0)


def add_second_row_in_scr(scr):
	product_dict = {}
	for column in [
		"product_code",
		"product_name",
		"qty",
		"uom",
		"bom",
		"warehouse",
		"stock_uom",
		"subcontracting_order",
		"subcontracting_order_finished_good_product",
		"conversion_factor",
		"rate",
		"expense_account",
		"sco_rm_detail",
	]:
		product_dict[column] = scr.products[0].get(column)

	scr.append("products", product_dict)


def get_supplied_products(scr_doc):
	supplied_products = {}
	for row in scr_doc.get("supplied_products"):
		if row.rm_product_code not in supplied_products:
			supplied_products.setdefault(
				row.rm_product_code, frappe._dict({"qty": 0, "serial_no": [], "batch_no": defaultdict(float)})
			)

		details = supplied_products[row.rm_product_code]
		update_product_details(row, details)

	return supplied_products


def make_stock_in_entry(**args):
	args = frappe._dict(args)

	products = {}
	for row in args.rm_products:
		row = frappe._dict(row)

		doc = make_stock_entry(
			target=row.warehouse or "_Test Warehouse - _TC",
			product_code=row.product_code,
			qty=row.qty or 1,
			basic_rate=row.rate or 100,
		)

		if row.product_code not in products:
			products.setdefault(
				row.product_code, frappe._dict({"qty": 0, "serial_no": [], "batch_no": defaultdict(float)})
			)

		child_row = doc.products[0]
		details = products[child_row.product_code]
		update_product_details(child_row, details)

	return products


def update_product_details(child_row, details):
	details.qty += (
		child_row.get("qty")
		if child_row.doctype == "Stock Entry Detail"
		else child_row.get("consumed_qty")
	)

	if child_row.serial_no:
		details.serial_no.extend(get_serial_nos(child_row.serial_no))

	if child_row.batch_no:
		details.batch_no[child_row.batch_no] += child_row.get("qty") or child_row.get("consumed_qty")


def make_stock_transfer_entry(**args):
	args = frappe._dict(args)

	products = []
	for row in args.rm_products:
		row = frappe._dict(row)

		product = {
			"product_code": row.main_product_code or args.main_product_code,
			"rm_product_code": row.product_code,
			"qty": row.qty or 1,
			"product_name": row.product_code,
			"rate": row.rate or 100,
			"stock_uom": row.stock_uom or "Nos",
			"warehouse": row.warehouse or "_Test Warehouse - _TC",
		}

		product_details = args.productwise_details.get(row.product_code)

		if product_details and product_details.serial_no:
			serial_nos = product_details.serial_no[0 : cint(row.qty)]
			product["serial_no"] = "\n".join(serial_nos)
			product_details.serial_no = list(set(product_details.serial_no) - set(serial_nos))

		if product_details and product_details.batch_no:
			for batch_no, batch_qty in product_details.batch_no.products():
				if batch_qty >= row.qty:
					product["batch_no"] = batch_no
					product_details.batch_no[batch_no] -= row.qty
					break

		products.append(product)

	ste_dict = make_rm_stock_entry(args.sco_no, products)
	doc = frappe.get_doc(ste_dict)
	doc.insert()
	doc.submit()

	return doc


def make_subcontracted_products():
	sub_contracted_products = {
		"Subcontracted Product SA1": {},
		"Subcontracted Product SA2": {},
		"Subcontracted Product SA3": {},
		"Subcontracted Product SA4": {
			"has_batch_no": 1,
			"create_new_batch": 1,
			"batch_number_series": "SBAT.####",
		},
		"Subcontracted Product SA5": {},
		"Subcontracted Product SA6": {},
		"Subcontracted Product SA7": {},
	}

	for product, properties in sub_contracted_products.products():
		if not frappe.db.exists("Product", product):
			properties.update({"is_stock_product": 1, "is_sub_contracted_product": 1})
			make_product(product, properties)


def make_raw_materials():
	raw_materials = {
		"Subcontracted SRM Product 1": {},
		"Subcontracted SRM Product 2": {"has_serial_no": 1, "serial_no_series": "SRI.####"},
		"Subcontracted SRM Product 3": {
			"has_batch_no": 1,
			"create_new_batch": 1,
			"batch_number_series": "BAT.####",
		},
		"Subcontracted SRM Product 4": {"has_serial_no": 1, "serial_no_series": "SRII.####"},
		"Subcontracted SRM Product 5": {"has_serial_no": 1, "serial_no_series": "SRII.####"},
	}

	for product, properties in raw_materials.products():
		if not frappe.db.exists("Product", product):
			properties.update({"is_stock_product": 1})
			make_product(product, properties)


def make_service_product(product, properties={}):
	if not frappe.db.exists("Product", product):
		properties.update({"is_stock_product": 0})
		make_product(product, properties)


def make_service_products():
	service_products = {
		"Subcontracted Service Product 1": {},
		"Subcontracted Service Product 2": {},
		"Subcontracted Service Product 3": {},
		"Subcontracted Service Product 4": {},
		"Subcontracted Service Product 5": {},
		"Subcontracted Service Product 6": {},
		"Subcontracted Service Product 7": {},
	}

	for product, properties in service_products.products():
		make_service_product(product, properties)


def make_bom_for_subcontracted_products():
	boms = {
		"Subcontracted Product SA1": [
			"Subcontracted SRM Product 1",
			"Subcontracted SRM Product 2",
			"Subcontracted SRM Product 3",
		],
		"Subcontracted Product SA2": ["Subcontracted SRM Product 2"],
		"Subcontracted Product SA3": ["Subcontracted SRM Product 2"],
		"Subcontracted Product SA4": [
			"Subcontracted SRM Product 1",
			"Subcontracted SRM Product 2",
			"Subcontracted SRM Product 3",
		],
		"Subcontracted Product SA5": ["Subcontracted SRM Product 5"],
		"Subcontracted Product SA6": ["Subcontracted SRM Product 3"],
		"Subcontracted Product SA7": ["Subcontracted SRM Product 1"],
	}

	for product_code, raw_materials in boms.products():
		if not frappe.db.exists("BOM", {"product": product_code}):
			make_bom(product=product_code, raw_materials=raw_materials, rate=100)


def set_backflush_based_on(based_on):
	frappe.db.set_value(
		"Buying Settings", None, "backflush_raw_materials_of_subcontract_based_on", based_on
	)


def get_subcontracting_order(**args):
	from erpnext.subcontracting.doctype.subcontracting_order.test_subcontracting_order import (
		create_subcontracting_order,
	)

	args = frappe._dict(args)

	if args.get("po_name"):
		po = frappe.get_doc("Purchase Order", args.get("po_name"))

		if po.is_subcontracted:
			return create_subcontracting_order(po_name=po.name, **args)

	if not args.service_products:
		service_products = [
			{
				"warehouse": args.warehouse or "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 7",
				"qty": 10,
				"rate": 100,
				"fg_product": "Subcontracted Product SA7",
				"fg_product_qty": 10,
			},
		]
	else:
		service_products = args.service_products

	po = create_purchase_order(
		rm_products=service_products,
		is_subcontracted=1,
		supplier_warehouse=args.supplier_warehouse or "_Test Warehouse 1 - _TC",
		company=args.company,
	)

	return create_subcontracting_order(po_name=po.name, **args)


def get_rm_products(supplied_products):
	rm_products = []

	for product in supplied_products:
		rm_products.append(
			{
				"main_product_code": product.main_product_code,
				"product_code": product.rm_product_code,
				"qty": product.required_qty,
				"rate": product.rate,
				"stock_uom": product.stock_uom,
				"warehouse": product.reserve_warehouse,
			}
		)

	return rm_products


def make_subcontracted_product(**args):
	from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom

	args = frappe._dict(args)

	if not frappe.db.exists("Product", args.product_code):
		make_product(
			args.product_code,
			{
				"is_stock_product": 1,
				"is_sub_contracted_product": 1,
				"has_batch_no": args.get("has_batch_no") or 0,
			},
		)

	if not args.raw_materials:
		if not frappe.db.exists("Product", "Test Extra Product 1"):
			make_product(
				"Test Extra Product 1",
				{
					"is_stock_product": 1,
				},
			)

		if not frappe.db.exists("Product", "Test Extra Product 2"):
			make_product(
				"Test Extra Product 2",
				{
					"is_stock_product": 1,
				},
			)

		args.raw_materials = ["_Test FG Product", "Test Extra Product 1"]

	if not frappe.db.get_value("BOM", {"product": args.product_code}, "name"):
		make_bom(product=args.product_code, raw_materials=args.get("raw_materials"))
