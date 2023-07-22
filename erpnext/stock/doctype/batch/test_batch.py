# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json

import frappe
from frappe.exceptions import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, flt
from frappe.utils.data import add_to_date, getdate

from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from erpnext.stock.doctype.batch.batch import UnableToSelectBatchError, get_batch_no, get_batch_qty
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import (
	create_stock_reconciliation,
)
from erpnext.stock.get_product_details import get_product_details
from erpnext.stock.stock_ledger import get_valuation_rate


class TestBatch(FrappeTestCase):
	def test_product_has_batch_enabled(self):
		self.assertRaises(
			ValidationError,
			frappe.get_doc({"doctype": "Batch", "name": "_test Batch", "product": "_Test Product"}).save,
		)

	@classmethod
	def make_batch_product(cls, product_name=None):
		from erpnext.stock.doctype.product.test_product import make_product

		if not frappe.db.exists(product_name):
			return make_product(product_name, dict(has_batch_no=1, create_new_batch=1, is_stock_product=1))

	def test_purchase_receipt(self, batch_qty=100):
		"""Test automated batch creation from Purchase Receipt"""
		self.make_batch_product("PRODUCT-BATCH-1")

		receipt = frappe.get_doc(
			dict(
				doctype="Purchase Receipt",
				supplier="_Test Supplier",
				company="_Test Company",
				products=[dict(product_code="PRODUCT-BATCH-1", qty=batch_qty, rate=10, warehouse="Stores - _TC")],
			)
		).insert()
		receipt.submit()

		self.assertTrue(receipt.products[0].batch_no)
		self.assertEqual(get_batch_qty(receipt.products[0].batch_no, receipt.products[0].warehouse), batch_qty)

		return receipt

	def test_stock_entry_incoming(self):
		"""Test batch creation via Stock Entry (Work Order)"""

		self.make_batch_product("PRODUCT-BATCH-1")

		stock_entry = frappe.get_doc(
			dict(
				doctype="Stock Entry",
				purpose="Material Receipt",
				company="_Test Company",
				products=[
					dict(
						product_code="PRODUCT-BATCH-1",
						qty=90,
						t_warehouse="_Test Warehouse - _TC",
						cost_center="Main - _TC",
						rate=10,
					)
				],
			)
		)

		stock_entry.set_stock_entry_type()
		stock_entry.insert()
		stock_entry.submit()

		self.assertTrue(stock_entry.products[0].batch_no)
		self.assertEqual(
			get_batch_qty(stock_entry.products[0].batch_no, stock_entry.products[0].t_warehouse), 90
		)

	def test_delivery_note(self):
		"""Test automatic batch selection for outgoing products"""
		batch_qty = 15
		receipt = self.test_purchase_receipt(batch_qty)
		product_code = "PRODUCT-BATCH-1"

		delivery_note = frappe.get_doc(
			dict(
				doctype="Delivery Note",
				customer="_Test Customer",
				company=receipt.company,
				products=[
					dict(product_code=product_code, qty=batch_qty, rate=10, warehouse=receipt.products[0].warehouse)
				],
			)
		).insert()
		delivery_note.submit()

		# shipped from FEFO batch
		self.assertEqual(
			delivery_note.products[0].batch_no, get_batch_no(product_code, receipt.products[0].warehouse, batch_qty)
		)

	def test_delivery_note_fail(self):
		"""Test automatic batch selection for outgoing products"""
		receipt = self.test_purchase_receipt(100)
		delivery_note = frappe.get_doc(
			dict(
				doctype="Delivery Note",
				customer="_Test Customer",
				company=receipt.company,
				products=[
					dict(product_code="PRODUCT-BATCH-1", qty=5000, rate=10, warehouse=receipt.products[0].warehouse)
				],
			)
		)
		self.assertRaises(UnableToSelectBatchError, delivery_note.insert)

	def test_stock_entry_outgoing(self):
		"""Test automatic batch selection for outgoing stock entry"""

		batch_qty = 16
		receipt = self.test_purchase_receipt(batch_qty)
		product_code = "PRODUCT-BATCH-1"

		stock_entry = frappe.get_doc(
			dict(
				doctype="Stock Entry",
				purpose="Material Issue",
				company=receipt.company,
				products=[
					dict(
						product_code=product_code,
						qty=batch_qty,
						s_warehouse=receipt.products[0].warehouse,
					)
				],
			)
		)

		stock_entry.set_stock_entry_type()
		stock_entry.insert()
		stock_entry.submit()

		# assert same batch is selected
		self.assertEqual(
			stock_entry.products[0].batch_no, get_batch_no(product_code, receipt.products[0].warehouse, batch_qty)
		)

	def test_batch_split(self):
		"""Test batch splitting"""
		receipt = self.test_purchase_receipt()
		from erpnext.stock.doctype.batch.batch import split_batch

		new_batch = split_batch(
			receipt.products[0].batch_no, "PRODUCT-BATCH-1", receipt.products[0].warehouse, 22
		)

		self.assertEqual(get_batch_qty(receipt.products[0].batch_no, receipt.products[0].warehouse), 78)
		self.assertEqual(get_batch_qty(new_batch, receipt.products[0].warehouse), 22)

	def test_get_batch_qty(self):
		"""Test getting batch quantities by batch_numbers, product_code or warehouse"""
		self.make_batch_product("PRODUCT-BATCH-2")
		self.make_new_batch_and_entry("PRODUCT-BATCH-2", "batch a", "_Test Warehouse - _TC")
		self.make_new_batch_and_entry("PRODUCT-BATCH-2", "batch b", "_Test Warehouse - _TC")

		self.assertEqual(
			get_batch_qty(product_code="PRODUCT-BATCH-2", warehouse="_Test Warehouse - _TC"),
			[{"batch_no": "batch a", "qty": 90.0}, {"batch_no": "batch b", "qty": 90.0}],
		)

		self.assertEqual(get_batch_qty("batch a", "_Test Warehouse - _TC"), 90)

	def test_total_batch_qty(self):
		self.make_batch_product("PRODUCT-BATCH-3")
		existing_batch_qty = flt(frappe.db.get_value("Batch", "B100", "batch_qty"))
		stock_entry = self.make_new_batch_and_entry("PRODUCT-BATCH-3", "B100", "_Test Warehouse - _TC")

		current_batch_qty = flt(frappe.db.get_value("Batch", "B100", "batch_qty"))
		self.assertEqual(current_batch_qty, existing_batch_qty + 90)

		stock_entry.cancel()
		current_batch_qty = flt(frappe.db.get_value("Batch", "B100", "batch_qty"))
		self.assertEqual(current_batch_qty, existing_batch_qty)

	@classmethod
	def make_new_batch_and_entry(cls, product_name, batch_name, warehouse):
		"""Make a new stock entry for given target warehouse and batch name of product"""

		if not frappe.db.exists("Batch", batch_name):
			batch = frappe.get_doc(dict(doctype="Batch", product=product_name, batch_id=batch_name)).insert(
				ignore_permissions=True
			)
			batch.save()

		stock_entry = frappe.get_doc(
			dict(
				doctype="Stock Entry",
				purpose="Material Receipt",
				company="_Test Company",
				products=[
					dict(
						product_code=product_name,
						qty=90,
						t_warehouse=warehouse,
						cost_center="Main - _TC",
						rate=10,
						batch_no=batch_name,
						allow_zero_valuation_rate=1,
					)
				],
			)
		)

		stock_entry.set_stock_entry_type()
		stock_entry.insert()
		stock_entry.submit()

		return stock_entry

	def test_batch_name_with_naming_series(self):
		stock_settings = frappe.get_single("Stock Settings")
		use_naming_series = cint(stock_settings.use_naming_series)

		if not use_naming_series:
			frappe.set_value("Stock Settings", "Stock Settings", "use_naming_series", 1)

		batch = self.make_new_batch("_Test Stock Product For Batch Test1")
		batch_name = batch.name

		self.assertTrue(batch_name.startswith("BATCH-"))

		batch.delete()
		batch = self.make_new_batch("_Test Stock Product For Batch Test2")

		self.assertEqual(batch_name, batch.name)

		# reset Stock Settings
		if not use_naming_series:
			frappe.set_value("Stock Settings", "Stock Settings", "use_naming_series", 0)

	def make_new_batch(self, product_name=None, batch_id=None, do_not_insert=0):
		batch = frappe.new_doc("Batch")
		product = self.make_batch_product(product_name)
		batch.product = product.name

		if batch_id:
			batch.batch_id = batch_id

		if not do_not_insert:
			batch.insert()

		return batch

	def test_batch_wise_product_price(self):
		if not frappe.db.get_value("Product", "_Test Batch Price Product"):
			frappe.get_doc(
				{
					"doctype": "Product",
					"is_stock_product": 1,
					"product_code": "_Test Batch Price Product",
					"product_group": "Products",
					"has_batch_no": 1,
					"create_new_batch": 1,
				}
			).insert(ignore_permissions=True)

		batch1 = create_batch("_Test Batch Price Product", 200, 1)
		batch2 = create_batch("_Test Batch Price Product", 300, 1)
		batch3 = create_batch("_Test Batch Price Product", 400, 0)

		company = "_Test Company with perpetual inventory"
		currency = frappe.get_cached_value("Company", company, "default_currency")

		args = frappe._dict(
			{
				"product_code": "_Test Batch Price Product",
				"company": company,
				"price_list": "_Test Price List",
				"currency": currency,
				"doctype": "Sales Invoice",
				"conversion_rate": 1,
				"price_list_currency": "_Test Currency",
				"plc_conversion_rate": 1,
				"customer": "_Test Customer",
				"name": None,
			}
		)

		# test price for batch1
		args.update({"batch_no": batch1})
		details = get_product_details(args)
		self.assertEqual(details.get("price_list_rate"), 200)

		# test price for batch2
		args.update({"batch_no": batch2})
		details = get_product_details(args)
		self.assertEqual(details.get("price_list_rate"), 300)

		# test price for batch3
		args.update({"batch_no": batch3})
		details = get_product_details(args)
		self.assertEqual(details.get("price_list_rate"), 400)

	def test_basic_batch_wise_valuation(self, batch_qty=100):
		product_code = "_TestBatchWiseVal"
		warehouse = "_Test Warehouse - _TC"
		self.make_batch_product(product_code)

		rates = [42, 420]

		batches = {}
		for rate in rates:
			se = make_stock_entry(product_code=product_code, qty=10, rate=rate, target=warehouse)
			batches[se.products[0].batch_no] = rate

		LOW, HIGH = list(batches.keys())

		# consume things out of order
		consumption_plan = [
			(HIGH, 1),
			(LOW, 2),
			(HIGH, 2),
			(HIGH, 4),
			(LOW, 6),
		]

		stock_value = sum(rates) * 10
		qty_after_transaction = 20
		for batch, qty in consumption_plan:
			# consume out of order
			se = make_stock_entry(product_code=product_code, source=warehouse, qty=qty, batch_no=batch)

			sle = frappe.get_last_doc("Stock Ledger Entry", {"is_cancelled": 0, "voucher_no": se.name})

			stock_value_difference = sle.actual_qty * batches[sle.batch_no]
			self.assertAlmostEqual(sle.stock_value_difference, stock_value_difference)

			stock_value += stock_value_difference
			self.assertAlmostEqual(sle.stock_value, stock_value)

			qty_after_transaction += sle.actual_qty
			self.assertAlmostEqual(sle.qty_after_transaction, qty_after_transaction)
			self.assertAlmostEqual(sle.valuation_rate, stock_value / qty_after_transaction)

			self.assertEqual(json.loads(sle.stock_queue), [])  # queues don't apply on batched products

	def test_moving_batch_valuation_rates(self):
		product_code = "_TestBatchWiseVal"
		warehouse = "_Test Warehouse - _TC"
		self.make_batch_product(product_code)

		def assertValuation(expected):
			actual = get_valuation_rate(
				product_code, warehouse, "voucher_type", "voucher_no", batch_no=batch_no
			)
			self.assertAlmostEqual(actual, expected)

		se = make_stock_entry(product_code=product_code, qty=100, rate=10, target=warehouse)
		batch_no = se.products[0].batch_no
		assertValuation(10)

		# consumption should never affect current valuation rate
		make_stock_entry(product_code=product_code, qty=20, source=warehouse)
		assertValuation(10)

		make_stock_entry(product_code=product_code, qty=30, source=warehouse)
		assertValuation(10)

		# 50 * 10 = 500 current value, add more product with higher valuation
		make_stock_entry(product_code=product_code, qty=50, rate=20, target=warehouse, batch_no=batch_no)
		assertValuation(15)

		# consuming again shouldn't do anything
		make_stock_entry(product_code=product_code, qty=20, source=warehouse)
		assertValuation(15)

		# reset rate with stock reconiliation
		create_stock_reconciliation(
			product_code=product_code, warehouse=warehouse, qty=10, rate=25, batch_no=batch_no
		)
		assertValuation(25)

		make_stock_entry(product_code=product_code, qty=20, rate=20, target=warehouse, batch_no=batch_no)
		assertValuation((20 * 20 + 10 * 25) / (10 + 20))

	def test_update_batch_properties(self):
		product_code = "_TestBatchWiseVal"
		self.make_batch_product(product_code)

		se = make_stock_entry(product_code=product_code, qty=100, rate=10, target="_Test Warehouse - _TC")
		batch_no = se.products[0].batch_no
		batch = frappe.get_doc("Batch", batch_no)

		expiry_date = add_to_date(batch.manufacturing_date, days=30)

		batch.expiry_date = expiry_date
		batch.save()

		batch.reload()

		self.assertEqual(getdate(batch.expiry_date), getdate(expiry_date))

	def test_autocreation_of_batches(self):
		"""
		Test if auto created Serial No excludes existing serial numbers
		"""
		product_code = make_product(
			properties={
				"has_batch_no": 1,
				"batch_number_series": "BATCHEXISTING.###",
				"create_new_batch": 1,
			}
		).name

		manually_created_batch = self.make_new_batch(product_code, batch_id="BATCHEXISTING001").name

		pr_1 = make_purchase_receipt(product_code=product_code, qty=1, batch_no=manually_created_batch)
		pr_2 = make_purchase_receipt(product_code=product_code, qty=1)

		self.assertNotEqual(pr_1.products[0].batch_no, pr_2.products[0].batch_no)
		self.assertEqual("BATCHEXISTING002", pr_2.products[0].batch_no)


