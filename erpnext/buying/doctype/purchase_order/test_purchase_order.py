# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json

import frappe
from frappe.tests.utils import FrappeTestCase, change_settings
from frappe.utils import add_days, flt, getdate, nowdate
from frappe.utils.data import today

from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.accounts.party import get_due_date_from_template
from erpnext.buying.doctype.purchase_order.purchase_order import make_inter_company_sales_order
from erpnext.buying.doctype.purchase_order.purchase_order import (
	make_purchase_invoice as make_pi_from_po,
)
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from erpnext.controllers.accounts_controller import update_child_qty_rate
from erpnext.manufacturing.doctype.blanket_order.test_blanket_order import make_blanket_order
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.doctype.material_request.material_request import make_purchase_order
from erpnext.stock.doctype.material_request.test_material_request import make_material_request
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
	make_purchase_invoice as make_pi_from_pr,
)


class TestPurchaseOrder(FrappeTestCase):
	def test_make_purchase_receipt(self):
		po = create_purchase_order(do_not_submit=True)
		self.assertRaises(frappe.ValidationError, make_purchase_receipt, po.name)
		po.submit()

		pr = create_pr_against_po(po.name)
		self.assertEqual(len(pr.get("products")), 1)

	def test_ordered_qty(self):
		existing_ordered_qty = get_ordered_qty()

		po = create_purchase_order(do_not_submit=True)
		self.assertRaises(frappe.ValidationError, make_purchase_receipt, po.name)

		po.submit()
		self.assertEqual(get_ordered_qty(), existing_ordered_qty + 10)

		create_pr_against_po(po.name)
		self.assertEqual(get_ordered_qty(), existing_ordered_qty + 6)

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 4)

		frappe.db.set_value("Product", "_Test Product", "over_delivery_receipt_allowance", 50)

		pr = create_pr_against_po(po.name, received_qty=8)
		self.assertEqual(get_ordered_qty(), existing_ordered_qty)

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 12)

		pr.cancel()
		self.assertEqual(get_ordered_qty(), existing_ordered_qty + 6)

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 4)

	def test_ordered_qty_against_pi_with_update_stock(self):
		existing_ordered_qty = get_ordered_qty()
		po = create_purchase_order()

		self.assertEqual(get_ordered_qty(), existing_ordered_qty + 10)

		frappe.db.set_value("Product", "_Test Product", "over_delivery_receipt_allowance", 50)
		frappe.db.set_value("Product", "_Test Product", "over_billing_allowance", 20)

		pi = make_pi_from_po(po.name)
		pi.update_stock = 1
		pi.products[0].qty = 12
		pi.insert()
		pi.submit()

		self.assertEqual(get_ordered_qty(), existing_ordered_qty)

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 12)

		pi.cancel()
		self.assertEqual(get_ordered_qty(), existing_ordered_qty + 10)

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 0)

		frappe.db.set_value("Product", "_Test Product", "over_delivery_receipt_allowance", 0)
		frappe.db.set_value("Product", "_Test Product", "over_billing_allowance", 0)
		frappe.db.set_value("Accounts Settings", None, "over_billing_allowance", 0)

	def test_update_remove_child_linked_to_mr(self):
		"""Test impact on linked PO and MR on deleting/updating row."""
		mr = make_material_request(qty=10)
		po = make_purchase_order(mr.name)
		po.supplier = "_Test Supplier"
		po.save()
		po.submit()

		first_product_of_po = po.get("products")[0]
		existing_ordered_qty = get_ordered_qty()  # 10
		existing_requested_qty = get_requested_qty()  # 0

		# decrease ordered qty by 3 (10 -> 7) and add product
		trans_product = json.dumps(
			[
				{
					"product_code": first_product_of_po.product_code,
					"rate": first_product_of_po.rate,
					"qty": 7,
					"docname": first_product_of_po.name,
				},
				{"product_code": "_Test Product 2", "rate": 200, "qty": 2},
			]
		)
		update_child_qty_rate("Purchase Order", trans_product, po.name)
		mr.reload()

		# requested qty increases as ordered qty decreases
		self.assertEqual(get_requested_qty(), existing_requested_qty + 3)  # 3
		self.assertEqual(mr.products[0].ordered_qty, 7)

		self.assertEqual(get_ordered_qty(), existing_ordered_qty - 3)  # 7

		# delete first product linked to Material Request
		trans_product = json.dumps([{"product_code": "_Test Product 2", "rate": 200, "qty": 2}])
		update_child_qty_rate("Purchase Order", trans_product, po.name)
		mr.reload()

		# requested qty increases as ordered qty is 0 (deleted row)
		self.assertEqual(get_requested_qty(), existing_requested_qty + 10)  # 10
		self.assertEqual(mr.products[0].ordered_qty, 0)

		# ordered qty decreases as ordered qty is 0 (deleted row)
		self.assertEqual(get_ordered_qty(), existing_ordered_qty - 10)  # 0

	def test_update_child(self):
		mr = make_material_request(qty=10)
		po = make_purchase_order(mr.name)
		po.supplier = "_Test Supplier"
		po.products[0].qty = 4
		po.save()
		po.submit()

		create_pr_against_po(po.name)

		make_pi_from_po(po.name)

		existing_ordered_qty = get_ordered_qty()
		existing_requested_qty = get_requested_qty()

		trans_product = json.dumps(
			[{"product_code": "_Test Product", "rate": 200, "qty": 7, "docname": po.products[0].name}]
		)
		update_child_qty_rate("Purchase Order", trans_product, po.name)

		mr.reload()
		self.assertEqual(mr.products[0].ordered_qty, 7)
		self.assertEqual(mr.per_ordered, 70)
		self.assertEqual(get_requested_qty(), existing_requested_qty - 3)

		po.reload()
		self.assertEqual(po.get("products")[0].rate, 200)
		self.assertEqual(po.get("products")[0].qty, 7)
		self.assertEqual(po.get("products")[0].amount, 1400)
		self.assertEqual(get_ordered_qty(), existing_ordered_qty + 3)

	def test_update_child_adding_new_product(self):
		po = create_purchase_order(do_not_save=1)
		po.products[0].qty = 4
		po.save()
		po.submit()
		pr = make_pr_against_po(po.name, 2)

		po.load_from_db()
		existing_ordered_qty = get_ordered_qty()
		first_product_of_po = po.get("products")[0]

		trans_product = json.dumps(
			[
				{
					"product_code": first_product_of_po.product_code,
					"rate": first_product_of_po.rate,
					"qty": first_product_of_po.qty,
					"docname": first_product_of_po.name,
				},
				{"product_code": "_Test Product", "rate": 200, "qty": 7},
			]
		)
		update_child_qty_rate("Purchase Order", trans_product, po.name)

		po.reload()
		self.assertEqual(len(po.get("products")), 2)
		self.assertEqual(po.status, "To Receive and Bill")
		# ordered qty should increase on row addition
		self.assertEqual(get_ordered_qty(), existing_ordered_qty + 7)

	def test_update_child_removing_product(self):
		po = create_purchase_order(do_not_save=1)
		po.products[0].qty = 4
		po.save()
		po.submit()
		pr = make_pr_against_po(po.name, 2)

		po.reload()
		first_product_of_po = po.get("products")[0]
		existing_ordered_qty = get_ordered_qty()
		# add an product
		trans_product = json.dumps(
			[
				{
					"product_code": first_product_of_po.product_code,
					"rate": first_product_of_po.rate,
					"qty": first_product_of_po.qty,
					"docname": first_product_of_po.name,
				},
				{"product_code": "_Test Product", "rate": 200, "qty": 7},
			]
		)
		update_child_qty_rate("Purchase Order", trans_product, po.name)

		po.reload()

		# ordered qty should increase on row addition
		self.assertEqual(get_ordered_qty(), existing_ordered_qty + 7)

		# check if can remove received product
		trans_product = json.dumps(
			[{"product_code": "_Test Product", "rate": 200, "qty": 7, "docname": po.get("products")[1].name}]
		)
		self.assertRaises(
			frappe.ValidationError, update_child_qty_rate, "Purchase Order", trans_product, po.name
		)

		first_product_of_po = po.get("products")[0]
		trans_product = json.dumps(
			[
				{
					"product_code": first_product_of_po.product_code,
					"rate": first_product_of_po.rate,
					"qty": first_product_of_po.qty,
					"docname": first_product_of_po.name,
				}
			]
		)
		update_child_qty_rate("Purchase Order", trans_product, po.name)

		po.reload()
		self.assertEqual(len(po.get("products")), 1)
		self.assertEqual(po.status, "To Receive and Bill")

		# ordered qty should decrease (back to initial) on row deletion
		self.assertEqual(get_ordered_qty(), existing_ordered_qty)

	def test_update_child_perm(self):
		po = create_purchase_order(product_code="_Test Product", qty=4)

		user = "test@example.com"
		test_user = frappe.get_doc("User", user)
		test_user.add_roles("Accounts User")
		frappe.set_user(user)

		# update qty
		trans_product = json.dumps(
			[{"product_code": "_Test Product", "rate": 200, "qty": 7, "docname": po.products[0].name}]
		)
		self.assertRaises(
			frappe.ValidationError, update_child_qty_rate, "Purchase Order", trans_product, po.name
		)

		# add new product
		trans_product = json.dumps([{"product_code": "_Test Product", "rate": 100, "qty": 2}])
		self.assertRaises(
			frappe.ValidationError, update_child_qty_rate, "Purchase Order", trans_product, po.name
		)
		frappe.set_user("Administrator")

	def test_update_child_with_tax_template(self):
		"""
		Test Action: Create a PO with one product having its tax account head already in the PO.
		Add the same product + new product with tax template via Update Products.
		Expected result: First Product's tax row is updated. New tax row is added for second Product.
		"""
		if not frappe.db.exists("Product", "Test Product with Tax"):
			make_product(
				"Test Product with Tax",
				{
					"is_stock_product": 1,
				},
			)

		if not frappe.db.exists("Product Tax Template", {"title": "Test Update Products Template"}):
			frappe.get_doc(
				{
					"doctype": "Product Tax Template",
					"title": "Test Update Products Template",
					"company": "_Test Company",
					"taxes": [
						{
							"tax_type": "_Test Account Service Tax - _TC",
							"tax_rate": 10,
						}
					],
				}
			).insert()

		new_product_with_tax = frappe.get_doc("Product", "Test Product with Tax")

		if not frappe.db.exists(
			"Product Tax",
			{"product_tax_template": "Test Update Products Template - _TC", "parent": "Test Product with Tax"},
		):
			new_product_with_tax.append(
				"taxes", {"product_tax_template": "Test Update Products Template - _TC", "valid_from": nowdate()}
			)
			new_product_with_tax.save()

		tax_template = "_Test Account Excise Duty @ 10 - _TC"
		product = "_Test Product Home Desktop 100"
		if not frappe.db.exists("Product Tax", {"parent": product, "product_tax_template": tax_template}):
			product_doc = frappe.get_doc("Product", product)
			product_doc.append("taxes", {"product_tax_template": tax_template, "valid_from": nowdate()})
			product_doc.save()
		else:
			# update valid from
			frappe.db.sql(
				"""UPDATE `tabProduct Tax` set valid_from = CURRENT_DATE
				where parent = %(product)s and product_tax_template = %(tax)s""",
				{"product": product, "tax": tax_template},
			)

		po = create_purchase_order(product_code=product, qty=1, do_not_save=1)

		po.append(
			"taxes",
			{
				"account_head": "_Test Account Excise Duty - _TC",
				"charge_type": "On Net Total",
				"cost_center": "_Test Cost Center - _TC",
				"description": "Excise Duty",
				"doctype": "Purchase Taxes and Charges",
				"rate": 10,
			},
		)
		po.insert()
		po.submit()

		self.assertEqual(po.taxes[0].tax_amount, 50)
		self.assertEqual(po.taxes[0].total, 550)

		products = json.dumps(
			[
				{"product_code": product, "rate": 500, "qty": 1, "docname": po.products[0].name},
				{
					"product_code": product,
					"rate": 100,
					"qty": 1,
				},  # added product whose tax account head already exists in PO
				{
					"product_code": new_product_with_tax.name,
					"rate": 100,
					"qty": 1,
				},  # added product whose tax account head  is missing in PO
			]
		)
		update_child_qty_rate("Purchase Order", products, po.name)

		po.reload()
		self.assertEqual(po.taxes[0].tax_amount, 70)
		self.assertEqual(po.taxes[0].total, 770)
		self.assertEqual(po.taxes[1].account_head, "_Test Account Service Tax - _TC")
		self.assertEqual(po.taxes[1].tax_amount, 70)
		self.assertEqual(po.taxes[1].total, 840)

		# teardown
		frappe.db.sql(
			"""UPDATE `tabProduct Tax` set valid_from = NULL
			where parent = %(product)s and product_tax_template = %(tax)s""",
			{"product": product, "tax": tax_template},
		)
		po.cancel()
		po.delete()
		new_product_with_tax.delete()
		frappe.get_doc("Product Tax Template", "Test Update Products Template - _TC").delete()

	def test_update_qty(self):
		po = create_purchase_order()

		pr = make_pr_against_po(po.name, 2)

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 2)

		# Check received_qty after making PI from PR without update_stock checked
		pi1 = make_pi_from_pr(pr.name)
		pi1.get("products")[0].qty = 2
		pi1.insert()
		pi1.submit()

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 2)

		# Check received_qty after making PI from PO with update_stock checked
		pi2 = make_pi_from_po(po.name)
		pi2.set("update_stock", 1)
		pi2.get("products")[0].qty = 3
		pi2.insert()
		pi2.submit()

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 5)

		# Check received_qty after making PR from PO
		pr = make_pr_against_po(po.name, 1)

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 6)

	def test_return_against_purchase_order(self):
		po = create_purchase_order()

		pr = make_pr_against_po(po.name, 6)

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 6)

		pi2 = make_pi_from_po(po.name)
		pi2.set("update_stock", 1)
		pi2.get("products")[0].qty = 3
		pi2.insert()
		pi2.submit()

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 9)

		# Make return purchase receipt, purchase invoice and check quantity
		from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import (
			make_purchase_invoice as make_purchase_invoice_return,
		)
		from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import (
			make_purchase_receipt as make_purchase_receipt_return,
		)

		pr1 = make_purchase_receipt_return(
			is_return=1, return_against=pr.name, qty=-3, do_not_submit=True
		)
		pr1.products[0].purchase_order = po.name
		pr1.products[0].purchase_order_product = po.products[0].name
		pr1.submit()

		pi1 = make_purchase_invoice_return(
			is_return=1, return_against=pi2.name, qty=-1, update_stock=1, do_not_submit=True
		)
		pi1.products[0].purchase_order = po.name
		pi1.products[0].po_detail = po.products[0].name
		pi1.submit()

		po.load_from_db()
		self.assertEqual(po.get("products")[0].received_qty, 5)

	def test_purchase_order_invoice_receipt_workflow(self):
		from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import make_purchase_receipt

		po = create_purchase_order()
		pi = make_pi_from_po(po.name)

		pi.submit()

		pr = make_purchase_receipt(pi.name)
		pr.submit()

		pi.load_from_db()

		self.assertEqual(pi.per_received, 100.00)
		self.assertEqual(pi.products[0].qty, pi.products[0].received_qty)

		po.load_from_db()

		self.assertEqual(po.per_received, 100.00)
		self.assertEqual(po.per_billed, 100.00)

		pr.cancel()

		pi.load_from_db()
		pi.cancel()

		po.load_from_db()
		po.cancel()

	def test_make_purchase_invoice(self):
		po = create_purchase_order(do_not_submit=True)

		self.assertRaises(frappe.ValidationError, make_pi_from_po, po.name)

		po.submit()
		pi = make_pi_from_po(po.name)

		self.assertEqual(pi.doctype, "Purchase Invoice")
		self.assertEqual(len(pi.get("products", [])), 1)

	def test_purchase_order_on_hold(self):
		po = create_purchase_order(product_code="_Test Product Bundle Product")
		po.db_set("Status", "On Hold")
		pi = make_pi_from_po(po.name)
		pr = make_purchase_receipt(po.name)
		self.assertRaises(frappe.ValidationError, pr.submit)
		self.assertRaises(frappe.ValidationError, pi.submit)

	def test_make_purchase_invoice_with_terms(self):
		from erpnext.selling.doctype.sales_order.test_sales_order import (
			automatically_fetch_payment_terms,
		)

		automatically_fetch_payment_terms()
		po = create_purchase_order(do_not_save=True)

		self.assertRaises(frappe.ValidationError, make_pi_from_po, po.name)

		po.update({"payment_terms_template": "_Test Payment Term Template"})

		po.save()
		po.submit()

		self.assertEqual(po.payment_schedule[0].payment_amount, 2500.0)
		self.assertEqual(getdate(po.payment_schedule[0].due_date), getdate(po.transaction_date))
		self.assertEqual(po.payment_schedule[1].payment_amount, 2500.0)
		self.assertEqual(
			getdate(po.payment_schedule[1].due_date), add_days(getdate(po.transaction_date), 30)
		)
		pi = make_pi_from_po(po.name)
		pi.save()

		self.assertEqual(pi.doctype, "Purchase Invoice")
		self.assertEqual(len(pi.get("products", [])), 1)

		self.assertEqual(pi.payment_schedule[0].payment_amount, 2500.0)
		self.assertEqual(getdate(pi.payment_schedule[0].due_date), getdate(po.transaction_date))
		self.assertEqual(pi.payment_schedule[1].payment_amount, 2500.0)
		self.assertEqual(
			getdate(pi.payment_schedule[1].due_date), add_days(getdate(po.transaction_date), 30)
		)
		automatically_fetch_payment_terms(enable=0)

	def test_warehouse_company_validation(self):
		from erpnext.stock.utils import InvalidWarehouseCompany

		po = create_purchase_order(company="_Test Company 1", do_not_save=True)
		self.assertRaises(InvalidWarehouseCompany, po.insert)

	def test_uom_integer_validation(self):
		from erpnext.utilities.transaction_base import UOMMustBeIntegerError

		po = create_purchase_order(qty=3.4, do_not_save=True)
		self.assertRaises(UOMMustBeIntegerError, po.insert)

	def test_ordered_qty_for_closing_po(self):
		bin = frappe.get_all(
			"Bin",
			filters={"product_code": "_Test Product", "warehouse": "_Test Warehouse - _TC"},
			fields=["ordered_qty"],
		)

		existing_ordered_qty = bin[0].ordered_qty if bin else 0.0

		po = create_purchase_order(product_code="_Test Product", qty=1)

		self.assertEqual(
			get_ordered_qty(product_code="_Test Product", warehouse="_Test Warehouse - _TC"),
			existing_ordered_qty + 1,
		)

		po.update_status("Closed")

		self.assertEqual(
			get_ordered_qty(product_code="_Test Product", warehouse="_Test Warehouse - _TC"), existing_ordered_qty
		)

	def test_group_same_products(self):
		frappe.db.set_value("Buying Settings", None, "allow_multiple_products", 1)
		frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"company": "_Test Company",
				"supplier": "_Test Supplier",
				"is_subcontracted": 0,
				"schedule_date": add_days(nowdate(), 1),
				"currency": frappe.get_cached_value("Company", "_Test Company", "default_currency"),
				"conversion_factor": 1,
				"products": get_same_products(),
				"group_same_products": 1,
			}
		).insert(ignore_permissions=True)

	def test_make_po_without_terms(self):
		po = create_purchase_order(do_not_save=1)

		self.assertFalse(po.get("payment_schedule"))

		po.insert()

		self.assertTrue(po.get("payment_schedule"))

	def test_po_for_blocked_supplier_all(self):
		supplier = frappe.get_doc("Supplier", "_Test Supplier")
		supplier.on_hold = 1
		supplier.save()

		self.assertEqual(supplier.hold_type, "All")
		self.assertRaises(frappe.ValidationError, create_purchase_order)

		supplier.on_hold = 0
		supplier.save()

	def test_po_for_blocked_supplier_invoices(self):
		supplier = frappe.get_doc("Supplier", "_Test Supplier")
		supplier.on_hold = 1
		supplier.hold_type = "Invoices"
		supplier.save()

		self.assertRaises(frappe.ValidationError, create_purchase_order)

		supplier.on_hold = 0
		supplier.save()

	def test_po_for_blocked_supplier_payments(self):
		supplier = frappe.get_doc("Supplier", "_Test Supplier")
		supplier.on_hold = 1
		supplier.hold_type = "Payments"
		supplier.save()

		po = create_purchase_order()

		self.assertRaises(
			frappe.ValidationError,
			get_payment_entry,
			dt="Purchase Order",
			dn=po.name,
			bank_account="_Test Bank - _TC",
		)

		supplier.on_hold = 0
		supplier.save()

	def test_po_for_blocked_supplier_payments_with_today_date(self):
		supplier = frappe.get_doc("Supplier", "_Test Supplier")
		supplier.on_hold = 1
		supplier.release_date = nowdate()
		supplier.hold_type = "Payments"
		supplier.save()

		po = create_purchase_order()

		self.assertRaises(
			frappe.ValidationError,
			get_payment_entry,
			dt="Purchase Order",
			dn=po.name,
			bank_account="_Test Bank - _TC",
		)

		supplier.on_hold = 0
		supplier.save()

	def test_po_for_blocked_supplier_payments_past_date(self):
		# this test is meant to fail only if something fails in the try block
		with self.assertRaises(Exception):
			try:
				supplier = frappe.get_doc("Supplier", "_Test Supplier")
				supplier.on_hold = 1
				supplier.hold_type = "Payments"
				supplier.release_date = "2018-03-01"
				supplier.save()

				po = create_purchase_order()
				get_payment_entry("Purchase Order", po.name, bank_account="_Test Bank - _TC")

				supplier.on_hold = 0
				supplier.save()
			except:
				pass
			else:
				raise Exception

	def test_default_payment_terms(self):
		due_date = get_due_date_from_template(
			"_Test Payment Term Template 1", "2023-02-03", None
		).strftime("%Y-%m-%d")
		self.assertEqual(due_date, "2023-03-31")

	def test_terms_are_not_copied_if_automatically_fetch_payment_terms_is_unchecked(self):
		po = create_purchase_order(do_not_save=1)
		po.payment_terms_template = "_Test Payment Term Template"
		po.save()
		po.submit()

		frappe.db.set_value("Company", "_Test Company", "payment_terms", "_Test Payment Term Template 1")
		pi = make_pi_from_po(po.name)
		pi.save()

		self.assertEqual(pi.get("payment_terms_template"), "_Test Payment Term Template 1")
		frappe.db.set_value("Company", "_Test Company", "payment_terms", "")

	def test_terms_copied(self):
		po = create_purchase_order(do_not_save=1)
		po.payment_terms_template = "_Test Payment Term Template"
		po.insert()
		po.submit()
		self.assertTrue(po.get("payment_schedule"))

		pi = make_pi_from_po(po.name)
		pi.insert()
		self.assertTrue(pi.get("payment_schedule"))

	@change_settings("Accounts Settings", {"unlink_advance_payment_on_cancelation_of_order": 1})
	def test_advance_payment_entry_unlink_against_purchase_order(self):
		from erpnext.accounts.doctype.payment_entry.test_payment_entry import get_payment_entry

		po_doc = create_purchase_order()

		pe = get_payment_entry("Purchase Order", po_doc.name, bank_account="_Test Bank - _TC")
		pe.reference_no = "1"
		pe.reference_date = nowdate()
		pe.paid_from_account_currency = po_doc.currency
		pe.paid_to_account_currency = po_doc.currency
		pe.source_exchange_rate = 1
		pe.target_exchange_rate = 1
		pe.paid_amount = po_doc.grand_total
		pe.save(ignore_permissions=True)
		pe.submit()

		po_doc = frappe.get_doc("Purchase Order", po_doc.name)
		po_doc.cancel()

		pe_doc = frappe.get_doc("Payment Entry", pe.name)
		pe_doc.cancel()

	@change_settings("Accounts Settings", {"unlink_advance_payment_on_cancelation_of_order": 1})
	def test_advance_paid_upon_payment_entry_cancellation(self):
		from erpnext.accounts.doctype.payment_entry.test_payment_entry import get_payment_entry

		po_doc = create_purchase_order(supplier="_Test Supplier USD", currency="USD", do_not_submit=1)
		po_doc.conversion_rate = 80
		po_doc.submit()

		pe = get_payment_entry("Purchase Order", po_doc.name)
		pe.mode_of_payment = "Cash"
		pe.paid_from = "Cash - _TC"
		pe.source_exchange_rate = 1
		pe.target_exchange_rate = 80
		pe.paid_amount = po_doc.base_grand_total
		pe.save(ignore_permissions=True)
		pe.submit()

		po_doc.reload()
		self.assertEqual(po_doc.advance_paid, po_doc.grand_total)
		self.assertEqual(po_doc.party_account_currency, "USD")

		pe_doc = frappe.get_doc("Payment Entry", pe.name)
		pe_doc.cancel()

		po_doc.reload()
		self.assertEqual(po_doc.advance_paid, 0)
		self.assertEqual(po_doc.party_account_currency, "USD")

	def test_schedule_date(self):
		po = create_purchase_order(do_not_submit=True)
		po.schedule_date = None
		po.append(
			"products",
			{"product_code": "_Test Product", "qty": 1, "rate": 100, "schedule_date": add_days(nowdate(), 5)},
		)
		po.save()
		self.assertEqual(po.schedule_date, add_days(nowdate(), 1))

		po.products[0].schedule_date = add_days(nowdate(), 2)
		po.save()
		self.assertEqual(po.schedule_date, add_days(nowdate(), 2))

	def test_po_optional_blanket_order(self):
		"""
		Expected result: Blanket order Ordered Quantity should only be affected on Purchase Order with against_blanket_order = 1.
		Second Purchase Order should not add on to Blanket Orders Ordered Quantity.
		"""

		bo = make_blanket_order(blanket_order_type="Purchasing", quantity=10, rate=10)

		po = create_purchase_order(product_code="_Test Product", qty=5, against_blanket_order=1)
		po_doc = frappe.get_doc("Purchase Order", po.get("name"))
		# To test if the PO has a Blanket Order
		self.assertTrue(po_doc.products[0].blanket_order)

		po = create_purchase_order(product_code="_Test Product", qty=5, against_blanket_order=0)
		po_doc = frappe.get_doc("Purchase Order", po.get("name"))
		# To test if the PO does NOT have a Blanket Order
		self.assertEqual(po_doc.products[0].blanket_order, None)

	def test_payment_terms_are_fetched_when_creating_purchase_invoice(self):
		from erpnext.accounts.doctype.payment_entry.test_payment_entry import (
			create_payment_terms_template,
		)
		from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
		from erpnext.selling.doctype.sales_order.test_sales_order import (
			automatically_fetch_payment_terms,
			compare_payment_schedules,
		)

		automatically_fetch_payment_terms()

		po = create_purchase_order(qty=10, rate=100, do_not_save=1)
		create_payment_terms_template()
		po.payment_terms_template = "Test Receivable Template"
		po.submit()

		pi = make_purchase_invoice(qty=10, rate=100, do_not_save=1)
		pi.products[0].purchase_order = po.name
		pi.products[0].po_detail = po.products[0].name
		pi.insert()

		# self.assertEqual(po.payment_terms_template, pi.payment_terms_template)
		compare_payment_schedules(self, po, pi)

		automatically_fetch_payment_terms(enable=0)

	def test_internal_transfer_flow(self):
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import (
			make_inter_company_purchase_invoice,
		)
		from erpnext.selling.doctype.sales_order.sales_order import (
			make_delivery_note,
			make_sales_invoice,
		)
		from erpnext.stock.doctype.delivery_note.delivery_note import make_inter_company_purchase_receipt

		frappe.db.set_value("Selling Settings", None, "maintain_same_sales_rate", 1)
		frappe.db.set_value("Buying Settings", None, "maintain_same_rate", 1)

		prepare_data_for_internal_transfer()
		supplier = "_Test Internal Supplier 2"

		mr = make_material_request(
			qty=2, company="_Test Company with perpetual inventory", warehouse="Stores - TCP1"
		)

		po = create_purchase_order(
			company="_Test Company with perpetual inventory",
			supplier=supplier,
			warehouse="Stores - TCP1",
			from_warehouse="_Test Internal Warehouse New 1 - TCP1",
			qty=2,
			rate=1,
			material_request=mr.name,
			material_request_product=mr.products[0].name,
		)

		so = make_inter_company_sales_order(po.name)
		so.products[0].delivery_date = today()
		self.assertEqual(so.products[0].warehouse, "_Test Internal Warehouse New 1 - TCP1")
		self.assertTrue(so.products[0].purchase_order)
		self.assertTrue(so.products[0].purchase_order_product)
		so.submit()

		dn = make_delivery_note(so.name)
		dn.products[0].target_warehouse = "_Test Internal Warehouse GIT - TCP1"
		self.assertEqual(dn.products[0].warehouse, "_Test Internal Warehouse New 1 - TCP1")
		self.assertTrue(dn.products[0].purchase_order)
		self.assertTrue(dn.products[0].purchase_order_product)

		self.assertEqual(po.products[0].name, dn.products[0].purchase_order_product)
		dn.submit()

		pr = make_inter_company_purchase_receipt(dn.name)
		self.assertEqual(pr.products[0].warehouse, "Stores - TCP1")
		self.assertTrue(pr.products[0].purchase_order)
		self.assertTrue(pr.products[0].purchase_order_product)
		self.assertEqual(po.products[0].name, pr.products[0].purchase_order_product)
		pr.submit()

		si = make_sales_invoice(so.name)
		self.assertEqual(si.products[0].warehouse, "_Test Internal Warehouse New 1 - TCP1")
		self.assertTrue(si.products[0].purchase_order)
		self.assertTrue(si.products[0].purchase_order_product)
		si.submit()

		pi = make_inter_company_purchase_invoice(si.name)
		self.assertTrue(pi.products[0].purchase_order)
		self.assertTrue(pi.products[0].po_detail)
		pi.submit()
		mr.reload()

		po.load_from_db()
		self.assertEqual(po.status, "Completed")
		self.assertEqual(mr.status, "Received")

	def test_variant_product_po(self):
		po = create_purchase_order(product_code="_Test Variant Product", qty=1, rate=100, do_not_save=1)

		self.assertRaises(frappe.ValidationError, po.save)


