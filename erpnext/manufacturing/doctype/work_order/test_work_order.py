# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import copy

import frappe
from frappe.tests.utils import FrappeTestCase, change_settings, timeout
from frappe.utils import add_days, add_months, add_to_date, cint, flt, now, today

from erpnext.manufacturing.doctype.job_card.job_card import JobCardCancelError
from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
from erpnext.manufacturing.doctype.work_order.work_order import (
	CapacityError,
	ProductHasVariantError,
	OverProductionError,
	StockOverProductionError,
	close_work_order,
	make_job_card,
	make_stock_entry,
	make_stock_return_entry,
	stop_unstop,
)
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.product.test_product import create_product, make_product
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.doctype.stock_entry import test_stock_entry
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.stock.utils import get_bin

test_dependencies = ["BOM"]


class TestWorkOrder(FrappeTestCase):
	def setUp(self):
		self.warehouse = "_Test Warehouse 2 - _TC"
		self.product = "_Test Product"
		prepare_data_for_backflush_based_on_materials_transferred()

	def tearDown(self):
		frappe.db.rollback()

	def check_planned_qty(self):

		planned0 = (
			frappe.db.get_value(
				"Bin", {"product_code": "_Test FG Product", "warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty"
			)
			or 0
		)

		wo_order = make_wo_order_test_record()

		planned1 = frappe.db.get_value(
			"Bin", {"product_code": "_Test FG Product", "warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty"
		)

		self.assertEqual(planned1, planned0 + 10)

		# add raw materials to stores
		test_stock_entry.make_stock_entry(
			product_code="_Test Product", target="Stores - _TC", qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100", target="Stores - _TC", qty=100, basic_rate=100
		)

		# from stores to wip
		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 4))
		for d in s.get("products"):
			d.s_warehouse = "Stores - _TC"
		s.insert()
		s.submit()

		# from wip to fg
		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 4))
		s.insert()
		s.submit()

		self.assertEqual(frappe.db.get_value("Work Order", wo_order.name, "produced_qty"), 4)

		planned2 = frappe.db.get_value(
			"Bin", {"product_code": "_Test FG Product", "warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty"
		)

		self.assertEqual(planned2, planned0 + 6)

		return wo_order

	def test_over_production(self):
		wo_doc = self.check_planned_qty()

		test_stock_entry.make_stock_entry(
			product_code="_Test Product", target="_Test Warehouse - _TC", qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100", target="_Test Warehouse - _TC", qty=100, basic_rate=100
		)

		s = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 7))
		s.insert()

		self.assertRaises(StockOverProductionError, s.submit)

	def test_planned_operating_cost(self):
		wo_order = make_wo_order_test_record(
			product="_Test FG Product 2", planned_start_date=now(), qty=1, do_not_save=True
		)
		wo_order.set_work_order_operations()
		cost = wo_order.planned_operating_cost
		wo_order.qty = 2
		wo_order.set_work_order_operations()
		self.assertEqual(wo_order.planned_operating_cost, cost * 2)

	def test_reserved_qty_for_partial_completion(self):
		product = "_Test Product"
		warehouse = "_Test Warehouse - _TC"

		bin1_at_start = get_bin(product, warehouse)

		# reset to correct value
		bin1_at_start.update_reserved_qty_for_production()

		wo_order = make_wo_order_test_record(
			product="_Test FG Product", qty=2, source_warehouse=warehouse, skip_transfer=1
		)

		reserved_qty_on_submission = cint(get_bin(product, warehouse).reserved_qty_for_production)

		# reserved qty for production is updated
		self.assertEqual(cint(bin1_at_start.reserved_qty_for_production) + 2, reserved_qty_on_submission)

		test_stock_entry.make_stock_entry(
			product_code="_Test Product", target=warehouse, qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100", target=warehouse, qty=100, basic_rate=100
		)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 1))
		s.submit()

		bin1_at_completion = get_bin(product, warehouse)

		self.assertEqual(
			cint(bin1_at_completion.reserved_qty_for_production), reserved_qty_on_submission - 1
		)

	def test_production_product(self):
		wo_order = make_wo_order_test_record(product="_Test FG Product", qty=1, do_not_save=True)
		frappe.db.set_value("Product", "_Test FG Product", "end_of_life", "2000-1-1")

		self.assertRaises(frappe.ValidationError, wo_order.save)

		frappe.db.set_value("Product", "_Test FG Product", "end_of_life", None)
		frappe.db.set_value("Product", "_Test FG Product", "disabled", 1)

		self.assertRaises(frappe.ValidationError, wo_order.save)

		frappe.db.set_value("Product", "_Test FG Product", "disabled", 0)

		wo_order = make_wo_order_test_record(product="_Test Variant Product", qty=1, do_not_save=True)
		self.assertRaises(ProductHasVariantError, wo_order.save)

	def test_reserved_qty_for_production_submit(self):
		self.bin1_at_start = get_bin(self.product, self.warehouse)

		# reset to correct value
		self.bin1_at_start.update_reserved_qty_for_production()

		self.wo_order = make_wo_order_test_record(
			product="_Test FG Product", qty=2, source_warehouse=self.warehouse
		)

		self.bin1_on_submit = get_bin(self.product, self.warehouse)

		# reserved qty for production is updated
		self.assertEqual(
			cint(self.bin1_at_start.reserved_qty_for_production) + 2,
			cint(self.bin1_on_submit.reserved_qty_for_production),
		)
		self.assertEqual(
			cint(self.bin1_at_start.projected_qty), cint(self.bin1_on_submit.projected_qty) + 2
		)

	def test_reserved_qty_for_production_cancel(self):
		self.test_reserved_qty_for_production_submit()

		self.wo_order.cancel()

		bin1_on_cancel = get_bin(self.product, self.warehouse)

		# reserved_qty_for_producion updated
		self.assertEqual(
			cint(self.bin1_at_start.reserved_qty_for_production),
			cint(bin1_on_cancel.reserved_qty_for_production),
		)
		self.assertEqual(self.bin1_at_start.projected_qty, cint(bin1_on_cancel.projected_qty))

	def test_reserved_qty_for_production_on_stock_entry(self):
		test_stock_entry.make_stock_entry(
			product_code="_Test Product", target=self.warehouse, qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100", target=self.warehouse, qty=100, basic_rate=100
		)

		self.test_reserved_qty_for_production_submit()

		s = frappe.get_doc(make_stock_entry(self.wo_order.name, "Material Transfer for Manufacture", 2))

		s.submit()

		bin1_on_start_production = get_bin(self.product, self.warehouse)

		# reserved_qty_for_producion updated
		self.assertEqual(
			cint(self.bin1_at_start.reserved_qty_for_production),
			cint(bin1_on_start_production.reserved_qty_for_production),
		)

		# projected qty will now be 2 less (becuase of product movement)
		self.assertEqual(
			cint(self.bin1_at_start.projected_qty), cint(bin1_on_start_production.projected_qty) + 2
		)

		s = frappe.get_doc(make_stock_entry(self.wo_order.name, "Manufacture", 2))

		bin1_on_end_production = get_bin(self.product, self.warehouse)

		# no change in reserved / projected
		self.assertEqual(
			cint(bin1_on_end_production.reserved_qty_for_production),
			cint(bin1_on_start_production.reserved_qty_for_production),
		)

	def test_reserved_qty_for_production_closed(self):

		wo1 = make_wo_order_test_record(product="_Test FG Product", qty=2, source_warehouse=self.warehouse)
		product = wo1.required_products[0].product_code
		bin_before = get_bin(product, self.warehouse)
		bin_before.update_reserved_qty_for_production()

		make_wo_order_test_record(product="_Test FG Product", qty=2, source_warehouse=self.warehouse)
		close_work_order(wo1.name, "Closed")

		bin_after = get_bin(product, self.warehouse)
		self.assertEqual(bin_before.reserved_qty_for_production, bin_after.reserved_qty_for_production)

	def test_backflush_qty_for_overpduction_manufacture(self):
		cancel_stock_entry = []
		allow_overproduction("overproduction_percentage_for_work_order", 30)
		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=100)
		ste1 = test_stock_entry.make_stock_entry(
			product_code="_Test Product", target="_Test Warehouse - _TC", qty=120, basic_rate=5000.0
		)
		ste2 = test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			target="_Test Warehouse - _TC",
			qty=240,
			basic_rate=1000.0,
		)

		cancel_stock_entry.extend([ste1.name, ste2.name])

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 60))
		s.submit()
		cancel_stock_entry.append(s.name)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 60))
		s.submit()
		cancel_stock_entry.append(s.name)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 60))
		s.submit()
		cancel_stock_entry.append(s.name)

		s1 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 50))
		s1.submit()
		cancel_stock_entry.append(s1.name)

		self.assertEqual(s1.products[0].qty, 50)
		self.assertEqual(s1.products[1].qty, 100)
		cancel_stock_entry.reverse()
		for ste in cancel_stock_entry:
			doc = frappe.get_doc("Stock Entry", ste)
			doc.cancel()

		allow_overproduction("overproduction_percentage_for_work_order", 0)

	def test_reserved_qty_for_stopped_production(self):
		test_stock_entry.make_stock_entry(
			product_code="_Test Product", target=self.warehouse, qty=100, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100", target=self.warehouse, qty=100, basic_rate=100
		)

		# 	0 0 0

		self.test_reserved_qty_for_production_submit()

		# 2 0 -2

		s = frappe.get_doc(make_stock_entry(self.wo_order.name, "Material Transfer for Manufacture", 1))

		s.submit()

		# 1 -1 0

		bin1_on_start_production = get_bin(self.product, self.warehouse)

		# reserved_qty_for_producion updated
		self.assertEqual(
			cint(self.bin1_at_start.reserved_qty_for_production) + 1,
			cint(bin1_on_start_production.reserved_qty_for_production),
		)

		# projected qty will now be 2 less (becuase of product movement)
		self.assertEqual(
			cint(self.bin1_at_start.projected_qty), cint(bin1_on_start_production.projected_qty) + 2
		)

		# STOP
		stop_unstop(self.wo_order.name, "Stopped")

		bin1_on_stop_production = get_bin(self.product, self.warehouse)

		# no change in reserved / projected
		self.assertEqual(
			cint(bin1_on_stop_production.reserved_qty_for_production),
			cint(self.bin1_at_start.reserved_qty_for_production),
		)
		self.assertEqual(
			cint(bin1_on_stop_production.projected_qty) + 1, cint(self.bin1_at_start.projected_qty)
		)

	def test_scrap_material_qty(self):
		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=2)

		# add raw materials to stores
		test_stock_entry.make_stock_entry(
			product_code="_Test Product", target="Stores - _TC", qty=10, basic_rate=5000.0
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100", target="Stores - _TC", qty=10, basic_rate=1000.0
		)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 2))
		for d in s.get("products"):
			d.s_warehouse = "Stores - _TC"
		s.insert()
		s.submit()

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 2))
		s.insert()
		s.submit()

		wo_order_details = frappe.db.get_value(
			"Work Order", wo_order.name, ["scrap_warehouse", "qty", "produced_qty", "bom_no"], as_dict=1
		)

		scrap_product_details = get_scrap_product_details(wo_order_details.bom_no)

		self.assertEqual(wo_order_details.produced_qty, 2)

		for product in s.products:
			if product.bom_no and product.product_code in scrap_product_details:
				self.assertEqual(wo_order_details.scrap_warehouse, product.t_warehouse)
				self.assertEqual(flt(wo_order_details.qty) * flt(scrap_product_details[product.product_code]), product.qty)

	def test_allow_overproduction(self):
		allow_overproduction("overproduction_percentage_for_work_order", 0)
		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=2)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product", target="_Test Warehouse - _TC", qty=10, basic_rate=5000.0
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			target="_Test Warehouse - _TC",
			qty=10,
			basic_rate=1000.0,
		)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 3))
		s.insert()
		self.assertRaises(StockOverProductionError, s.submit)

		allow_overproduction("overproduction_percentage_for_work_order", 50)
		s.load_from_db()
		s.submit()
		self.assertEqual(s.docstatus, 1)

		allow_overproduction("overproduction_percentage_for_work_order", 0)

	def test_over_production_for_sales_order(self):
		so = make_sales_order(product_code="_Test FG Product", qty=2)

		allow_overproduction("overproduction_percentage_for_sales_order", 0)
		wo_order = make_wo_order_test_record(
			planned_start_date=now(), sales_order=so.name, qty=3, do_not_save=True
		)

		self.assertRaises(OverProductionError, wo_order.save)

		allow_overproduction("overproduction_percentage_for_sales_order", 50)
		wo_order = make_wo_order_test_record(planned_start_date=now(), sales_order=so.name, qty=3)

		self.assertEqual(wo_order.docstatus, 1)

		allow_overproduction("overproduction_percentage_for_sales_order", 0)

	def test_work_order_with_non_stock_product(self):
		products = {
			"Finished Good Test Product For non stock": 1,
			"_Test FG Product": 1,
			"_Test FG Non Stock Product": 0,
		}
		for product, is_stock_product in products.products():
			make_product(product, {"is_stock_product": is_stock_product})

		if not frappe.db.get_value("Product Price", {"product_code": "_Test FG Non Stock Product"}):
			frappe.get_doc(
				{
					"doctype": "Product Price",
					"product_code": "_Test FG Non Stock Product",
					"price_list_rate": 1000,
					"price_list": "_Test Price List India",
				}
			).insert(ignore_permissions=True)

		fg_product = "Finished Good Test Product For non stock"
		test_stock_entry.make_stock_entry(
			product_code="_Test FG Product", target="_Test Warehouse - _TC", qty=1, basic_rate=100
		)

		if not frappe.db.get_value("BOM", {"product": fg_product, "docstatus": 1}):
			bom = make_bom(
				product=fg_product,
				rate=1000,
				raw_materials=["_Test FG Product", "_Test FG Non Stock Product"],
				do_not_save=True,
			)
			bom.rm_cost_as_per = "Price List"  # non stock product won't have valuation rate
			bom.buying_price_list = "_Test Price List India"
			bom.currency = "INR"
			bom.save()

		wo = make_wo_order_test_record(production_product=fg_product)

		se = frappe.get_doc(make_stock_entry(wo.name, "Material Transfer for Manufacture", 1))
		se.insert()
		se.submit()

		ste = frappe.get_doc(make_stock_entry(wo.name, "Manufacture", 1))
		ste.insert()
		self.assertEqual(len(ste.additional_costs), 1)
		self.assertEqual(ste.total_additional_costs, 1000)

	@timeout(seconds=60)
	def test_job_card(self):
		stock_entries = []
		bom = frappe.get_doc("BOM", {"docstatus": 1, "with_operations": 1, "company": "_Test Company"})

		work_order = make_wo_order_test_record(
			product=bom.product, qty=1, bom_no=bom.name, source_warehouse="_Test Warehouse - _TC"
		)

		for row in work_order.required_products:
			stock_entry_doc = test_stock_entry.make_stock_entry(
				product_code=row.product_code, target="_Test Warehouse - _TC", qty=row.required_qty, basic_rate=100
			)
			stock_entries.append(stock_entry_doc)

		ste = frappe.get_doc(make_stock_entry(work_order.name, "Material Transfer for Manufacture", 1))
		ste.submit()
		stock_entries.append(ste)

		job_cards = frappe.get_all(
			"Job Card", filters={"work_order": work_order.name}, order_by="creation asc"
		)
		self.assertEqual(len(job_cards), len(bom.operations))

		for i, job_card in enumerate(job_cards):
			doc = frappe.get_doc("Job Card", job_card)
			doc.time_logs[0].completed_qty = 1
			doc.submit()

		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 1))
		ste1.submit()
		stock_entries.append(ste1)

		for job_card in job_cards:
			doc = frappe.get_doc("Job Card", job_card)
			self.assertRaises(JobCardCancelError, doc.cancel)

		stock_entries.reverse()
		for stock_entry in stock_entries:
			stock_entry.cancel()

	def test_capcity_planning(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			{"disable_capacity_planning": 0, "capacity_planning_for_days": 1},
		)

		data = frappe.get_cached_value(
			"BOM",
			{"docstatus": 1, "product": "_Test FG Product 2", "with_operations": 1, "company": "_Test Company"},
			["name", "product"],
		)

		if data:
			bom, bom_product = data

			planned_start_date = add_months(today(), months=-1)
			work_order = make_wo_order_test_record(
				product=bom_product, qty=10, bom_no=bom, planned_start_date=planned_start_date
			)

			work_order1 = make_wo_order_test_record(
				product=bom_product, qty=30, bom_no=bom, planned_start_date=planned_start_date, do_not_submit=1
			)

			self.assertRaises(CapacityError, work_order1.submit)

			frappe.db.set_value("Manufacturing Settings", None, {"capacity_planning_for_days": 30})

			work_order1.reload()
			work_order1.submit()
			self.assertTrue(work_order1.docstatus, 1)

			work_order1.cancel()
			work_order.cancel()

	def test_work_order_with_non_transfer_product(self):
		frappe.db.set_value("Manufacturing Settings", None, "backflush_raw_materials_based_on", "BOM")

		products = {"Finished Good Transfer Product": 1, "_Test FG Product": 1, "_Test FG Product 1": 0}
		for product, allow_transfer in products.products():
			make_product(product, {"include_product_in_manufacturing": allow_transfer})

		fg_product = "Finished Good Transfer Product"
		test_stock_entry.make_stock_entry(
			product_code="_Test FG Product", target="_Test Warehouse - _TC", qty=1, basic_rate=100
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test FG Product 1", target="_Test Warehouse - _TC", qty=1, basic_rate=100
		)

		if not frappe.db.get_value("BOM", {"product": fg_product}):
			make_bom(product=fg_product, raw_materials=["_Test FG Product", "_Test FG Product 1"])

		wo = make_wo_order_test_record(production_product=fg_product)
		ste = frappe.get_doc(make_stock_entry(wo.name, "Material Transfer for Manufacture", 1))
		ste.insert()
		ste.submit()
		self.assertEqual(len(ste.products), 1)
		ste1 = frappe.get_doc(make_stock_entry(wo.name, "Manufacture", 1))
		self.assertEqual(len(ste1.products), 3)

	def test_cost_center_for_manufacture(self):
		wo_order = make_wo_order_test_record()
		ste = make_stock_entry(wo_order.name, "Material Transfer for Manufacture", wo_order.qty)
		self.assertEqual(ste.get("products")[0].get("cost_center"), "_Test Cost Center - _TC")

	def test_operation_time_with_batch_size(self):
		fg_product = "Test Batch Size Product For BOM"
		rm1 = "Test Batch Size Product RM 1 For BOM"

		for product in ["Test Batch Size Product For BOM", "Test Batch Size Product RM 1 For BOM"]:
			make_product(product, {"include_product_in_manufacturing": 1, "is_stock_product": 1})

		bom_name = frappe.db.get_value(
			"BOM", {"product": fg_product, "is_active": 1, "with_operations": 1}, "name"
		)

		if not bom_name:
			bom = make_bom(product=fg_product, rate=1000, raw_materials=[rm1], do_not_save=True)
			bom.with_operations = 1
			bom.append(
				"operations",
				{
					"operation": "_Test Operation 1",
					"workstation": "_Test Workstation 1",
					"description": "Test Data",
					"operating_cost": 100,
					"time_in_mins": 40,
					"batch_size": 5,
				},
			)

			bom.save()
			bom.submit()
			bom_name = bom.name

		work_order = make_wo_order_test_record(
			product=fg_product, planned_start_date=now(), qty=1, do_not_save=True
		)

		work_order.set_work_order_operations()
		work_order.save()
		self.assertEqual(work_order.operations[0].time_in_mins, 8.0)

		work_order1 = make_wo_order_test_record(
			product=fg_product, planned_start_date=now(), qty=5, do_not_save=True
		)

		work_order1.set_work_order_operations()
		work_order1.save()
		self.assertEqual(work_order1.operations[0].time_in_mins, 40.0)

	def test_batch_size_for_fg_product(self):
		fg_product = "Test Batch Size Product For BOM 3"
		rm1 = "Test Batch Size Product RM 1 For BOM 3"

		frappe.db.set_value("Manufacturing Settings", None, "make_serial_no_batch_from_work_order", 0)
		for product in ["Test Batch Size Product For BOM 3", "Test Batch Size Product RM 1 For BOM 3"]:
			product_args = {"include_product_in_manufacturing": 1, "is_stock_product": 1}

			if product == fg_product:
				product_args["has_batch_no"] = 1
				product_args["create_new_batch"] = 1
				product_args["batch_number_series"] = "TBSI3.#####"

			make_product(product, product_args)

		bom_name = frappe.db.get_value(
			"BOM", {"product": fg_product, "is_active": 1, "with_operations": 1}, "name"
		)

		if not bom_name:
			bom = make_bom(product=fg_product, rate=1000, raw_materials=[rm1], do_not_save=True)
			bom.save()
			bom.submit()
			bom_name = bom.name

		ste1 = test_stock_entry.make_stock_entry(
			product_code=rm1, target="_Test Warehouse - _TC", qty=32, basic_rate=5000.0
		)

		work_order = make_wo_order_test_record(
			product=fg_product, skip_transfer=True, planned_start_date=now(), qty=1
		)
		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 1))
		for row in ste1.get("products"):
			if row.is_finished_product:
				self.assertEqual(row.product_code, fg_product)

		work_order = make_wo_order_test_record(
			product=fg_product, skip_transfer=True, planned_start_date=now(), qty=1
		)
		frappe.db.set_value("Manufacturing Settings", None, "make_serial_no_batch_from_work_order", 1)
		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 1))
		for row in ste1.get("products"):
			if row.is_finished_product:
				self.assertEqual(row.product_code, fg_product)

		work_order = make_wo_order_test_record(
			product=fg_product, skip_transfer=True, planned_start_date=now(), qty=30, do_not_save=True
		)
		work_order.batch_size = 10
		work_order.insert()
		work_order.submit()
		self.assertEqual(work_order.has_batch_no, 1)
		batches = frappe.get_all("Batch", filters={"reference_name": work_order.name})
		self.assertEqual(len(batches), 3)
		batches = [batch.name for batch in batches]

		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 10))
		for row in ste1.get("products"):
			if row.is_finished_product:
				self.assertEqual(row.product_code, fg_product)
				self.assertEqual(row.qty, 10)
				self.assertTrue(row.batch_no in batches)
				batches.remove(row.batch_no)

		ste1.submit()

		remaining_batches = []
		ste1 = frappe.get_doc(make_stock_entry(work_order.name, "Manufacture", 20))
		for row in ste1.get("products"):
			if row.is_finished_product:
				self.assertEqual(row.product_code, fg_product)
				self.assertEqual(row.qty, 10)
				remaining_batches.append(row.batch_no)

		self.assertEqual(sorted(remaining_batches), sorted(batches))

		frappe.db.set_value("Manufacturing Settings", None, "make_serial_no_batch_from_work_order", 0)

	def test_partial_material_consumption(self):
		frappe.db.set_value("Manufacturing Settings", None, "material_consumption", 1)
		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=4)

		ste_cancel_list = []
		ste1 = test_stock_entry.make_stock_entry(
			product_code="_Test Product", target="_Test Warehouse - _TC", qty=20, basic_rate=5000.0
		)
		ste2 = test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			target="_Test Warehouse - _TC",
			qty=20,
			basic_rate=1000.0,
		)

		ste_cancel_list.extend([ste1, ste2])

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 4))
		s.submit()
		ste_cancel_list.append(s)

		ste1 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 2))
		ste1.submit()
		ste_cancel_list.append(ste1)

		ste3 = frappe.get_doc(make_stock_entry(wo_order.name, "Material Consumption for Manufacture", 2))
		self.assertEqual(ste3.fg_completed_qty, 2)

		expected_qty = {"_Test Product": 2, "_Test Product Home Desktop 100": 4}
		for row in ste3.products:
			self.assertEqual(row.qty, expected_qty.get(row.product_code))
		ste_cancel_list.reverse()
		for ste_doc in ste_cancel_list:
			ste_doc.cancel()

		frappe.db.set_value("Manufacturing Settings", None, "material_consumption", 0)

	def test_extra_material_transfer(self):
		frappe.db.set_value("Manufacturing Settings", None, "material_consumption", 0)
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=4)

		ste_cancel_list = []
		ste1 = test_stock_entry.make_stock_entry(
			product_code="_Test Product", target="_Test Warehouse - _TC", qty=20, basic_rate=5000.0
		)
		ste2 = test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			target="_Test Warehouse - _TC",
			qty=20,
			basic_rate=1000.0,
		)

		ste_cancel_list.extend([ste1, ste2])

		productwise_qty = {}
		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 4))
		for row in s.products:
			row.qty = row.qty + 2
			productwise_qty.setdefault(row.product_code, row.qty)

		s.submit()
		ste_cancel_list.append(s)

		ste3 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 2))
		for ste_row in ste3.products:
			if productwise_qty.get(ste_row.product_code) and ste_row.s_warehouse:
				self.assertEqual(ste_row.qty, productwise_qty.get(ste_row.product_code) / 2)

		ste3.submit()
		ste_cancel_list.append(ste3)

		ste2 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 2))
		for ste_row in ste2.products:
			if productwise_qty.get(ste_row.product_code) and ste_row.s_warehouse:
				self.assertEqual(ste_row.qty, productwise_qty.get(ste_row.product_code) / 2)
		ste_cancel_list.reverse()
		for ste_doc in ste_cancel_list:
			ste_doc.cancel()

		frappe.db.set_value("Manufacturing Settings", None, "backflush_raw_materials_based_on", "BOM")

	def test_make_stock_entry_for_customer_provided_product(self):
		finished_product = "Test Product for Make Stock Entry 1"
		make_product(finished_product, {"include_product_in_manufacturing": 1, "is_stock_product": 1})

		customer_provided_product = "CUST-0987"
		make_product(
			customer_provided_product,
			{
				"is_purchase_product": 0,
				"is_customer_provided_product": 1,
				"is_stock_product": 1,
				"include_product_in_manufacturing": 1,
				"customer": "_Test Customer",
			},
		)

		if not frappe.db.exists("BOM", {"product": finished_product}):
			make_bom(product=finished_product, raw_materials=[customer_provided_product], rm_qty=1)

		company = "_Test Company with perpetual inventory"
		customer_warehouse = create_warehouse("Test Customer Provided Warehouse", company=company)
		wo = make_wo_order_test_record(
			product=finished_product, qty=1, source_warehouse=customer_warehouse, company=company
		)

		ste = frappe.get_doc(make_stock_entry(wo.name, purpose="Material Transfer for Manufacture"))
		ste.insert()

		self.assertEqual(len(ste.products), 1)
		for product in ste.products:
			self.assertEqual(product.allow_zero_valuation_rate, 1)
			self.assertEqual(product.valuation_rate, 0)

	def test_valuation_rate_missing_on_make_stock_entry(self):
		product_name = "Test Valuation Rate Missing"
		rm_product = "_Test raw material product"
		make_product(
			product_name,
			{
				"is_stock_product": 1,
				"include_product_in_manufacturing": 1,
			},
		)
		make_product(
			"_Test raw material product",
			{
				"is_stock_product": 1,
				"include_product_in_manufacturing": 1,
			},
		)

		if not frappe.db.get_value("BOM", {"product": product_name}):
			make_bom(product=product_name, raw_materials=[rm_product], rm_qty=1)

		company = "_Test Company with perpetual inventory"
		source_warehouse = create_warehouse("Test Valuation Rate Missing Warehouse", company=company)
		wo = make_wo_order_test_record(
			product=product_name, qty=1, source_warehouse=source_warehouse, company=company
		)

		stock_entry = frappe.get_doc(make_stock_entry(wo.name, "Material Transfer for Manufacture"))
		self.assertRaises(frappe.ValidationError, stock_entry.save)

	def test_wo_completion_with_pl_bom(self):
		from erpnext.manufacturing.doctype.bom.test_bom import (
			create_bom_with_process_loss_product,
			create_process_loss_bom_products,
		)

		qty = 10
		scrap_qty = 0.25  # bom product qty = 1, consider as 25% of FG
		source_warehouse = "Stores - _TC"
		wip_warehouse = "_Test Warehouse - _TC"
		fg_product_non_whole, _, bom_product = create_process_loss_bom_products()

		test_stock_entry.make_stock_entry(
			product_code=bom_product.product_code, target=source_warehouse, qty=qty, basic_rate=100
		)

		bom_no = f"BOM-{fg_product_non_whole.product_code}-001"
		if not frappe.db.exists("BOM", bom_no):
			bom_doc = create_bom_with_process_loss_product(
				fg_product_non_whole, bom_product, fg_qty=1, process_loss_percentage=10
			)
			bom_doc.submit()

		wo = make_wo_order_test_record(
			production_product=fg_product_non_whole.product_code,
			bom_no=bom_no,
			wip_warehouse=wip_warehouse,
			qty=qty,
			skip_transfer=1,
			stock_uom=fg_product_non_whole.stock_uom,
		)

		se = frappe.get_doc(make_stock_entry(wo.name, "Material Transfer for Manufacture", qty))
		se.get("products")[0].s_warehouse = "Stores - _TC"
		se.insert()
		se.submit()

		se = frappe.get_doc(make_stock_entry(wo.name, "Manufacture", qty))
		se.insert()
		se.submit()

		# Testing stock entry values
		products = se.get("products")
		self.assertEqual(len(products), 2, "There should be 3 products including process loss.")
		fg_product = products[1]

		self.assertEqual(fg_product.qty, qty - 1)
		self.assertEqual(se.process_loss_percentage, 10)
		self.assertEqual(se.process_loss_qty, 1)

		wo.load_from_db()
		self.assertEqual(wo.status, "Completed")

	@timeout(seconds=60)
	def test_job_card_scrap_product(self):
		products = [
			"Test FG Product for Scrap Product Test",
			"Test RM Product 1 for Scrap Product Test",
			"Test RM Product 2 for Scrap Product Test",
		]

		company = "_Test Company with perpetual inventory"
		for product_code in products:
			create_product(
				product_code=product_code,
				is_stock_product=1,
				is_purchase_product=1,
				opening_stock=100,
				valuation_rate=10,
				company=company,
				warehouse="Stores - TCP1",
			)

		product = "Test FG Product for Scrap Product Test"
		raw_materials = ["Test RM Product 1 for Scrap Product Test", "Test RM Product 2 for Scrap Product Test"]
		if not frappe.db.get_value("BOM", {"product": product}):
			bom = make_bom(
				product=product, source_warehouse="Stores - TCP1", raw_materials=raw_materials, do_not_save=True
			)
			bom.with_operations = 1
			bom.append(
				"operations",
				{
					"operation": "_Test Operation 1",
					"workstation": "_Test Workstation 1",
					"hour_rate": 20,
					"time_in_mins": 60,
				},
			)

			bom.submit()

		wo_order = make_wo_order_test_record(
			product=product, company=company, planned_start_date=now(), qty=20, skip_transfer=1
		)
		job_card = frappe.db.get_value("Job Card", {"work_order": wo_order.name}, "name")
		update_job_card(job_card)

		stock_entry = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 10))
		for row in stock_entry.products:
			if row.is_scrap_product:
				self.assertEqual(row.qty, 1)

		# Partial Job Card 1 with qty 10
		wo_order = make_wo_order_test_record(
			product=product, company=company, planned_start_date=add_days(now(), 60), qty=20, skip_transfer=1
		)
		job_card = frappe.db.get_value("Job Card", {"work_order": wo_order.name}, "name")
		update_job_card(job_card, 10)

		stock_entry = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 10))
		for row in stock_entry.products:
			if row.is_scrap_product:
				self.assertEqual(row.qty, 2)

		# Partial Job Card 2 with qty 10
		operations = []
		wo_order.load_from_db()
		for row in wo_order.operations:
			n_dict = row.as_dict()
			n_dict["qty"] = 10
			n_dict["pending_qty"] = 10
			operations.append(n_dict)

		make_job_card(wo_order.name, operations)
		job_card = frappe.db.get_value("Job Card", {"work_order": wo_order.name, "docstatus": 0}, "name")
		update_job_card(job_card, 10)

		stock_entry = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 10))
		for row in stock_entry.products:
			if row.is_scrap_product:
				self.assertEqual(row.qty, 2)

	def test_close_work_order(self):
		products = [
			"Test FG Product for Closed WO",
			"Test RM Product 1 for Closed WO",
			"Test RM Product 2 for Closed WO",
		]

		company = "_Test Company with perpetual inventory"
		for product_code in products:
			create_product(
				product_code=product_code,
				is_stock_product=1,
				is_purchase_product=1,
				opening_stock=100,
				valuation_rate=10,
				company=company,
				warehouse="Stores - TCP1",
			)

		product = "Test FG Product for Closed WO"
		raw_materials = ["Test RM Product 1 for Closed WO", "Test RM Product 2 for Closed WO"]
		if not frappe.db.get_value("BOM", {"product": product}):
			bom = make_bom(
				product=product, source_warehouse="Stores - TCP1", raw_materials=raw_materials, do_not_save=True
			)
			bom.with_operations = 1
			bom.append(
				"operations",
				{
					"operation": "_Test Operation 1",
					"workstation": "_Test Workstation 1",
					"hour_rate": 20,
					"time_in_mins": 60,
				},
			)

			bom.submit()

		wo_order = make_wo_order_test_record(
			product=product, company=company, planned_start_date=now(), qty=20, skip_transfer=1
		)
		job_cards = frappe.db.get_value("Job Card", {"work_order": wo_order.name}, "name")

		if len(job_cards) == len(bom.operations):
			for jc in job_cards:
				job_card_doc = frappe.get_doc("Job Card", jc)
				job_card_doc.append(
					"time_logs",
					{"from_time": now(), "time_in_mins": 60, "completed_qty": job_card_doc.for_quantity},
				)

				job_card_doc.submit()

			close_work_order(wo_order, "Closed")
			self.assertEqual(wo_order.get("status"), "Closed")

	def test_fix_time_operations(self):
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"product": "_Test FG Product 2",
				"is_active": 1,
				"is_default": 1,
				"quantity": 1.0,
				"with_operations": 1,
				"operations": [
					{
						"operation": "_Test Operation 1",
						"description": "_Test",
						"workstation": "_Test Workstation 1",
						"time_in_mins": 60,
						"operating_cost": 140,
						"fixed_time": 1,
					}
				],
				"products": [
					{
						"amount": 5000.0,
						"doctype": "BOM Product",
						"product_code": "_Test Product",
						"parentfield": "products",
						"qty": 1.0,
						"rate": 5000.0,
					},
				],
			}
		)
		bom.save()
		bom.submit()

		wo1 = make_wo_order_test_record(
			product=bom.product, bom_no=bom.name, qty=1, skip_transfer=1, do_not_submit=1
		)
		wo2 = make_wo_order_test_record(
			product=bom.product, bom_no=bom.name, qty=2, skip_transfer=1, do_not_submit=1
		)

		self.assertEqual(wo1.operations[0].time_in_mins, wo2.operations[0].time_in_mins)

	def test_partial_manufacture_entries(self):
		cancel_stock_entry = []

		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=100)
		ste1 = test_stock_entry.make_stock_entry(
			product_code="_Test Product", target="_Test Warehouse - _TC", qty=120, basic_rate=5000.0
		)
		ste2 = test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			target="_Test Warehouse - _TC",
			qty=240,
			basic_rate=1000.0,
		)

		cancel_stock_entry.extend([ste1.name, ste2.name])

		sm = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 100))
		for row in sm.get("products"):
			if row.get("product_code") == "_Test Product":
				row.qty = 120

		sm.submit()
		cancel_stock_entry.append(sm.name)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 90))
		for row in s.get("products"):
			if row.get("product_code") == "_Test Product":
				self.assertEqual(row.get("qty"), 108)
		s.submit()
		cancel_stock_entry.append(s.name)

		s1 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 5))
		for row in s1.get("products"):
			if row.get("product_code") == "_Test Product":
				self.assertEqual(row.get("qty"), 6)
		s1.submit()
		cancel_stock_entry.append(s1.name)

		s2 = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 5))
		for row in s2.get("products"):
			if row.get("product_code") == "_Test Product":
				self.assertEqual(row.get("qty"), 6)

		cancel_stock_entry.reverse()
		for ste in cancel_stock_entry:
			doc = frappe.get_doc("Stock Entry", ste)
			doc.cancel()

		frappe.db.set_value("Manufacturing Settings", None, "backflush_raw_materials_based_on", "BOM")

	@change_settings("Manufacturing Settings", {"make_serial_no_batch_from_work_order": 1})
	def test_auto_batch_creation(self):
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		fg_product = frappe.generate_hash(length=20)
		child_product = frappe.generate_hash(length=20)

		bom_tree = {fg_product: {child_product: {}}}

		create_nested_bom(bom_tree, prefix="")

		product = frappe.get_doc("Product", fg_product)
		product.has_batch_no = 1
		product.create_new_batch = 0
		product.save()

		try:
			make_wo_order_test_record(product=fg_product)
		except frappe.MandatoryError:
			self.fail("Batch generation causing failing in Work Order")

	@change_settings("Manufacturing Settings", {"make_serial_no_batch_from_work_order": 1})
	def test_auto_serial_no_creation(self):
		from erpnext.manufacturing.doctype.bom.test_bom import create_nested_bom

		fg_product = frappe.generate_hash(length=20)
		child_product = frappe.generate_hash(length=20)

		bom_tree = {fg_product: {child_product: {}}}

		create_nested_bom(bom_tree, prefix="")

		product = frappe.get_doc("Product", fg_product)
		product.has_serial_no = 1
		product.serial_no_series = f"{product.name}.#####"
		product.save()

		try:
			wo_order = make_wo_order_test_record(product=fg_product, qty=2, skip_transfer=True)
			serial_nos = wo_order.serial_no
			stock_entry = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 10))
			stock_entry.set_work_order_details()
			stock_entry.set_serial_no_batch_for_finished_good()
			for row in stock_entry.products:
				if row.product_code == fg_product:
					self.assertTrue(row.serial_no)
					self.assertEqual(sorted(get_serial_nos(row.serial_no)), sorted(get_serial_nos(serial_nos)))

		except frappe.MandatoryError:
			self.fail("Batch generation causing failing in Work Order")

	@change_settings(
		"Manufacturing Settings",
		{"backflush_raw_materials_based_on": "Material Transferred for Manufacture"},
	)
	def test_manufacture_entry_mapped_idx_with_exploded_bom(self):
		"""Test if WO containing BOM with partial exploded products and scrap products, maps idx correctly."""
		test_stock_entry.make_stock_entry(
			product_code="_Test Product",
			target="_Test Warehouse - _TC",
			basic_rate=5000.0,
			qty=2,
		)
		test_stock_entry.make_stock_entry(
			product_code="_Test Product Home Desktop 100",
			target="_Test Warehouse - _TC",
			basic_rate=1000.0,
			qty=2,
		)

		wo_order = make_wo_order_test_record(
			qty=1,
			use_multi_level_bom=1,
			skip_transfer=1,
		)

		ste_manu = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 1))

		for index, row in enumerate(ste_manu.get("products"), start=1):
			self.assertEqual(index, row.idx)

	@change_settings(
		"Manufacturing Settings",
		{"backflush_raw_materials_based_on": "Material Transferred for Manufacture"},
	)
	def test_work_order_multiple_material_transfer(self):
		"""
		Test transferring multiple RMs in separate Stock Entries.
		"""
		work_order = make_wo_order_test_record(planned_start_date=now(), qty=1)
		test_stock_entry.make_stock_entry(  # stock up RM
			product_code="_Test Product",
			target="_Test Warehouse - _TC",
			qty=1,
			basic_rate=5000.0,
		)
		test_stock_entry.make_stock_entry(  # stock up RM
			product_code="_Test Product Home Desktop 100",
			target="_Test Warehouse - _TC",
			qty=2,
			basic_rate=1000.0,
		)

		transfer_entry = frappe.get_doc(
			make_stock_entry(work_order.name, "Material Transfer for Manufacture", 1)
		)
		del transfer_entry.get("products")[0]  # transfer only one RM
		transfer_entry.submit()

		# WO's "Material Transferred for Mfg" shows all is transferred, one RM is pending
		work_order.reload()
		self.assertEqual(work_order.material_transferred_for_manufacturing, 1)
		self.assertEqual(work_order.required_products[0].transferred_qty, 0)
		self.assertEqual(work_order.required_products[1].transferred_qty, 2)

		final_transfer_entry = frappe.get_doc(  # transfer last RM with For Quantity = 0
			make_stock_entry(work_order.name, "Material Transfer for Manufacture", 0)
		)
		final_transfer_entry.save()

		self.assertEqual(final_transfer_entry.fg_completed_qty, 0.0)
		self.assertEqual(final_transfer_entry.products[0].qty, 1)

		final_transfer_entry.submit()
		work_order.reload()

		# WO's "Material Transferred for Mfg" shows all is transferred, no RM is pending
		self.assertEqual(work_order.material_transferred_for_manufacturing, 1)
		self.assertEqual(work_order.required_products[0].transferred_qty, 1)
		self.assertEqual(work_order.required_products[1].transferred_qty, 2)

	def test_backflushed_batch_raw_materials_based_on_transferred(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		batch_product = "Test Batch MCC Keyboard"
		fg_product = "Test FG Product with Batch Raw Materials"

		ste_doc = test_stock_entry.make_stock_entry(
			product_code=batch_product, target="Stores - _TC", qty=2, basic_rate=100, do_not_save=True
		)

		ste_doc.append(
			"products",
			{
				"product_code": batch_product,
				"product_name": batch_product,
				"description": batch_product,
				"basic_rate": 100,
				"t_warehouse": "Stores - _TC",
				"qty": 2,
				"uom": "Nos",
				"stock_uom": "Nos",
				"conversion_factor": 1,
			},
		)

		# Inward raw materials in Stores warehouse
		ste_doc.insert()
		ste_doc.submit()

		batch_list = sorted([row.batch_no for row in ste_doc.products])

		wo_doc = make_wo_order_test_record(production_product=fg_product, qty=4)
		transferred_ste_doc = frappe.get_doc(
			make_stock_entry(wo_doc.name, "Material Transfer for Manufacture", 4)
		)

		transferred_ste_doc.products[0].qty = 2
		transferred_ste_doc.products[0].batch_no = batch_list[0]

		new_row = copy.deepcopy(transferred_ste_doc.products[0])
		new_row.name = ""
		new_row.batch_no = batch_list[1]

		# Transferred two batches from Stores to WIP Warehouse
		transferred_ste_doc.append("products", new_row)
		transferred_ste_doc.submit()

		# First Manufacture stock entry
		manufacture_ste_doc1 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 1))

		# Batch no should be same as transferred Batch no
		self.assertEqual(manufacture_ste_doc1.products[0].batch_no, batch_list[0])
		self.assertEqual(manufacture_ste_doc1.products[0].qty, 1)

		manufacture_ste_doc1.submit()

		# Second Manufacture stock entry
		manufacture_ste_doc2 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 2))

		# Batch no should be same as transferred Batch no
		self.assertEqual(manufacture_ste_doc2.products[0].batch_no, batch_list[0])
		self.assertEqual(manufacture_ste_doc2.products[0].qty, 1)
		self.assertEqual(manufacture_ste_doc2.products[1].batch_no, batch_list[1])
		self.assertEqual(manufacture_ste_doc2.products[1].qty, 1)

	def test_backflushed_serial_no_raw_materials_based_on_transferred(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		sn_product = "Test Serial No BTT Headphone"
		fg_product = "Test FG Product with Serial No Raw Materials"

		ste_doc = test_stock_entry.make_stock_entry(
			product_code=sn_product, target="Stores - _TC", qty=4, basic_rate=100, do_not_save=True
		)

		# Inward raw materials in Stores warehouse
		ste_doc.submit()

		serial_nos_list = sorted(get_serial_nos(ste_doc.products[0].serial_no))

		wo_doc = make_wo_order_test_record(production_product=fg_product, qty=4)
		transferred_ste_doc = frappe.get_doc(
			make_stock_entry(wo_doc.name, "Material Transfer for Manufacture", 4)
		)

		transferred_ste_doc.products[0].serial_no = "\n".join(serial_nos_list)
		transferred_ste_doc.submit()

		# First Manufacture stock entry
		manufacture_ste_doc1 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 1))

		# Serial nos should be same as transferred Serial nos
		self.assertEqual(get_serial_nos(manufacture_ste_doc1.products[0].serial_no), serial_nos_list[0:1])
		self.assertEqual(manufacture_ste_doc1.products[0].qty, 1)

		manufacture_ste_doc1.submit()

		# Second Manufacture stock entry
		manufacture_ste_doc2 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 2))

		# Serial nos should be same as transferred Serial nos
		self.assertEqual(get_serial_nos(manufacture_ste_doc2.products[0].serial_no), serial_nos_list[1:3])
		self.assertEqual(manufacture_ste_doc2.products[0].qty, 2)

	def test_backflushed_serial_no_batch_raw_materials_based_on_transferred(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		sn_batch_product = "Test Batch Serial No WebCam"
		fg_product = "Test FG Product with Serial & Batch No Raw Materials"

		ste_doc = test_stock_entry.make_stock_entry(
			product_code=sn_batch_product, target="Stores - _TC", qty=2, basic_rate=100, do_not_save=True
		)

		ste_doc.append(
			"products",
			{
				"product_code": sn_batch_product,
				"product_name": sn_batch_product,
				"description": sn_batch_product,
				"basic_rate": 100,
				"t_warehouse": "Stores - _TC",
				"qty": 2,
				"uom": "Nos",
				"stock_uom": "Nos",
				"conversion_factor": 1,
			},
		)

		# Inward raw materials in Stores warehouse
		ste_doc.insert()
		ste_doc.submit()

		batch_dict = {row.batch_no: get_serial_nos(row.serial_no) for row in ste_doc.products}
		batches = list(batch_dict.keys())

		wo_doc = make_wo_order_test_record(production_product=fg_product, qty=4)
		transferred_ste_doc = frappe.get_doc(
			make_stock_entry(wo_doc.name, "Material Transfer for Manufacture", 4)
		)

		transferred_ste_doc.products[0].qty = 2
		transferred_ste_doc.products[0].batch_no = batches[0]
		transferred_ste_doc.products[0].serial_no = "\n".join(batch_dict.get(batches[0]))

		new_row = copy.deepcopy(transferred_ste_doc.products[0])
		new_row.name = ""
		new_row.batch_no = batches[1]
		new_row.serial_no = "\n".join(batch_dict.get(batches[1]))

		# Transferred two batches from Stores to WIP Warehouse
		transferred_ste_doc.append("products", new_row)
		transferred_ste_doc.submit()

		# First Manufacture stock entry
		manufacture_ste_doc1 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 1))

		# Batch no & Serial Nos should be same as transferred Batch no & Serial Nos
		batch_no = manufacture_ste_doc1.products[0].batch_no
		self.assertEqual(
			get_serial_nos(manufacture_ste_doc1.products[0].serial_no)[0], batch_dict.get(batch_no)[0]
		)
		self.assertEqual(manufacture_ste_doc1.products[0].qty, 1)

		manufacture_ste_doc1.submit()

		# Second Manufacture stock entry
		manufacture_ste_doc2 = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 2))

		# Batch no & Serial Nos should be same as transferred Batch no & Serial Nos
		batch_no = manufacture_ste_doc2.products[0].batch_no
		self.assertEqual(
			get_serial_nos(manufacture_ste_doc2.products[0].serial_no)[0], batch_dict.get(batch_no)[1]
		)
		self.assertEqual(manufacture_ste_doc2.products[0].qty, 1)

		batch_no = manufacture_ste_doc2.products[1].batch_no
		self.assertEqual(
			get_serial_nos(manufacture_ste_doc2.products[1].serial_no)[0], batch_dict.get(batch_no)[0]
		)
		self.assertEqual(manufacture_ste_doc2.products[1].qty, 1)

	def test_non_consumed_material_return_against_work_order(self):
		frappe.db.set_value(
			"Manufacturing Settings",
			None,
			"backflush_raw_materials_based_on",
			"Material Transferred for Manufacture",
		)

		product = make_product(
			"Test FG Product To Test Return Case",
			{
				"is_stock_product": 1,
			},
		)

		product_code = product.name
		bom_doc = make_bom(
			product=product_code,
			source_warehouse="Stores - _TC",
			raw_materials=["Test Batch MCC Keyboard", "Test Serial No BTT Headphone"],
		)

		# Create a work order
		wo_doc = make_wo_order_test_record(production_product=product_code, qty=5)
		wo_doc.save()

		self.assertEqual(wo_doc.bom_no, bom_doc.name)

		# Transfer material for manufacture
		ste_doc = frappe.get_doc(make_stock_entry(wo_doc.name, "Material Transfer for Manufacture", 5))
		for row in ste_doc.products:
			row.qty += 2
			row.transfer_qty += 2
			nste_doc = test_stock_entry.make_stock_entry(
				product_code=row.product_code, target="Stores - _TC", qty=row.qty, basic_rate=100
			)

			row.batch_no = nste_doc.products[0].batch_no
			row.serial_no = nste_doc.products[0].serial_no

		ste_doc.save()
		ste_doc.submit()
		ste_doc.load_from_db()

		# Create a stock entry to manufacture the product
		ste_doc = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 5))
		for row in ste_doc.products:
			if row.s_warehouse and not row.t_warehouse:
				row.qty -= 2
				row.transfer_qty -= 2

				if row.serial_no:
					serial_nos = get_serial_nos(row.serial_no)
					row.serial_no = "\n".join(serial_nos[0:5])

		ste_doc.save()
		ste_doc.submit()

		wo_doc.load_from_db()
		for row in wo_doc.required_products:
			self.assertEqual(row.transferred_qty, 7)
			self.assertEqual(row.consumed_qty, 5)

		self.assertEqual(wo_doc.status, "Completed")
		return_ste_doc = make_stock_return_entry(wo_doc.name)
		return_ste_doc.save()

		self.assertTrue(return_ste_doc.is_return)
		for row in return_ste_doc.products:
			self.assertEqual(row.qty, 2)

	def test_workstation_type_for_work_order(self):
		prepare_data_for_workstation_type_check()

		workstation_types = ["Workstation Type 1", "Workstation Type 2", "Workstation Type 3"]
		planned_start_date = "2022-11-14 10:00:00"

		wo_order = make_wo_order_test_record(
			product="Test FG Product For Workstation Type", planned_start_date=planned_start_date, qty=2
		)

		job_cards = frappe.get_all(
			"Job Card",
			fields=[
				"`tabJob Card`.`name`",
				"`tabJob Card`.`workstation_type`",
				"`tabJob Card`.`workstation`",
				"`tabJob Card Time Log`.`from_time`",
				"`tabJob Card Time Log`.`to_time`",
				"`tabJob Card Time Log`.`time_in_mins`",
			],
			filters=[
				["Job Card", "work_order", "=", wo_order.name],
				["Job Card Time Log", "docstatus", "=", 1],
			],
			order_by="`tabJob Card`.`creation` desc",
		)

		workstations_to_check = ["Workstation 1", "Workstation 3", "Workstation 5"]
		for index, row in enumerate(job_cards):
			if index != 0:
				planned_start_date = add_to_date(planned_start_date, minutes=40)

			self.assertEqual(row.workstation_type, workstation_types[index])
			self.assertEqual(row.from_time, planned_start_date)
			self.assertEqual(row.to_time, add_to_date(planned_start_date, minutes=30))
			self.assertEqual(row.workstation, workstations_to_check[index])

		planned_start_date = "2022-11-14 10:00:00"

		wo_order = make_wo_order_test_record(
			product="Test FG Product For Workstation Type", planned_start_date=planned_start_date, qty=2
		)

		job_cards = frappe.get_all(
			"Job Card",
			fields=[
				"`tabJob Card`.`name`",
				"`tabJob Card`.`workstation_type`",
				"`tabJob Card`.`workstation`",
				"`tabJob Card Time Log`.`from_time`",
				"`tabJob Card Time Log`.`to_time`",
				"`tabJob Card Time Log`.`time_in_mins`",
			],
			filters=[
				["Job Card", "work_order", "=", wo_order.name],
				["Job Card Time Log", "docstatus", "=", 1],
			],
			order_by="`tabJob Card`.`creation` desc",
		)

		workstations_to_check = ["Workstation 2", "Workstation 4", "Workstation 6"]
		for index, row in enumerate(job_cards):
			if index != 0:
				planned_start_date = add_to_date(planned_start_date, minutes=40)

			self.assertEqual(row.workstation_type, workstation_types[index])
			self.assertEqual(row.from_time, planned_start_date)
			self.assertEqual(row.to_time, add_to_date(planned_start_date, minutes=30))
			self.assertEqual(row.workstation, workstations_to_check[index])

	def test_job_card_extra_qty(self):
		products = [
			"Test FG Product for Scrap Product Test 1",
			"Test RM Product 1 for Scrap Product Test 1",
			"Test RM Product 2 for Scrap Product Test 1",
		]

		company = "_Test Company with perpetual inventory"
		for product_code in products:
			create_product(
				product_code=product_code,
				is_stock_product=1,
				is_purchase_product=1,
				opening_stock=100,
				valuation_rate=10,
				company=company,
				warehouse="Stores - TCP1",
			)

		product = "Test FG Product for Scrap Product Test 1"
		raw_materials = ["Test RM Product 1 for Scrap Product Test 1", "Test RM Product 2 for Scrap Product Test 1"]
		if not frappe.db.get_value("BOM", {"product": product}):
			bom = make_bom(
				product=product, source_warehouse="Stores - TCP1", raw_materials=raw_materials, do_not_save=True
			)
			bom.with_operations = 1
			bom.append(
				"operations",
				{
					"operation": "_Test Operation 1",
					"workstation": "_Test Workstation 1",
					"hour_rate": 20,
					"time_in_mins": 60,
				},
			)

			bom.submit()

		wo_order = make_wo_order_test_record(
			product=product,
			company=company,
			planned_start_date=now(),
			qty=20,
		)
		job_card = frappe.db.get_value("Job Card", {"work_order": wo_order.name}, "name")
		job_card_doc = frappe.get_doc("Job Card", job_card)

		# Make another Job Card for the same Work Order
		job_card2 = frappe.copy_doc(job_card_doc)
		self.assertRaises(frappe.ValidationError, job_card2.save)

		frappe.db.set_single_value(
			"Manufacturing Settings", "overproduction_percentage_for_work_order", 100
		)

		job_card2 = frappe.copy_doc(job_card_doc)
		job_card2.time_logs = []
		job_card2.save()

	def test_make_serial_no_batch_from_work_order_for_serial_no(self):
		product_code = "Test Serial No Product For Work Order"
		warehouse = "_Test Warehouse - _TC"
		raw_materials = [
			"Test RM Product 1 for Serial No Product In Work Order",
		]

		make_product(
			product_code,
			{
				"has_stock_product": 1,
				"has_serial_no": 1,
				"serial_no_series": "TSNIFWO-.#####",
			},
		)

		for rm_product in raw_materials:
			make_product(
				rm_product,
				{
					"has_stock_product": 1,
				},
			)

			test_stock_entry.make_stock_entry(product_code=rm_product, target=warehouse, qty=10, basic_rate=100)

		bom = make_bom(product=product_code, raw_materials=raw_materials)

		frappe.db.set_single_value("Manufacturing Settings", "make_serial_no_batch_from_work_order", 1)

		wo_order = make_wo_order_test_record(
			product=product_code,
			bom_no=bom.name,
			qty=5,
			skip_transfer=1,
			from_wip_warehouse=1,
		)

		serial_nos = frappe.get_all(
			"Serial No",
			filters={"product_code": product_code, "work_order": wo_order.name},
		)

		serial_nos = [d.name for d in serial_nos]
		self.assertEqual(len(serial_nos), 5)

		stock_entry = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 5))

		stock_entry.submit()
		for row in stock_entry.products:
			if row.is_finished_product:
				self.assertEqual(sorted(get_serial_nos(row.serial_no)), sorted(get_serial_nos(serial_nos)))

		frappe.db.set_single_value("Manufacturing Settings", "make_serial_no_batch_from_work_order", 0)


