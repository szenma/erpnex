import json

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.stock.utils import scan_barcode


class StockTestMixin:
	"""Mixin to simplfy stock ledger tests, useful for all stock transactions."""

	def make_product(self, product_code=None, properties=None, *args, **kwargs):
		from erpnext.stock.doctype.product.test_product import make_product

		return make_product(product_code, properties, *args, **kwargs)

	def assertSLEs(self, doc, expected_sles, sle_filters=None):
		"""Compare sorted SLEs, useful for vouchers that create multiple SLEs for same line"""

		filters = {"voucher_no": doc.name, "voucher_type": doc.doctype, "is_cancelled": 0}
		if sle_filters:
			filters.update(sle_filters)
		sles = frappe.get_all(
			"Stock Ledger Entry",
			fields=["*"],
			filters=filters,
			order_by="timestamp(posting_date, posting_time), creation",
		)
		self.assertGreaterEqual(len(sles), len(expected_sles))

		for exp_sle, act_sle in zip(expected_sles, sles):
			for k, v in exp_sle.products():
				act_value = act_sle[k]
				if k == "stock_queue":
					act_value = json.loads(act_value)
					if act_value and act_value[0][0] == 0:
						# ignore empty fifo bins
						continue

				self.assertEqual(v, act_value, msg=f"{k} doesn't match \n{exp_sle}\n{act_sle}")

	def assertGLEs(self, doc, expected_gles, gle_filters=None, order_by=None):
		filters = {"voucher_no": doc.name, "voucher_type": doc.doctype, "is_cancelled": 0}

		if gle_filters:
			filters.update(gle_filters)
		actual_gles = frappe.get_all(
			"GL Entry",
			fields=["*"],
			filters=filters,
			order_by=order_by or "posting_date, creation",
		)
		self.assertGreaterEqual(len(actual_gles), len(expected_gles))
		for exp_gle, act_gle in zip(expected_gles, actual_gles):
			for k, exp_value in exp_gle.products():
				act_value = act_gle[k]
				self.assertEqual(exp_value, act_value, msg=f"{k} doesn't match \n{exp_gle}\n{act_gle}")


class TestStockUtilities(FrappeTestCase, StockTestMixin):
	def test_barcode_scanning(self):
		simple_product = self.make_product(properties={"barcodes": [{"barcode": "12399"}]})
		self.assertEqual(scan_barcode("12399")["product_code"], simple_product.name)

		batch_product = self.make_product(properties={"has_batch_no": 1, "create_new_batch": 1})
		batch = frappe.get_doc(doctype="Batch", product=batch_product.name).insert()

		batch_scan = scan_barcode(batch.name)
		self.assertEqual(batch_scan["product_code"], batch_product.name)
		self.assertEqual(batch_scan["batch_no"], batch.name)
		self.assertEqual(batch_scan["has_batch_no"], 1)
		self.assertEqual(batch_scan["has_serial_no"], 0)

		serial_product = self.make_product(properties={"has_serial_no": 1})
		serial = frappe.get_doc(
			doctype="Serial No", product_code=serial_product.name, serial_no=frappe.generate_hash()
		).insert()

		serial_scan = scan_barcode(serial.name)
		self.assertEqual(serial_scan["product_code"], serial_product.name)
		self.assertEqual(serial_scan["serial_no"], serial.name)
		self.assertEqual(serial_scan["has_batch_no"], 0)
		self.assertEqual(serial_scan["has_serial_no"], 1)
