# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from typing import List, Optional, Tuple

import frappe
from frappe.tests.utils import FrappeTestCase, change_settings
from frappe.utils import add_to_date, nowdate

from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import get_gl_entries
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry


def create_product_bundle(
	quantities: Optional[List[int]] = None, warehouse: Optional[str] = None
) -> Tuple[str, List[str]]:
	"""Get a new product_bundle for use in tests.

	Create 10x required stock if warehouse is specified.
	"""
	if not quantities:
		quantities = [2, 2]

	bundle = make_product(properties={"is_stock_product": 0}).name

	bundle_doc = frappe.get_doc({"doctype": "Product Bundle", "new_product_code": bundle})

	components = []
	for qty in quantities:
		compoenent = make_product().name
		components.append(compoenent)
		bundle_doc.append("products", {"product_code": compoenent, "qty": qty})
		if warehouse:
			make_stock_entry(product=compoenent, to_warehouse=warehouse, qty=10 * qty, rate=100)

	bundle_doc.insert()

	return bundle, components


class TestPackedProduct(FrappeTestCase):
	"Test impact on Packed Products table in various scenarios."

	@classmethod
	def setUpClass(cls) -> None:
		super().setUpClass()
		cls.warehouse = "_Test Warehouse - _TC"

		cls.bundle, cls.bundle_products = create_product_bundle(warehouse=cls.warehouse)
		cls.bundle2, cls.bundle2_products = create_product_bundle(warehouse=cls.warehouse)

		cls.normal_product = make_product().name

	def test_adding_bundle_product(self):
		"Test impact on packed products if bundle product row is added."
		so = make_sales_order(product_code=self.bundle, qty=1, do_not_submit=True)

		self.assertEqual(so.products[0].qty, 1)
		self.assertEqual(len(so.packed_products), 2)
		self.assertEqual(so.packed_products[0].product_code, self.bundle_products[0])
		self.assertEqual(so.packed_products[0].qty, 2)

	def test_updating_bundle_product(self):
		"Test impact on packed products if bundle product row is updated."
		so = make_sales_order(product_code=self.bundle, qty=1, do_not_submit=True)

		so.products[0].qty = 2  # change qty
		so.save()

		self.assertEqual(so.packed_products[0].qty, 4)
		self.assertEqual(so.packed_products[1].qty, 4)

		# change product code to non bundle product
		so.products[0].product_code = self.normal_product
		so.save()

		self.assertEqual(len(so.packed_products), 0)

	def test_recurring_bundle_product(self):
		"Test impact on packed products if same bundle product is added and removed."
		so_products = []
		for qty in [2, 4, 6, 8]:
			so_products.append(
				{"product_code": self.bundle, "qty": qty, "rate": 400, "warehouse": "_Test Warehouse - _TC"}
			)

		# create SO with recurring bundle product
		so = make_sales_order(product_list=so_products, do_not_submit=True)

		# check alternate rows for qty
		self.assertEqual(len(so.packed_products), 8)
		self.assertEqual(so.packed_products[1].product_code, self.bundle_products[1])
		self.assertEqual(so.packed_products[1].qty, 4)
		self.assertEqual(so.packed_products[3].qty, 8)
		self.assertEqual(so.packed_products[5].qty, 12)
		self.assertEqual(so.packed_products[7].qty, 16)

		# delete intermediate row (2nd)
		del so.products[1]
		so.save()

		# check alternate rows for qty
		self.assertEqual(len(so.packed_products), 6)
		self.assertEqual(so.packed_products[1].qty, 4)
		self.assertEqual(so.packed_products[3].qty, 12)
		self.assertEqual(so.packed_products[5].qty, 16)

		# delete last row
		del so.products[2]
		so.save()

		# check alternate rows for qty
		self.assertEqual(len(so.packed_products), 4)
		self.assertEqual(so.packed_products[1].qty, 4)
		self.assertEqual(so.packed_products[3].qty, 12)

	@change_settings("Selling Settings", {"editable_bundle_product_rates": 1})
	def test_bundle_product_cumulative_price(self):
		"Test if Bundle Product rate is cumulative from packed products."
		so = make_sales_order(product_code=self.bundle, qty=2, do_not_submit=True)

		so.packed_products[0].rate = 150
		so.packed_products[1].rate = 200
		so.save()

		self.assertEqual(so.products[0].rate, 700)
		self.assertEqual(so.products[0].amount, 1400)

	def test_newly_mapped_doc_packed_products(self):
		"Test impact on packed products in newly mapped DN from SO."
		so_products = []
		for qty in [2, 4]:
			so_products.append(
				{"product_code": self.bundle, "qty": qty, "rate": 400, "warehouse": "_Test Warehouse - _TC"}
			)

		# create SO with recurring bundle product
		so = make_sales_order(product_list=so_products)

		dn = make_delivery_note(so.name)
		dn.products[1].qty = 3  # change second row qty for inserting doc
		dn.save()

		self.assertEqual(len(dn.packed_products), 4)
		self.assertEqual(dn.packed_products[2].qty, 6)
		self.assertEqual(dn.packed_products[3].qty, 6)

	def test_reposting_packed_products(self):
		warehouse = "Stores - TCP1"
		company = "_Test Company with perpetual inventory"

		today = nowdate()
		yesterday = add_to_date(today, days=-1, as_string=True)

		for product in self.bundle_products:
			make_stock_entry(product_code=product, to_warehouse=warehouse, qty=10, rate=100, posting_date=today)

		so = make_sales_order(product_code=self.bundle, qty=1, company=company, warehouse=warehouse)

		dn = make_delivery_note(so.name)
		dn.save()
		dn.submit()

		gles = get_gl_entries(dn.doctype, dn.name)
		credit_before_repost = sum(gle.credit for gle in gles)

		# backdated stock entry
		for product in self.bundle_products:
			make_stock_entry(
				product_code=product, to_warehouse=warehouse, qty=10, rate=200, posting_date=yesterday
			)

		# assert correct reposting
		gles = get_gl_entries(dn.doctype, dn.name)
		credit_after_reposting = sum(gle.credit for gle in gles)
		self.assertNotEqual(credit_before_repost, credit_after_reposting)
		self.assertAlmostEqual(credit_after_reposting, 2 * credit_before_repost)

	def assertReturns(self, original, returned):
		self.assertEqual(len(original), len(returned))

		sort_function = lambda p: (p.parent_product, p.product_code, p.qty)

		for sent, returned in zip(
			sorted(original, key=sort_function), sorted(returned, key=sort_function)
		):
			self.assertEqual(sent.product_code, returned.product_code)
			self.assertEqual(sent.parent_product, returned.parent_product)
			self.assertEqual(sent.qty, -1 * returned.qty)

	def test_returning_full_bundles(self):
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return

		product_list = [
			{
				"product_code": self.bundle,
				"warehouse": self.warehouse,
				"qty": 1,
				"rate": 100,
			},
			{
				"product_code": self.bundle2,
				"warehouse": self.warehouse,
				"qty": 1,
				"rate": 100,
			},
		]
		so = make_sales_order(product_list=product_list, warehouse=self.warehouse)

		dn = make_delivery_note(so.name)
		dn.save()
		dn.submit()

		# create return
		dn_ret = make_sales_return(dn.name)
		dn_ret.save()
		dn_ret.submit()
		self.assertReturns(dn.packed_products, dn_ret.packed_products)

	def test_returning_partial_bundles(self):
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return

		product_list = [
			{
				"product_code": self.bundle,
				"warehouse": self.warehouse,
				"qty": 1,
				"rate": 100,
			},
			{
				"product_code": self.bundle2,
				"warehouse": self.warehouse,
				"qty": 1,
				"rate": 100,
			},
		]
		so = make_sales_order(product_list=product_list, warehouse=self.warehouse)

		dn = make_delivery_note(so.name)
		dn.save()
		dn.submit()

		# create return
		dn_ret = make_sales_return(dn.name)
		# remove bundle 2
		dn_ret.products.pop()

		dn_ret.save()
		dn_ret.submit()
		dn_ret.reload()

		self.assertTrue(all(d.parent_product == self.bundle for d in dn_ret.packed_products))

		expected_returns = [d for d in dn.packed_products if d.parent_product == self.bundle]
		self.assertReturns(expected_returns, dn_ret.packed_products)

	def test_returning_partial_bundle_qty(self):
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return

		so = make_sales_order(product_code=self.bundle, warehouse=self.warehouse, qty=2)

		dn = make_delivery_note(so.name)
		dn.save()
		dn.submit()

		# create return
		dn_ret = make_sales_return(dn.name)
		# halve the qty
		dn_ret.products[0].qty = -1
		dn_ret.save()
		dn_ret.submit()

		expected_returns = dn.packed_products
		for d in expected_returns:
			d.qty /= 2
		self.assertReturns(expected_returns, dn_ret.packed_products)
