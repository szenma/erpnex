# Python bytecode 2.7 (62211)
# Embedded file name: /Users/anuragmishra/frappe-develop/apps/erpnext/erpnext/buying/report/subcontracted_raw_materials_to_be_transferred/test_subcontracted_raw_materials_to_be_transferred.py
# Compiled at: 2019-05-06 10:24:35
# Decompiled by https://python-decompiler.com

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext.buying.report.subcontracted_raw_materials_to_be_transferred.subcontracted_raw_materials_to_be_transferred import (
	execute,
)
from erpnext.controllers.subcontracting_controller import make_rm_stock_entry
from erpnext.controllers.tests.test_subcontracting_controller import (
	get_subcontracting_order,
	make_service_product,
)
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry


class TestSubcontractedProductToBeTransferred(FrappeTestCase):
	def test_pending_and_transferred_qty(self):
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
		sco = get_subcontracting_order(service_products=service_products)

		# Material Receipt of RMs
		make_stock_entry(product_code="_Test Product", target="_Test Warehouse - _TC", qty=100, basic_rate=100)
		make_stock_entry(
			product_code="_Test Product Home Desktop 100", target="_Test Warehouse - _TC", qty=100, basic_rate=100
		)

		transfer_subcontracted_raw_materials(sco)

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
		sco.reload()

		sco_data = [row for row in data if row.get("subcontract_order") == sco.name]
		# Alphabetically sort to be certain of order
		sco_data = sorted(sco_data, key=lambda i: i["rm_product_code"])

		self.assertEqual(len(sco_data), 2)
		self.assertEqual(sco_data[0]["subcontract_order"], sco.name)

		self.assertEqual(sco_data[0]["rm_product_code"], "_Test Product")
		self.assertEqual(sco_data[0]["p_qty"], 8)
		self.assertEqual(sco_data[0]["transferred_qty"], 2)

		self.assertEqual(sco_data[1]["rm_product_code"], "_Test Product Home Desktop 100")
		self.assertEqual(sco_data[1]["p_qty"], 19)
		self.assertEqual(sco_data[1]["transferred_qty"], 1)


def transfer_subcontracted_raw_materials(sco):
	# Order of supplied products fetched in SCO is flaky
	transfer_qty_map = {"_Test Product": 2, "_Test Product Home Desktop 100": 1}

	product_1 = sco.supplied_products[0].rm_product_code
	product_2 = sco.supplied_products[1].rm_product_code

	rm_products = [
		{
			"name": sco.supplied_products[0].name,
			"product_code": product_1,
			"rm_product_code": product_1,
			"product_name": product_1,
			"qty": transfer_qty_map[product_1],
			"warehouse": "_Test Warehouse - _TC",
			"rate": 100,
			"amount": 100 * transfer_qty_map[product_1],
			"stock_uom": "Nos",
		},
		{
			"name": sco.supplied_products[1].name,
			"product_code": product_2,
			"rm_product_code": product_2,
			"product_name": product_2,
			"qty": transfer_qty_map[product_2],
			"warehouse": "_Test Warehouse - _TC",
			"rate": 100,
			"amount": 100 * transfer_qty_map[product_2],
			"stock_uom": "Nos",
		},
	]
	se = frappe.get_doc(make_rm_stock_entry(sco.name, rm_products))
	se.from_warehouse = "_Test Warehouse - _TC"
	se.to_warehouse = "_Test Warehouse - _TC"
	se.stock_entry_type = "Send to Subcontractor"
	se.save()
	se.submit()
	return se