def prepare_data_for_workstation_type_check():
	from erpnext.manufacturing.doctype.operation.test_operation import make_operation
	from erpnext.manufacturing.doctype.workstation.test_workstation import make_workstation
	from erpnext.manufacturing.doctype.workstation_type.test_workstation_type import (
		create_workstation_type,
	)

	workstation_types = ["Workstation Type 1", "Workstation Type 2", "Workstation Type 3"]
	for workstation_type in workstation_types:
		create_workstation_type(workstation_type=workstation_type)

	operations = ["Cutting", "Sewing", "Packing"]
	for operation in operations:
		make_operation(
			{
				"operation": operation,
			}
		)

	workstations = [
		{
			"workstation": "Workstation 1",
			"workstation_type": "Workstation Type 1",
		},
		{
			"workstation": "Workstation 2",
			"workstation_type": "Workstation Type 1",
		},
		{
			"workstation": "Workstation 3",
			"workstation_type": "Workstation Type 2",
		},
		{
			"workstation": "Workstation 4",
			"workstation_type": "Workstation Type 2",
		},
		{
			"workstation": "Workstation 5",
			"workstation_type": "Workstation Type 3",
		},
		{
			"workstation": "Workstation 6",
			"workstation_type": "Workstation Type 3",
		},
	]

	for row in workstations:
		make_workstation(row)

	fg_product = make_product(
		"Test FG Product For Workstation Type",
		{
			"is_stock_product": 1,
		},
	)

	rm_product = make_product(
		"Test RM Product For Workstation Type",
		{
			"is_stock_product": 1,
		},
	)

	if not frappe.db.exists("BOM", {"product": fg_product.name}):
		bom_doc = make_bom(
			product=fg_product.name,
			source_warehouse="Stores - _TC",
			raw_materials=[rm_product.name],
			do_not_submit=True,
		)

		submit_bom = False
		for index, operation in enumerate(operations):
			if not frappe.db.exists("BOM Operation", {"parent": bom_doc.name, "operation": operation}):
				bom_doc.append(
					"operations",
					{
						"operation": operation,
						"time_in_mins": 30,
						"hour_rate": 100,
						"workstation_type": workstation_types[index],
					},
				)

				submit_bom = True

		if submit_bom:
			bom_doc.submit()