def create_batch(product_code, rate, create_product_price_for_batch):
	pi = make_purchase_invoice(
		company="_Test Company",
		warehouse="Stores - _TC",
		cost_center="Main - _TC",
		update_stock=1,
		expense_account="_Test Account Cost for Goods Sold - _TC",
		product_code=product_code,
	)

	batch = frappe.db.get_value("Batch", {"product": product_code, "reference_name": pi.name})

	if not create_product_price_for_batch:
		create_price_list_for_batch(product_code, None, rate)
	else:
		create_price_list_for_batch(product_code, batch, rate)

	return batch


def create_price_list_for_batch(product_code, batch, rate):
	frappe.get_doc(
		{
			"doctype": "Product Price",
			"product_code": "_Test Batch Price Product",
			"price_list": "_Test Price List",
			"batch_no": batch,
			"price_list_rate": rate,
		}
	).insert()


def make_new_batch(**args):
	args = frappe._dict(args)

	if frappe.db.exists("Batch", args.batch_id):
		batch = frappe.get_doc("Batch", args.batch_id)
	else:
		batch = frappe.get_doc(
			{
				"doctype": "Batch",
				"batch_id": args.batch_id,
				"product": args.product_code,
				"expiry_date": args.expiry_date,
			}
		)

		if args.expiry_date:
			batch.expiry_date = args.expiry_date

		batch.insert()

	return batch
