# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.meta import get_field_precision
from frappe.utils import flt

import erpnext
from erpnext.controllers.taxes_and_totals import init_landed_taxes_and_totals
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos


class LandedCostVoucher(Document):
	@frappe.whitelist()
	def get_products_from_purchase_receipts(self):
		self.set("products", [])
		for pr in self.get("purchase_receipts"):
			if pr.receipt_document_type and pr.receipt_document:
				pr_products = frappe.db.sql(
					"""select pr_product.product_code, pr_product.description,
					pr_product.qty, pr_product.base_rate, pr_product.base_amount, pr_product.name,
					pr_product.cost_center, pr_product.is_fixed_asset
					from `tab{doctype} Product` pr_product where parent = %s
					and exists(select name from tabProduct
						where name = pr_product.product_code and (is_stock_product = 1 or is_fixed_asset=1))
					""".format(
						doctype=pr.receipt_document_type
					),
					pr.receipt_document,
					as_dict=True,
				)

				for d in pr_products:
					product = self.append("products")
					product.product_code = d.product_code
					product.description = d.description
					product.qty = d.qty
					product.rate = d.base_rate
					product.cost_center = d.cost_center or erpnext.get_default_cost_center(self.company)
					product.amount = d.base_amount
					product.receipt_document_type = pr.receipt_document_type
					product.receipt_document = pr.receipt_document
					product.purchase_receipt_product = d.name
					product.is_fixed_asset = d.is_fixed_asset

	def validate(self):
		self.check_mandatory()
		self.validate_receipt_documents()
		init_landed_taxes_and_totals(self)
		self.set_total_taxes_and_charges()
		if not self.get("products"):
			self.get_products_from_purchase_receipts()

		self.set_applicable_charges_on_product()

	def check_mandatory(self):
		if not self.get("purchase_receipts"):
			frappe.throw(_("Please enter Receipt Document"))

	def validate_receipt_documents(self):
		receipt_documents = []

		for d in self.get("purchase_receipts"):
			docstatus = frappe.db.get_value(d.receipt_document_type, d.receipt_document, "docstatus")
			if docstatus != 1:
				msg = (
					f"Row {d.idx}: {d.receipt_document_type} {frappe.bold(d.receipt_document)} must be submitted"
				)
				frappe.throw(_(msg), title=_("Invalid Document"))

			if d.receipt_document_type == "Purchase Invoice":
				update_stock = frappe.db.get_value(d.receipt_document_type, d.receipt_document, "update_stock")
				if not update_stock:
					msg = _("Row {0}: Purchase Invoice {1} has no stock impact.").format(
						d.idx, frappe.bold(d.receipt_document)
					)
					msg += "<br>" + _(
						"Please create Landed Cost Vouchers against Invoices that have 'Update Stock' enabled."
					)
					frappe.throw(msg, title=_("Incorrect Invoice"))

			receipt_documents.append(d.receipt_document)

		for product in self.get("products"):
			if not product.receipt_document:
				frappe.throw(_("Product must be added using 'Get Products from Purchase Receipts' button"))

			elif product.receipt_document not in receipt_documents:
				frappe.throw(
					_("Product Row {0}: {1} {2} does not exist in above '{1}' table").format(
						product.idx, product.receipt_document_type, product.receipt_document
					)
				)

			if not product.cost_center:
				frappe.throw(
					_("Row {0}: Cost center is required for an product {1}").format(product.idx, product.product_code)
				)

	def set_total_taxes_and_charges(self):
		self.total_taxes_and_charges = sum(flt(d.base_amount) for d in self.get("taxes"))

	def set_applicable_charges_on_product(self):
		if self.get("taxes") and self.distribute_charges_based_on != "Distribute Manually":
			total_product_cost = 0.0
			total_charges = 0.0
			product_count = 0
			based_on_field = frappe.scrub(self.distribute_charges_based_on)

			for product in self.get("products"):
				total_product_cost += product.get(based_on_field)

			for product in self.get("products"):
				if not total_product_cost and not product.get(based_on_field):
					frappe.throw(
						_(
							"It's not possible to distribute charges equally when total amount is zero, please set 'Distribute Charges Based On' as 'Quantity'"
						)
					)

				product.applicable_charges = flt(
					flt(product.get(based_on_field)) * (flt(self.total_taxes_and_charges) / flt(total_product_cost)),
					product.precision("applicable_charges"),
				)
				total_charges += product.applicable_charges
				product_count += 1

			if total_charges != self.total_taxes_and_charges:
				diff = self.total_taxes_and_charges - total_charges
				self.get("products")[product_count - 1].applicable_charges += diff

	def validate_applicable_charges_for_product(self):
		based_on = self.distribute_charges_based_on.lower()

		if based_on != "distribute manually":
			total = sum(flt(d.get(based_on)) for d in self.get("products"))
		else:
			# consider for proportion while distributing manually
			total = sum(flt(d.get("applicable_charges")) for d in self.get("products"))

		if not total:
			frappe.throw(
				_(
					"Total {0} for all products is zero, may be you should change 'Distribute Charges Based On'"
				).format(based_on)
			)

		total_applicable_charges = sum(flt(d.applicable_charges) for d in self.get("products"))

		precision = get_field_precision(
			frappe.get_meta("Landed Cost Product").get_field("applicable_charges"),
			currency=frappe.get_cached_value("Company", self.company, "default_currency"),
		)

		diff = flt(self.total_taxes_and_charges) - flt(total_applicable_charges)
		diff = flt(diff, precision)

		if abs(diff) < (2.0 / (10**precision)):
			self.products[-1].applicable_charges += diff
		else:
			frappe.throw(
				_(
					"Total Applicable Charges in Purchase Receipt Products table must be same as Total Taxes and Charges"
				)
			)

	def on_submit(self):
		self.validate_applicable_charges_for_product()
		self.update_landed_cost()

	def on_cancel(self):
		self.update_landed_cost()

	def update_landed_cost(self):
		for d in self.get("purchase_receipts"):
			doc = frappe.get_doc(d.receipt_document_type, d.receipt_document)
			# check if there are {qty} assets created and linked to this receipt document
			self.validate_asset_qty_and_status(d.receipt_document_type, doc)

			# set landed cost voucher amount in pr product
			doc.set_landed_cost_voucher_amount()

			# set valuation amount in pr product
			doc.update_valuation_rate(reset_outgoing_rate=False)

			# db_update will update and save landed_cost_voucher_amount and voucher_amount in PR
			for product in doc.get("products"):
				product.db_update()

			# asset rate will be updated while creating asset gl entries from PI or PY

			# update latest valuation rate in serial no
			self.update_rate_in_serial_no_for_non_asset_products(doc)

		for d in self.get("purchase_receipts"):
			doc = frappe.get_doc(d.receipt_document_type, d.receipt_document)
			# update stock & gl entries for cancelled state of PR
			doc.docstatus = 2
			doc.update_stock_ledger(allow_negative_stock=True, via_landed_cost_voucher=True)
			doc.make_gl_entries_on_cancel()

			# update stock & gl entries for submit state of PR
			doc.docstatus = 1
			doc.update_stock_ledger(allow_negative_stock=True, via_landed_cost_voucher=True)
			doc.make_gl_entries()
			doc.repost_future_sle_and_gle()

	def validate_asset_qty_and_status(self, receipt_document_type, receipt_document):
		for product in self.get("products"):
			if product.is_fixed_asset:
				receipt_document_type = (
					"purchase_invoice" if product.receipt_document_type == "Purchase Invoice" else "purchase_receipt"
				)
				docs = frappe.db.get_all(
					"Asset",
					filters={receipt_document_type: product.receipt_document, "product_code": product.product_code},
					fields=["name", "docstatus"],
				)
				if not docs or len(docs) != product.qty:
					frappe.throw(
						_(
							"There are not enough asset created or linked to {0}. Please create or link {1} Assets with respective document."
						).format(product.receipt_document, product.qty)
					)
				if docs:
					for d in docs:
						if d.docstatus == 1:
							frappe.throw(
								_(
									"{2} <b>{0}</b> has submitted Assets. Remove Product <b>{1}</b> from table to continue."
								).format(
									product.receipt_document, product.product_code, product.receipt_document_type
								)
							)

	def update_rate_in_serial_no_for_non_asset_products(self, receipt_document):
		for product in receipt_document.get("products"):
			if not product.is_fixed_asset and product.serial_no:
				serial_nos = get_serial_nos(product.serial_no)
				if serial_nos:
					frappe.db.sql(
						"update `tabSerial No` set purchase_rate=%s where name in ({0})".format(
							", ".join(["%s"] * len(serial_nos))
						),
						tuple([product.valuation_rate] + serial_nos),
					)
