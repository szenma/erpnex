# Python bytecode 2.7 (62211)
# Embedded file name: /Users/anuragmishra/frappe-develop/apps/erpnext/erpnext/buying/report/subcontracted_product_to_be_received/test_subcontracted_product_to_be_received.py
# Compiled at: 2019-05-06 09:51:46
# Decompiled by https://python-decompiler.com


import copy

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.buying.report.subcontracted_product_to_be_received.subcontracted_product_to_be_received import (
	execute,
)
from erpnext.controllers.tests.test_subcontracting_controller import (
	get_rm_products,
	get_subcontracting_order,
	make_service_product,
	make_stock_in_entry,
	make_stock_transfer_entry,
)
from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
	make_subcontracting_receipt,
)


class TestSubcontractedProductToBeReceived(FrappeTestCase):
	def test_pending_and_received_qty(self):
		make_service_product("Subcontracted Service Product 1")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 10,
				"rate": 500,
				"fg_product": "_Test FG Product",
				"fg_product_qty": 10,
			},
		]
		sco = get_subcontracting_order(
			service_products=service_products, supplier_warehouse="_Test Warehouse 1 - _TC"
		)
		rm_products = get_rm_products(sco.supplied_products)
		productwise_details = make_stock_in_entry(rm_products=rm_products)

		for product in rm_products:
			product["sco_rm_detail"] = sco.products[0].name

		make_stock_transfer_entry(
			sco_no=sco.name,
			rm_products=rm_products,
			productwise_details=copy.deepcopy(productwise_details),
		)

		make_subcontracting_receipt_against_sco(sco.name)
		sco.reload()
		col, data = execute(
			filters=frappe._dict(
				{
					"order_type": "Subcontracting Order",
					"supplier": sco.supplier,
					"from_date": frappe.utils.get_datetime(
						frappe.utils.add_to_date(sco.transaction_date, days=-10)
					),
					"to_date": frappe.utils.get_datetime(frappe.utils.add_to_date(sco.transaction_date, days=10)),
				}
			)
		)
		self.assertEqual(data[0]["pending_qty"], 5)
		self.assertEqual(data[0]["received_qty"], 5)
		self.assertEqual(data[0]["subcontract_order"], sco.name)
		self.assertEqual(data[0]["supplier"], sco.supplier)


def make_subcontracting_receipt_against_sco(sco, quantity=5):
	scr = make_subcontracting_receipt(sco)
	scr.products[0].qty = quantity
	scr.insert()
	scr.submit()