def prepare_data_for_backflush_based_on_materials_transferred():
	batch_product_doc = make_product(
		"Test Batch MCC Keyboard",
		{
			"is_stock_product": 1,
			"has_batch_no": 1,
			"create_new_batch": 1,
			"batch_number_series": "TBMK.#####",
			"valuation_rate": 100,
			"stock_uom": "Nos",
		},
	)

	product = make_product(
		"Test FG Product with Batch Raw Materials",
		{
			"is_stock_product": 1,
		},
	)

	make_bom(product=product.name, source_warehouse="Stores - _TC", raw_materials=[batch_product_doc.name])

	sn_product_doc = make_product(
		"Test Serial No BTT Headphone",
		{
			"is_stock_product": 1,
			"has_serial_no": 1,
			"serial_no_series": "TSBH.#####",
			"valuation_rate": 100,
			"stock_uom": "Nos",
		},
	)

	product = make_product(
		"Test FG Product with Serial No Raw Materials",
		{
			"is_stock_product": 1,
		},
	)

	make_bom(product=product.name, source_warehouse="Stores - _TC", raw_materials=[sn_product_doc.name])

	sn_batch_product_doc = make_product(
		"Test Batch Serial No WebCam",
		{
			"is_stock_product": 1,
			"has_batch_no": 1,
			"create_new_batch": 1,
			"batch_number_series": "TBSW.#####",
			"has_serial_no": 1,
			"serial_no_series": "TBSWC.#####",
			"valuation_rate": 100,
			"stock_uom": "Nos",
		},
	)

	product = make_product(
		"Test FG Product with Serial & Batch No Raw Materials",
		{
			"is_stock_product": 1,
		},
	)

	make_bom(product=product.name, source_warehouse="Stores - _TC", raw_materials=[sn_batch_product_doc.name])