def prepare_data_for_internal_transfer():
	from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_internal_supplier
	from erpnext.selling.doctype.customer.test_customer import create_internal_customer
	from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt
	from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse

	company = "_Test Company with perpetual inventory"

	create_internal_customer(
		"_Test Internal Customer 2",
		company,
		company,
	)

	create_internal_supplier(
		"_Test Internal Supplier 2",
		company,
		company,
	)

	warehouse = create_warehouse("_Test Internal Warehouse New 1", company=company)

	create_warehouse("_Test Internal Warehouse GIT", company=company)

	make_purchase_receipt(company=company, warehouse=warehouse, qty=2, rate=100)

	if not frappe.db.get_value("Company", company, "unrealized_profit_loss_account"):
		account = "Unrealized Profit and Loss - TCP1"
		if not frappe.db.exists("Account", account):
			frappe.get_doc(
				{
					"doctype": "Account",
					"account_name": "Unrealized Profit and Loss",
					"parent_account": "Direct Income - TCP1",
					"company": company,
					"is_group": 0,
					"account_type": "Income Account",
				}
			).insert()

		frappe.db.set_value("Company", company, "unrealized_profit_loss_account", account)


def make_pr_against_po(po, received_qty=0):
	pr = make_purchase_receipt(po)
	pr.get("products")[0].qty = received_qty or 5
	pr.insert()
	pr.submit()
	return pr


