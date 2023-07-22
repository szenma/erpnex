# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

import erpnext
from erpnext.accounts.utils import get_account_currency
from erpnext.controllers.subcontracting_controller import SubcontractingController


class SubcontractingReceipt(SubcontractingController):
	def __init__(self, *args, **kwargs):
		super(SubcontractingReceipt, self).__init__(*args, **kwargs)
		self.status_updater = [
			{
				"target_dt": "Subcontracting Order Product",
				"join_field": "subcontracting_order_product",
				"target_field": "received_qty",
				"target_parent_dt": "Subcontracting Order",
				"target_parent_field": "per_received",
				"target_ref_field": "qty",
				"source_dt": "Subcontracting Receipt Product",
				"source_field": "received_qty",
				"percent_join_field": "subcontracting_order",
				"overflow_type": "receipt",
			},
		]

	def onload(self):
		self.set_onload(
			"backflush_based_on",
			frappe.db.get_single_value(
				"Buying Settings", "backflush_raw_materials_of_subcontract_based_on"
			),
		)

	def update_status_updater_args(self):
		if cint(self.is_return):
			self.status_updater.extend(
				[
					{
						"source_dt": "Subcontracting Receipt Product",
						"target_dt": "Subcontracting Order Product",
						"join_field": "subcontracting_order_product",
						"target_field": "returned_qty",
						"source_field": "-1 * qty",
						"extra_cond": """ and exists (select name from `tabSubcontracting Receipt`
						where name=`tabSubcontracting Receipt Product`.parent and is_return=1)""",
					},
					{
						"source_dt": "Subcontracting Receipt Product",
						"target_dt": "Subcontracting Receipt Product",
						"join_field": "subcontracting_receipt_product",
						"target_field": "returned_qty",
						"target_parent_dt": "Subcontracting Receipt",
						"target_parent_field": "per_returned",
						"target_ref_field": "received_qty",
						"source_field": "-1 * received_qty",
						"percent_join_field_parent": "return_against",
					},
				]
			)

	def before_validate(self):
		super(SubcontractingReceipt, self).before_validate()
		self.validate_products_qty()
		self.set_products_bom()
		self.set_products_cost_center()
		self.set_products_expense_account()

	def validate(self):
		if (
			frappe.db.get_single_value("Buying Settings", "backflush_raw_materials_of_subcontract_based_on")
			== "BOM"
		):
			self.supplied_products = []
		super(SubcontractingReceipt, self).validate()
		self.set_missing_values()
		self.validate_posting_time()
		self.validate_rejected_warehouse()

		if self._action == "submit":
			self.make_batches("warehouse")

		if getdate(self.posting_date) > getdate(nowdate()):
			frappe.throw(_("Posting Date cannot be future date"))

		self.reset_default_field_value("set_warehouse", "products", "warehouse")
		self.reset_default_field_value("rejected_warehouse", "products", "rejected_warehouse")
		self.get_current_stock()

	def on_submit(self):
		self.validate_available_qty_for_consumption()
		self.update_status_updater_args()
		self.update_prevdoc_status()
		self.set_subcontracting_order_status()
		self.set_consumed_qty_in_subcontract_order()
		self.update_stock_ledger()

		from erpnext.stock.doctype.serial_no.serial_no import update_serial_nos_after_submit

		update_serial_nos_after_submit(self, "products")

		self.make_gl_entries()
		self.repost_future_sle_and_gle()
		self.update_status()

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Repost Product Valuation")
		self.update_status_updater_args()
		self.update_prevdoc_status()
		self.update_stock_ledger()
		self.make_gl_entries_on_cancel()
		self.repost_future_sle_and_gle()
		self.delete_auto_created_batches()
		self.set_consumed_qty_in_subcontract_order()
		self.set_subcontracting_order_status()
		self.update_status()

	@frappe.whitelist()
	def set_missing_values(self):
		self.calculate_additional_costs()
		self.calculate_supplied_products_qty_and_amount()
		self.calculate_products_qty_and_amount()

	def set_available_qty_for_consumption(self):
		supplied_products_details = {}

		sco_supplied_product = frappe.qb.DocType("Subcontracting Order Supplied Product")
		for product in self.get("products"):
			supplied_products = (
				frappe.qb.from_(sco_supplied_product)
				.select(
					sco_supplied_product.rm_product_code,
					sco_supplied_product.reference_name,
					(sco_supplied_product.total_supplied_qty - sco_supplied_product.consumed_qty).as_("available_qty"),
				)
				.where(
					(sco_supplied_product.parent == product.subcontracting_order)
					& (sco_supplied_product.main_product_code == product.product_code)
					& (sco_supplied_product.reference_name == product.subcontracting_order_product)
				)
			).run(as_dict=True)

			if supplied_products:
				supplied_products_details[product.name] = {}

				for supplied_product in supplied_products:
					supplied_products_details[product.name][supplied_product.rm_product_code] = supplied_product.available_qty
		else:
			for product in self.get("supplied_products"):
				product.available_qty_for_consumption = supplied_products_details.get(product.reference_name, {}).get(
					product.rm_product_code, 0
				)

	def calculate_supplied_products_qty_and_amount(self):
		for product in self.get("supplied_products") or []:
			product.amount = product.rate * product.consumed_qty

		self.set_available_qty_for_consumption()

	def calculate_products_qty_and_amount(self):
		rm_supp_cost = {}
		for product in self.get("supplied_products") or []:
			if product.reference_name in rm_supp_cost:
				rm_supp_cost[product.reference_name] += product.amount
			else:
				rm_supp_cost[product.reference_name] = product.amount

		total_qty = total_amount = 0
		for product in self.products:
			if product.qty and product.name in rm_supp_cost:
				product.rm_supp_cost = rm_supp_cost[product.name]
				product.rm_cost_per_qty = product.rm_supp_cost / product.qty
				rm_supp_cost.pop(product.name)

			if product.recalculate_rate:
				product.rate = (
					flt(product.rm_cost_per_qty) + flt(product.service_cost_per_qty) + flt(product.additional_cost_per_qty)
				)

			product.received_qty = product.qty + flt(product.rejected_qty)
			product.amount = product.qty * product.rate
			total_qty += product.qty
			total_amount += product.amount
		else:
			self.total_qty = total_qty
			self.total = total_amount

	def validate_rejected_warehouse(self):
		for product in self.products:
			if flt(product.rejected_qty) and not product.rejected_warehouse:
				if self.rejected_warehouse:
					product.rejected_warehouse = self.rejected_warehouse

				if not product.rejected_warehouse:
					frappe.throw(
						_("Row #{0}: Rejected Warehouse is mandatory for the rejected Product {1}").format(
							product.idx, product.product_code
						)
					)

			if product.get("rejected_warehouse") and (product.get("rejected_warehouse") == product.get("warehouse")):
				frappe.throw(
					_("Row #{0}: Accepted Warehouse and Rejected Warehouse cannot be same").format(product.idx)
				)

	def validate_available_qty_for_consumption(self):
		for product in self.get("supplied_products"):
			precision = product.precision("consumed_qty")
			if (
				product.available_qty_for_consumption
				and flt(product.available_qty_for_consumption, precision) - flt(product.consumed_qty, precision) < 0
			):
				msg = f"""Row {product.idx}: Consumed Qty {flt(product.consumed_qty, precision)}
					must be less than or equal to Available Qty For Consumption
					{flt(product.available_qty_for_consumption, precision)}
					in Consumed Products Table."""

				frappe.throw(_(msg))

	def validate_products_qty(self):
		for product in self.products:
			if not (product.qty or product.rejected_qty):
				frappe.throw(
					_("Row {0}: Accepted Qty and Rejected Qty can't be zero at the same time.").format(product.idx)
				)

	def set_products_bom(self):
		if self.is_return:
			for product in self.products:
				if not product.bom:
					product.bom = frappe.db.get_value(
						"Subcontracting Receipt Product",
						{"name": product.subcontracting_receipt_product, "parent": self.return_against},
						"bom",
					)
		else:
			for product in self.products:
				if not product.bom:
					product.bom = frappe.db.get_value(
						"Subcontracting Order Product",
						{"name": product.subcontracting_order_product, "parent": product.subcontracting_order},
						"bom",
					)

	def set_products_cost_center(self):
		if self.company:
			cost_center = frappe.get_cached_value("Company", self.company, "cost_center")

			for product in self.products:
				if not product.cost_center:
					product.cost_center = cost_center

	def set_products_expense_account(self):
		if self.company:
			expense_account = self.get_company_default("default_expense_account", ignore_validation=True)

			for product in self.products:
				if not product.expense_account:
					product.expense_account = expense_account

	def update_status(self, status=None, update_modified=False):
		if not status:
			if self.docstatus == 0:
				status = "Draft"
			elif self.docstatus == 1:
				status = "Completed"
				if self.is_return:
					status = "Return"
					return_against = frappe.get_doc("Subcontracting Receipt", self.return_against)
					return_against.run_method("update_status")
				elif self.per_returned == 100:
					status = "Return Issued"
			elif self.docstatus == 2:
				status = "Cancelled"

		if status:
			frappe.db.set_value("Subcontracting Receipt", self.name, "status", status, update_modified)

	def get_gl_entries(self, warehouse_account=None):
		from erpnext.accounts.general_ledger import process_gl_map

		if not erpnext.is_perpetual_inventory_enabled(self.company):
			return []

		gl_entries = []
		self.make_product_gl_entries(gl_entries, warehouse_account)

		return process_gl_map(gl_entries)

	def make_product_gl_entries(self, gl_entries, warehouse_account=None):
		stock_rbnb = self.get_company_default("stock_received_but_not_billed")
		expenses_included_in_valuation = self.get_company_default("expenses_included_in_valuation")

		warehouse_with_no_account = []

		for product in self.products:
			if flt(product.rate) and flt(product.qty):
				if warehouse_account.get(product.warehouse):
					stock_value_diff = frappe.db.get_value(
						"Stock Ledger Entry",
						{
							"voucher_type": "Subcontracting Receipt",
							"voucher_no": self.name,
							"voucher_detail_no": product.name,
							"warehouse": product.warehouse,
							"is_cancelled": 0,
						},
						"stock_value_difference",
					)

					warehouse_account_name = warehouse_account[product.warehouse]["account"]
					warehouse_account_currency = warehouse_account[product.warehouse]["account_currency"]
					supplier_warehouse_account = warehouse_account.get(self.supplier_warehouse, {}).get("account")
					supplier_warehouse_account_currency = warehouse_account.get(self.supplier_warehouse, {}).get(
						"account_currency"
					)
					remarks = self.get("remarks") or _("Accounting Entry for Stock")

					# FG Warehouse Account (Debit)
					self.add_gl_entry(
						gl_entries=gl_entries,
						account=warehouse_account_name,
						cost_center=product.cost_center,
						debit=stock_value_diff,
						credit=0.0,
						remarks=remarks,
						against_account=stock_rbnb,
						account_currency=warehouse_account_currency,
						product=product,
					)

					# Supplier Warehouse Account (Credit)
					if flt(product.rm_supp_cost) and warehouse_account.get(self.supplier_warehouse):
						self.add_gl_entry(
							gl_entries=gl_entries,
							account=supplier_warehouse_account,
							cost_center=product.cost_center,
							debit=0.0,
							credit=flt(product.rm_supp_cost),
							remarks=remarks,
							against_account=warehouse_account_name,
							account_currency=supplier_warehouse_account_currency,
							product=product,
						)

					# Expense Account (Credit)
					if flt(product.service_cost_per_qty):
						self.add_gl_entry(
							gl_entries=gl_entries,
							account=product.expense_account,
							cost_center=product.cost_center,
							debit=0.0,
							credit=flt(product.service_cost_per_qty) * flt(product.qty),
							remarks=remarks,
							against_account=warehouse_account_name,
							account_currency=get_account_currency(product.expense_account),
							product=product,
						)

					# Loss Account (Credit)
					divisional_loss = flt(product.amount - stock_value_diff, product.precision("amount"))

					if divisional_loss:
						if self.is_return:
							loss_account = expenses_included_in_valuation
						else:
							loss_account = product.expense_account

						self.add_gl_entry(
							gl_entries=gl_entries,
							account=loss_account,
							cost_center=product.cost_center,
							debit=divisional_loss,
							credit=0.0,
							remarks=remarks,
							against_account=warehouse_account_name,
							account_currency=get_account_currency(loss_account),
							project=product.project,
							product=product,
						)
				elif (
					product.warehouse not in warehouse_with_no_account
					or product.rejected_warehouse not in warehouse_with_no_account
				):
					warehouse_with_no_account.append(product.warehouse)

		# Additional Costs Expense Accounts (Credit)
		for row in self.additional_costs:
			credit_amount = (
				flt(row.base_amount)
				if (row.base_amount or row.account_currency != self.company_currency)
				else flt(row.amount)
			)

			self.add_gl_entry(
				gl_entries=gl_entries,
				account=row.expense_account,
				cost_center=self.cost_center or self.get_company_default("cost_center"),
				debit=0.0,
				credit=credit_amount,
				remarks=remarks,
				against_account=None,
			)

		if warehouse_with_no_account:
			frappe.msgprint(
				_("No accounting entries for the following warehouses")
				+ ": \n"
				+ "\n".join(warehouse_with_no_account)
			)


@frappe.whitelist()
def make_subcontract_return(source_name, target_doc=None):
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	return make_return_doc("Subcontracting Receipt", source_name, target_doc)