def update_job_card(job_card, jc_qty=None):
	employee = frappe.db.get_value("Employee", {"status": "Active"}, "name")
	job_card_doc = frappe.get_doc("Job Card", job_card)
	job_card_doc.set(
		"scrap_products",
		[
			{"product_code": "Test RM Product 1 for Scrap Product Test", "stock_qty": 2},
			{"product_code": "Test RM Product 2 for Scrap Product Test", "stock_qty": 2},
		],
	)

	if jc_qty:
		job_card_doc.for_quantity = jc_qty

	job_card_doc.append(
		"time_logs",
		{
			"from_time": now(),
			"employee": employee,
			"time_in_mins": 60,
			"completed_qty": job_card_doc.for_quantity,
		},
	)

	job_card_doc.submit()


def get_scrap_product_details(bom_no):
	scrap_products = {}
	for product in frappe.db.sql(
		"""select product_code, stock_qty from `tabBOM Scrap Product`
		where parent = %s""",
		bom_no,
		as_dict=1,
	):
		scrap_products[product.product_code] = product.stock_qty

	return scrap_products


def allow_overproduction(fieldname, percentage):
	doc = frappe.get_doc("Manufacturing Settings")
	doc.update({fieldname: percentage})
	doc.save()


def make_wo_order_test_record(**args):
	args = frappe._dict(args)
	if args.company and args.company != "_Test Company":
		warehouse_map = {"fg_warehouse": "_Test FG Warehouse", "wip_warehouse": "_Test WIP Warehouse"}

		for attr, wh_name in warehouse_map.products():
			if not args.get(attr):
				args[attr] = create_warehouse(wh_name, company=args.company)

	wo_order = frappe.new_doc("Work Order")
	wo_order.production_product = args.production_product or args.product or args.product_code or "_Test FG Product"
	wo_order.bom_no = args.bom_no or frappe.db.get_value(
		"BOM", {"product": wo_order.production_product, "is_active": 1, "is_default": 1}
	)
	wo_order.qty = args.qty or 10
	wo_order.wip_warehouse = args.wip_warehouse or "_Test Warehouse - _TC"
	wo_order.fg_warehouse = args.fg_warehouse or "_Test Warehouse 1 - _TC"
	wo_order.scrap_warehouse = args.fg_warehouse or "_Test Scrap Warehouse - _TC"
	wo_order.company = args.company or "_Test Company"
	wo_order.stock_uom = args.stock_uom or "_Test UOM"
	wo_order.use_multi_level_bom = args.use_multi_level_bom or 0
	wo_order.skip_transfer = args.skip_transfer or 0
	wo_order.get_products_and_operations_from_bom()
	wo_order.sales_order = args.sales_order or None
	wo_order.planned_start_date = args.planned_start_date or now()
	wo_order.transfer_material_against = args.transfer_material_against or "Work Order"
	wo_order.from_wip_warehouse = args.from_wip_warehouse or None

	if args.source_warehouse:
		for product in wo_order.get("required_products"):
			product.source_warehouse = args.source_warehouse

	if not args.do_not_save:
		wo_order.insert()

		if not args.do_not_submit:
			wo_order.submit()
	return wo_order


test_records = frappe.get_test_records("Work Order")