def get_same_products():
	return [
		{
			"product_code": "_Test FG Product",
			"warehouse": "_Test Warehouse - _TC",
			"qty": 1,
			"rate": 500,
			"schedule_date": add_days(nowdate(), 1),
		},
		{
			"product_code": "_Test FG Product",
			"warehouse": "_Test Warehouse - _TC",
			"qty": 4,
			"rate": 500,
			"schedule_date": add_days(nowdate(), 1),
		},
	]


def create_purchase_order(**args):
	po = frappe.new_doc("Purchase Order")
	args = frappe._dict(args)
	if args.transaction_date:
		po.transaction_date = args.transaction_date

	po.schedule_date = add_days(nowdate(), 1)
	po.company = args.company or "_Test Company"
	po.supplier = args.supplier or "_Test Supplier"
	po.is_subcontracted = args.is_subcontracted or 0
	po.currency = args.currency or frappe.get_cached_value("Company", po.company, "default_currency")
	po.conversion_factor = args.conversion_factor or 1
	po.supplier_warehouse = args.supplier_warehouse or None

	if args.rm_products:
		for row in args.rm_products:
			po.append("products", row)
	else:
		po.append(
			"products",
			{
				"product_code": args.product or args.product_code or "_Test Product",
				"warehouse": args.warehouse or "_Test Warehouse - _TC",
				"from_warehouse": args.from_warehouse,
				"qty": args.qty or 10,
				"rate": args.rate or 500,
				"schedule_date": add_days(nowdate(), 1),
				"include_exploded_products": args.get("include_exploded_products", 1),
				"against_blanket_order": args.against_blanket_order,
				"material_request": args.material_request,
				"material_request_product": args.material_request_product,
			},
		)

	if not args.do_not_save:
		po.set_missing_values()
		po.insert()
		if not args.do_not_submit:
			if po.is_subcontracted:
				supp_products = po.get("supplied_products")
				for d in supp_products:
					if not d.reserve_warehouse:
						d.reserve_warehouse = args.warehouse or "_Test Warehouse - _TC"
			po.submit()

	return po


def create_pr_against_po(po, received_qty=4):
	pr = make_purchase_receipt(po)
	pr.get("products")[0].qty = received_qty
	pr.insert()
	pr.submit()
	return pr


def get_ordered_qty(product_code="_Test Product", warehouse="_Test Warehouse - _TC"):
	return flt(
		frappe.db.get_value("Bin", {"product_code": product_code, "warehouse": warehouse}, "ordered_qty")
	)


def get_requested_qty(product_code="_Test Product", warehouse="_Test Warehouse - _TC"):
	return flt(
		frappe.db.get_value("Bin", {"product_code": product_code, "warehouse": warehouse}, "indented_qty")
	)


test_dependencies = ["BOM", "Product Price"]

test_records = frappe.get_test_records("Purchase Order")
