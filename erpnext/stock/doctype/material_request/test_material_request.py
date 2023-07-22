# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

# ERPNext - web based ERP (http://erpnext.com)
# For license information, please see license.txt


import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from erpnext.stock.doctype.product.test_product import create_product
from erpnext.stock.doctype.material_request.material_request import (
	make_in_transit_stock_entry,
	make_purchase_order,
	make_stock_entry,
	make_supplier_quotation,
	raise_work_orders,
)


class TestMaterialRequest(FrappeTestCase):
	def test_make_purchase_order(self):
		mr = frappe.copy_doc(test_records[0]).insert()

		self.assertRaises(frappe.ValidationError, make_purchase_order, mr.name)

		mr = frappe.get_doc("Material Request", mr.name)
		mr.submit()
		po = make_purchase_order(mr.name)

		self.assertEqual(po.doctype, "Purchase Order")
		self.assertEqual(len(po.get("products")), len(mr.get("products")))

	def test_make_supplier_quotation(self):
		mr = frappe.copy_doc(test_records[0]).insert()

		self.assertRaises(frappe.ValidationError, make_supplier_quotation, mr.name)

		mr = frappe.get_doc("Material Request", mr.name)
		mr.submit()
		sq = make_supplier_quotation(mr.name)

		self.assertEqual(sq.doctype, "Supplier Quotation")
		self.assertEqual(len(sq.get("products")), len(mr.get("products")))

	def test_make_stock_entry(self):
		mr = frappe.copy_doc(test_records[0]).insert()

		self.assertRaises(frappe.ValidationError, make_stock_entry, mr.name)

		mr = frappe.get_doc("Material Request", mr.name)
		mr.material_request_type = "Material Transfer"
		mr.submit()
		se = make_stock_entry(mr.name)

		self.assertEqual(se.stock_entry_type, "Material Transfer")
		self.assertEqual(se.purpose, "Material Transfer")
		self.assertEqual(se.doctype, "Stock Entry")
		self.assertEqual(len(se.get("products")), len(mr.get("products")))

	def test_in_transit_make_stock_entry(self):
		mr = frappe.copy_doc(test_records[0]).insert()

		self.assertRaises(frappe.ValidationError, make_stock_entry, mr.name)

		mr = frappe.get_doc("Material Request", mr.name)
		mr.material_request_type = "Material Transfer"
		mr.submit()

		in_transit_warehouse = get_in_transit_warehouse(mr.company)
		se = make_in_transit_stock_entry(mr.name, in_transit_warehouse)

		self.assertEqual(se.stock_entry_type, "Material Transfer")
		self.assertEqual(se.purpose, "Material Transfer")
		self.assertEqual(se.doctype, "Stock Entry")
		for row in se.get("products"):
			self.assertEqual(row.t_warehouse, in_transit_warehouse)

	def _insert_stock_entry(self, qty1, qty2, warehouse=None):
		se = frappe.get_doc(
			{
				"company": "_Test Company",
				"doctype": "Stock Entry",
				"posting_date": "2013-03-01",
				"posting_time": "00:00:00",
				"purpose": "Material Receipt",
				"products": [
					{
						"conversion_factor": 1.0,
						"doctype": "Stock Entry Detail",
						"product_code": "_Test Product Home Desktop 100",
						"parentfield": "products",
						"basic_rate": 100,
						"qty": qty1,
						"stock_uom": "_Test UOM 1",
						"transfer_qty": qty1,
						"uom": "_Test UOM 1",
						"t_warehouse": warehouse or "_Test Warehouse 1 - _TC",
						"cost_center": "_Test Cost Center - _TC",
					},
					{
						"conversion_factor": 1.0,
						"doctype": "Stock Entry Detail",
						"product_code": "_Test Product Home Desktop 200",
						"parentfield": "products",
						"basic_rate": 100,
						"qty": qty2,
						"stock_uom": "_Test UOM 1",
						"transfer_qty": qty2,
						"uom": "_Test UOM 1",
						"t_warehouse": warehouse or "_Test Warehouse 1 - _TC",
						"cost_center": "_Test Cost Center - _TC",
					},
				],
			}
		)

		se.set_stock_entry_type()
		se.insert()
		se.submit()

	def test_cannot_stop_cancelled_material_request(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()

		mr.load_from_db()
		mr.cancel()
		self.assertRaises(frappe.ValidationError, mr.update_status, "Stopped")

	def test_mr_changes_from_stopped_to_pending_after_reopen(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()
		self.assertEqual("Pending", mr.status)

		mr.update_status("Stopped")
		self.assertEqual("Stopped", mr.status)

		mr.update_status("Submitted")
		self.assertEqual("Pending", mr.status)

	def test_cannot_submit_cancelled_mr(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()
		mr.load_from_db()
		mr.cancel()
		self.assertRaises(frappe.ValidationError, mr.submit)

	def test_mr_changes_from_pending_to_cancelled_after_cancel(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()
		mr.cancel()
		self.assertEqual("Cancelled", mr.status)

	def test_cannot_change_cancelled_mr(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()
		mr.load_from_db()
		mr.cancel()

		self.assertRaises(frappe.InvalidStatusError, mr.update_status, "Draft")
		self.assertRaises(frappe.InvalidStatusError, mr.update_status, "Stopped")
		self.assertRaises(frappe.InvalidStatusError, mr.update_status, "Ordered")
		self.assertRaises(frappe.InvalidStatusError, mr.update_status, "Issued")
		self.assertRaises(frappe.InvalidStatusError, mr.update_status, "Transferred")
		self.assertRaises(frappe.InvalidStatusError, mr.update_status, "Pending")

	def test_cannot_submit_deleted_material_request(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.delete()

		self.assertRaises(frappe.ValidationError, mr.submit)

	def test_cannot_delete_submitted_mr(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()

		self.assertRaises(frappe.ValidationError, mr.delete)

	def test_stopped_mr_changes_to_pending_after_reopen(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()
		mr.load_from_db()

		mr.update_status("Stopped")
		mr.update_status("Submitted")
		self.assertEqual(mr.status, "Pending")

	def test_pending_mr_changes_to_stopped_after_stop(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()
		mr.load_from_db()

		mr.update_status("Stopped")
		self.assertEqual(mr.status, "Stopped")

	def test_cannot_stop_unsubmitted_mr(self):
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		self.assertRaises(frappe.InvalidStatusError, mr.update_status, "Stopped")

	def test_completed_qty_for_purchase(self):
		existing_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		existing_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		# submit material request of type Purchase
		mr = frappe.copy_doc(test_records[0])
		mr.insert()
		mr.submit()

		# map a purchase order
		po_doc = make_purchase_order(mr.name)
		po_doc.supplier = "_Test Supplier"
		po_doc.transaction_date = "2013-07-07"
		po_doc.schedule_date = "2013-07-09"
		po_doc.get("products")[0].qty = 27.0
		po_doc.get("products")[1].qty = 1.5
		po_doc.get("products")[0].schedule_date = "2013-07-09"
		po_doc.get("products")[1].schedule_date = "2013-07-09"

		# check for stopped status of Material Request
		po = frappe.copy_doc(po_doc)
		po.insert()
		po.load_from_db()
		mr.update_status("Stopped")
		self.assertRaises(frappe.InvalidStatusError, po.submit)
		po.db_set("docstatus", 1)
		self.assertRaises(frappe.InvalidStatusError, po.cancel)

		# resubmit and check for per complete
		mr.load_from_db()
		mr.update_status("Submitted")
		po = frappe.copy_doc(po_doc)
		po.insert()
		po.submit()

		# check if per complete is as expected
		mr.load_from_db()
		self.assertEqual(mr.per_ordered, 50)
		self.assertEqual(mr.get("products")[0].ordered_qty, 27.0)
		self.assertEqual(mr.get("products")[1].ordered_qty, 1.5)

		current_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		current_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		self.assertEqual(current_requested_qty_product1, existing_requested_qty_product1 + 27.0)
		self.assertEqual(current_requested_qty_product2, existing_requested_qty_product2 + 1.5)

		po.cancel()
		# check if per complete is as expected
		mr.load_from_db()
		self.assertEqual(mr.per_ordered, 0)
		self.assertEqual(mr.get("products")[0].ordered_qty, 0)
		self.assertEqual(mr.get("products")[1].ordered_qty, 0)

		current_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		current_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		self.assertEqual(current_requested_qty_product1, existing_requested_qty_product1 + 54.0)
		self.assertEqual(current_requested_qty_product2, existing_requested_qty_product2 + 3.0)

	def test_completed_qty_for_transfer(self):
		existing_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		existing_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		# submit material request of type Purchase
		mr = frappe.copy_doc(test_records[0])
		mr.material_request_type = "Material Transfer"
		mr.insert()
		mr.submit()

		# check if per complete is None
		mr.load_from_db()
		self.assertEqual(mr.per_ordered, 0)
		self.assertEqual(mr.get("products")[0].ordered_qty, 0)
		self.assertEqual(mr.get("products")[1].ordered_qty, 0)

		current_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		current_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		self.assertEqual(current_requested_qty_product1, existing_requested_qty_product1 + 54.0)
		self.assertEqual(current_requested_qty_product2, existing_requested_qty_product2 + 3.0)

		# map a stock entry
		se_doc = make_stock_entry(mr.name)
		se_doc.update(
			{
				"posting_date": "2013-03-01",
				"posting_time": "01:00",
				"fiscal_year": "_Test Fiscal Year 2013",
			}
		)
		se_doc.get("products")[0].update(
			{"qty": 27.0, "transfer_qty": 27.0, "s_warehouse": "_Test Warehouse 1 - _TC", "basic_rate": 1.0}
		)
		se_doc.get("products")[1].update(
			{"qty": 1.5, "transfer_qty": 1.5, "s_warehouse": "_Test Warehouse 1 - _TC", "basic_rate": 1.0}
		)

		# make available the qty in _Test Warehouse 1 before transfer
		self._insert_stock_entry(27.0, 1.5)

		# check for stopped status of Material Request
		se = frappe.copy_doc(se_doc)
		se.insert()
		mr.update_status("Stopped")
		self.assertRaises(frappe.InvalidStatusError, se.submit)

		mr.update_status("Submitted")

		se.flags.ignore_validate_update_after_submit = True
		se.submit()
		mr.update_status("Stopped")
		self.assertRaises(frappe.InvalidStatusError, se.cancel)

		mr.update_status("Submitted")
		se = frappe.copy_doc(se_doc)
		se.insert()
		se.submit()

		# check if per complete is as expected
		mr.load_from_db()
		self.assertEqual(mr.per_ordered, 50)
		self.assertEqual(mr.get("products")[0].ordered_qty, 27.0)
		self.assertEqual(mr.get("products")[1].ordered_qty, 1.5)

		current_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		current_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		self.assertEqual(current_requested_qty_product1, existing_requested_qty_product1 + 27.0)
		self.assertEqual(current_requested_qty_product2, existing_requested_qty_product2 + 1.5)

		# check if per complete is as expected for Stock Entry cancelled
		se.cancel()
		mr.load_from_db()
		self.assertEqual(mr.per_ordered, 0)
		self.assertEqual(mr.get("products")[0].ordered_qty, 0)
		self.assertEqual(mr.get("products")[1].ordered_qty, 0)

		current_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		current_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		self.assertEqual(current_requested_qty_product1, existing_requested_qty_product1 + 54.0)
		self.assertEqual(current_requested_qty_product2, existing_requested_qty_product2 + 3.0)

	def test_over_transfer_qty_allowance(self):
		mr = frappe.new_doc("Material Request")
		mr.company = "_Test Company"
		mr.scheduled_date = today()
		mr.append(
			"products",
			{
				"product_code": "_Test FG Product",
				"product_name": "_Test FG Product",
				"qty": 10,
				"schedule_date": today(),
				"uom": "_Test UOM 1",
				"warehouse": "_Test Warehouse - _TC",
			},
		)

		mr.material_request_type = "Material Transfer"
		mr.insert()
		mr.submit()

		frappe.db.set_value("Stock Settings", None, "mr_qty_allowance", 20)

		# map a stock entry

		se_doc = make_stock_entry(mr.name)
		se_doc.update(
			{
				"posting_date": today(),
				"posting_time": "00:00",
			}
		)
		se_doc.get("products")[0].update(
			{
				"qty": 13,
				"transfer_qty": 12.0,
				"s_warehouse": "_Test Warehouse - _TC",
				"t_warehouse": "_Test Warehouse 1 - _TC",
				"basic_rate": 1.0,
			}
		)

		# make available the qty in _Test Warehouse 1 before transfer
		sr = frappe.new_doc("Stock Reconciliation")
		sr.company = "_Test Company"
		sr.purpose = "Opening Stock"
		sr.append(
			"products",
			{
				"product_code": "_Test FG Product",
				"warehouse": "_Test Warehouse - _TC",
				"qty": 20,
				"valuation_rate": 0.01,
			},
		)
		sr.insert()
		sr.submit()
		se = frappe.copy_doc(se_doc)
		se.insert()
		self.assertRaises(frappe.ValidationError)
		se.products[0].qty = 12
		se.submit()

	def test_completed_qty_for_over_transfer(self):
		existing_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		existing_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		# submit material request of type Purchase
		mr = frappe.copy_doc(test_records[0])
		mr.material_request_type = "Material Transfer"
		mr.insert()
		mr.submit()

		# map a stock entry

		se_doc = make_stock_entry(mr.name)
		se_doc.update(
			{
				"posting_date": "2013-03-01",
				"posting_time": "00:00",
				"fiscal_year": "_Test Fiscal Year 2013",
			}
		)
		se_doc.get("products")[0].update(
			{"qty": 54.0, "transfer_qty": 54.0, "s_warehouse": "_Test Warehouse 1 - _TC", "basic_rate": 1.0}
		)
		se_doc.get("products")[1].update(
			{"qty": 3.0, "transfer_qty": 3.0, "s_warehouse": "_Test Warehouse 1 - _TC", "basic_rate": 1.0}
		)

		# make available the qty in _Test Warehouse 1 before transfer
		self._insert_stock_entry(60.0, 3.0)

		# check for stopped status of Material Request
		se = frappe.copy_doc(se_doc)
		se.set_stock_entry_type()
		se.insert()
		mr.update_status("Stopped")
		self.assertRaises(frappe.InvalidStatusError, se.submit)
		self.assertRaises(frappe.InvalidStatusError, se.cancel)

		mr.update_status("Submitted")
		se = frappe.copy_doc(se_doc)
		se.set_stock_entry_type()
		se.insert()
		se.submit()

		# check if per complete is as expected
		mr.load_from_db()

		self.assertEqual(mr.per_ordered, 100)
		self.assertEqual(mr.get("products")[0].ordered_qty, 54.0)
		self.assertEqual(mr.get("products")[1].ordered_qty, 3.0)

		current_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		current_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		self.assertEqual(current_requested_qty_product1, existing_requested_qty_product1)
		self.assertEqual(current_requested_qty_product2, existing_requested_qty_product2)

		# check if per complete is as expected for Stock Entry cancelled
		se.cancel()
		mr.load_from_db()
		self.assertEqual(mr.per_ordered, 0)
		self.assertEqual(mr.get("products")[0].ordered_qty, 0)
		self.assertEqual(mr.get("products")[1].ordered_qty, 0)

		current_requested_qty_product1 = self._get_requested_qty(
			"_Test Product Home Desktop 100", "_Test Warehouse - _TC"
		)
		current_requested_qty_product2 = self._get_requested_qty(
			"_Test Product Home Desktop 200", "_Test Warehouse - _TC"
		)

		self.assertEqual(current_requested_qty_product1, existing_requested_qty_product1 + 54.0)
		self.assertEqual(current_requested_qty_product2, existing_requested_qty_product2 + 3.0)

	def test_incorrect_mapping_of_stock_entry(self):
		# submit material request of type Transfer
		mr = frappe.copy_doc(test_records[0])
		mr.material_request_type = "Material Transfer"
		mr.insert()
		mr.submit()

		se_doc = make_stock_entry(mr.name)
		se_doc.update(
			{
				"posting_date": "2013-03-01",
				"posting_time": "00:00",
				"fiscal_year": "_Test Fiscal Year 2013",
			}
		)
		se_doc.get("products")[0].update(
			{
				"qty": 60.0,
				"transfer_qty": 60.0,
				"s_warehouse": "_Test Warehouse - _TC",
				"t_warehouse": "_Test Warehouse 1 - _TC",
				"basic_rate": 1.0,
			}
		)
		se_doc.get("products")[1].update(
			{
				"product_code": "_Test Product Home Desktop 100",
				"qty": 3.0,
				"transfer_qty": 3.0,
				"s_warehouse": "_Test Warehouse 1 - _TC",
				"basic_rate": 1.0,
			}
		)

		# check for stopped status of Material Request
		se = frappe.copy_doc(se_doc)
		self.assertRaises(frappe.MappingMismatchError, se.insert)

		# submit material request of type Transfer
		mr = frappe.copy_doc(test_records[0])
		mr.material_request_type = "Material Issue"
		mr.insert()
		mr.submit()

		se_doc = make_stock_entry(mr.name)
		self.assertEqual(se_doc.get("products")[0].s_warehouse, "_Test Warehouse - _TC")

	def test_warehouse_company_validation(self):
		from erpnext.stock.utils import InvalidWarehouseCompany

		mr = frappe.copy_doc(test_records[0])
		mr.company = "_Test Company 1"
		self.assertRaises(InvalidWarehouseCompany, mr.insert)

	def _get_requested_qty(self, product_code, warehouse):
		return flt(
			frappe.db.get_value("Bin", {"product_code": product_code, "warehouse": warehouse}, "indented_qty")
		)

	def test_make_stock_entry_for_material_issue(self):
		mr = frappe.copy_doc(test_records[0]).insert()

		self.assertRaises(frappe.ValidationError, make_stock_entry, mr.name)

		mr = frappe.get_doc("Material Request", mr.name)
		mr.material_request_type = "Material Issue"
		mr.submit()
		se = make_stock_entry(mr.name)

		self.assertEqual(se.doctype, "Stock Entry")
		self.assertEqual(len(se.get("products")), len(mr.get("products")))

	def test_completed_qty_for_issue(self):
		def _get_requested_qty():
			return flt(
				frappe.db.get_value(
					"Bin",
					{"product_code": "_Test Product Home Desktop 100", "warehouse": "_Test Warehouse - _TC"},
					"indented_qty",
				)
			)

		existing_requested_qty = _get_requested_qty()

		mr = frappe.copy_doc(test_records[0])
		mr.material_request_type = "Material Issue"
		mr.submit()
		frappe.db.value_cache = {}

		# testing bin value after material request is submitted
		self.assertEqual(_get_requested_qty(), existing_requested_qty - 54.0)

		# receive products to allow issue
		self._insert_stock_entry(60, 6, "_Test Warehouse - _TC")

		# make stock entry against MR

		se_doc = make_stock_entry(mr.name)
		se_doc.fiscal_year = "_Test Fiscal Year 2014"
		se_doc.get("products")[0].qty = 54.0
		se_doc.insert()
		se_doc.submit()

		# check if per complete is as expected
		mr.load_from_db()
		self.assertEqual(mr.get("products")[0].ordered_qty, 54.0)
		self.assertEqual(mr.get("products")[1].ordered_qty, 3.0)

		# testing bin requested qty after issuing stock against material request
		self.assertEqual(_get_requested_qty(), existing_requested_qty)

	def test_material_request_type_manufacture(self):
		mr = frappe.copy_doc(test_records[1]).insert()
		mr = frappe.get_doc("Material Request", mr.name)
		mr.submit()
		completed_qty = mr.products[0].ordered_qty
		requested_qty = frappe.db.sql(
			"""select indented_qty from `tabBin` where \
			product_code= %s and warehouse= %s """,
			(mr.products[0].product_code, mr.products[0].warehouse),
		)[0][0]

		prod_order = raise_work_orders(mr.name)
		po = frappe.get_doc("Work Order", prod_order[0])
		po.wip_warehouse = "_Test Warehouse 1 - _TC"
		po.submit()

		mr = frappe.get_doc("Material Request", mr.name)
		self.assertEqual(completed_qty + po.qty, mr.products[0].ordered_qty)

		new_requested_qty = frappe.db.sql(
			"""select indented_qty from `tabBin` where \
			product_code= %s and warehouse= %s """,
			(mr.products[0].product_code, mr.products[0].warehouse),
		)[0][0]

		self.assertEqual(requested_qty - po.qty, new_requested_qty)

		po.cancel()

		mr = frappe.get_doc("Material Request", mr.name)
		self.assertEqual(completed_qty, mr.products[0].ordered_qty)

		new_requested_qty = frappe.db.sql(
			"""select indented_qty from `tabBin` where \
			product_code= %s and warehouse= %s """,
			(mr.products[0].product_code, mr.products[0].warehouse),
		)[0][0]
		self.assertEqual(requested_qty, new_requested_qty)

	def test_requested_qty_multi_uom(self):
		existing_requested_qty = self._get_requested_qty("_Test FG Product", "_Test Warehouse - _TC")

		mr = make_material_request(
			product_code="_Test FG Product",
			material_request_type="Manufacture",
			uom="_Test UOM 1",
			conversion_factor=12,
		)

		requested_qty = self._get_requested_qty("_Test FG Product", "_Test Warehouse - _TC")

		self.assertEqual(requested_qty, existing_requested_qty + 120)

		work_order = raise_work_orders(mr.name)
		wo = frappe.get_doc("Work Order", work_order[0])
		wo.qty = 50
		wo.wip_warehouse = "_Test Warehouse 1 - _TC"
		wo.submit()

		requested_qty = self._get_requested_qty("_Test FG Product", "_Test Warehouse - _TC")
		self.assertEqual(requested_qty, existing_requested_qty + 70)

		wo.cancel()

		requested_qty = self._get_requested_qty("_Test FG Product", "_Test Warehouse - _TC")
		self.assertEqual(requested_qty, existing_requested_qty + 120)

		mr.reload()
		mr.cancel()
		requested_qty = self._get_requested_qty("_Test FG Product", "_Test Warehouse - _TC")
		self.assertEqual(requested_qty, existing_requested_qty)

	def test_multi_uom_for_purchase(self):
		mr = frappe.copy_doc(test_records[0])
		mr.material_request_type = "Purchase"
		product = mr.products[0]
		mr.schedule_date = today()

		if not frappe.db.get_value("UOM Conversion Detail", {"parent": product.product_code, "uom": "Kg"}):
			product_doc = frappe.get_doc("Product", product.product_code)
			product_doc.append("uoms", {"uom": "Kg", "conversion_factor": 5})
			product_doc.save(ignore_permissions=True)

		product.uom = "Kg"
		for product in mr.products:
			product.schedule_date = mr.schedule_date

		mr.insert()
		self.assertRaises(frappe.ValidationError, make_purchase_order, mr.name)

		mr = frappe.get_doc("Material Request", mr.name)
		mr.submit()
		product = mr.products[0]

		self.assertEqual(product.uom, "Kg")
		self.assertEqual(product.conversion_factor, 5.0)
		self.assertEqual(product.stock_qty, flt(product.qty * 5))

		po = make_purchase_order(mr.name)
		self.assertEqual(po.doctype, "Purchase Order")
		self.assertEqual(len(po.get("products")), len(mr.get("products")))

		po.supplier = "_Test Supplier"
		po.insert()
		po.submit()
		mr = frappe.get_doc("Material Request", mr.name)
		self.assertEqual(mr.per_ordered, 100)

	def test_customer_provided_parts_mr(self):
		create_product(
			"CUST-0987", is_customer_provided_product=1, customer="_Test Customer", is_purchase_product=0
		)
		existing_requested_qty = self._get_requested_qty("_Test Customer", "_Test Warehouse - _TC")

		mr = make_material_request(product_code="CUST-0987", material_request_type="Customer Provided")
		se = make_stock_entry(mr.name)
		se.insert()
		se.submit()
		self.assertEqual(se.get("products")[0].amount, 0)
		self.assertEqual(se.get("products")[0].material_request, mr.name)
		mr = frappe.get_doc("Material Request", mr.name)
		mr.submit()
		current_requested_qty = self._get_requested_qty("_Test Customer", "_Test Warehouse - _TC")

		self.assertEqual(mr.per_ordered, 100)
		self.assertEqual(existing_requested_qty, current_requested_qty)


def get_in_transit_warehouse(company):
	if not frappe.db.exists("Warehouse Type", "Transit"):
		frappe.get_doc(
			{
				"doctype": "Warehouse Type",
				"name": "Transit",
			}
		).insert()

	in_transit_warehouse = frappe.db.exists(
		"Warehouse", {"warehouse_type": "Transit", "company": company}
	)

	if not in_transit_warehouse:
		in_transit_warehouse = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": "Transit",
					"warehouse_type": "Transit",
					"company": company,
				}
			)
			.insert()
			.name
		)

	return in_transit_warehouse


def make_material_request(**args):
	args = frappe._dict(args)
	mr = frappe.new_doc("Material Request")
	mr.material_request_type = args.material_request_type or "Purchase"
	mr.company = args.company or "_Test Company"
	mr.customer = args.customer or "_Test Customer"
	mr.append(
		"products",
		{
			"product_code": args.product_code or "_Test Product",
			"qty": args.qty or 10,
			"uom": args.uom or "_Test UOM",
			"conversion_factor": args.conversion_factor or 1,
			"schedule_date": args.schedule_date or today(),
			"warehouse": args.warehouse or "_Test Warehouse - _TC",
			"cost_center": args.cost_center or "_Test Cost Center - _TC",
		},
	)
	mr.insert()
	if not args.do_not_submit:
		mr.submit()
	return mr


test_dependencies = ["Currency Exchange", "BOM"]
test_records = frappe.get_test_records("Material Request")
