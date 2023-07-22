# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder.functions import Sum
from frappe.utils import (
	cint,
	comma_or,
	cstr,
	flt,
	format_time,
	formatdate,
	getdate,
	month_diff,
	nowdate,
)

import erpnext
from erpnext.accounts.general_ledger import process_gl_map
from erpnext.controllers.taxes_and_totals import init_landed_taxes_and_totals
from erpnext.manufacturing.doctype.bom.bom import add_additional_cost, validate_bom_no
from erpnext.setup.doctype.brand.brand import get_brand_defaults
from erpnext.setup.doctype.product_group.product_group import get_product_group_defaults
from erpnext.stock.doctype.batch.batch import get_batch_no, get_batch_qty, set_batch_nos
from erpnext.stock.doctype.product.product import get_product_defaults
from erpnext.stock.doctype.serial_no.serial_no import (
	get_serial_nos,
	update_serial_nos_after_submit,
)
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import (
	OpeningEntryAccountError,
)
from erpnext.stock.get_product_details import (
	get_bin_details,
	get_conversion_factor,
	get_default_cost_center,
	get_reserved_qty_for_so,
)
from erpnext.stock.stock_ledger import NegativeStockError, get_previous_sle, get_valuation_rate
from erpnext.stock.utils import get_bin, get_incoming_rate


class FinishedGoodError(frappe.ValidationError):
	pass


class IncorrectValuationRateError(frappe.ValidationError):
	pass


class DuplicateEntryForWorkOrderError(frappe.ValidationError):
	pass


class OperationsNotCompleteError(frappe.ValidationError):
	pass


class MaxSampleAlreadyRetainedError(frappe.ValidationError):
	pass


from erpnext.controllers.stock_controller import StockController

form_grid_templates = {"products": "templates/form_grid/stock_entry_grid.html"}


class StockEntry(StockController):
	def __init__(self, *args, **kwargs):
		super(StockEntry, self).__init__(*args, **kwargs)
		if self.purchase_order:
			self.subcontract_data = frappe._dict(
				{
					"order_doctype": "Purchase Order",
					"order_field": "purchase_order",
					"rm_detail_field": "po_detail",
					"order_supplied_products_field": "Purchase Order Product Supplied",
				}
			)
		else:
			self.subcontract_data = frappe._dict(
				{
					"order_doctype": "Subcontracting Order",
					"order_field": "subcontracting_order",
					"rm_detail_field": "sco_rm_detail",
					"order_supplied_products_field": "Subcontracting Order Supplied Product",
				}
			)

	def get_feed(self):
		return self.stock_entry_type

	def onload(self):
		for product in self.get("products"):
			product.update(get_bin_details(product.product_code, product.s_warehouse))

	def before_validate(self):
		from erpnext.stock.doctype.putaway_rule.putaway_rule import apply_putaway_rule

		apply_rule = self.apply_putaway_rule and (
			self.purpose in ["Material Transfer", "Material Receipt"]
		)

		if self.get("products") and apply_rule:
			apply_putaway_rule(self.doctype, self.get("products"), self.company, purpose=self.purpose)

	def validate(self):
		self.pro_doc = frappe._dict()
		if self.work_order:
			self.pro_doc = frappe.get_doc("Work Order", self.work_order)

		self.validate_posting_time()
		self.validate_purpose()
		self.validate_product()
		self.validate_customer_provided_product()
		self.validate_qty()
		self.set_transfer_qty()
		self.validate_uom_is_integer("uom", "qty")
		self.validate_uom_is_integer("stock_uom", "transfer_qty")
		self.validate_warehouse()
		self.validate_work_order()
		self.validate_bom()
		self.set_process_loss_qty()
		self.validate_purchase_order()
		self.validate_subcontracting_order()

		if self.purpose in ("Manufacture", "Repack"):
			self.mark_finished_and_scrap_products()
			self.validate_finished_goods()

		self.validate_with_material_request()
		self.validate_batch()
		self.validate_inspection()
		self.validate_fg_completed_qty()
		self.validate_difference_account()
		self.set_job_card_data()
		self.validate_job_card_product()
		self.set_purpose_for_stock_entry()
		self.clean_serial_nos()
		self.validate_duplicate_serial_no()

		if not self.from_bom:
			self.fg_completed_qty = 0.0

		if self._action == "submit":
			self.make_batches("t_warehouse")
		else:
			set_batch_nos(self, "s_warehouse")

		self.validate_serialized_batch()
		self.set_actual_qty()
		self.calculate_rate_and_amount()
		self.validate_putaway_capacity()

		if not self.get("purpose") == "Manufacture":
			# ignore scrap product wh difference and empty source/target wh
			# in Manufacture Entry
			self.reset_default_field_value("from_warehouse", "products", "s_warehouse")
			self.reset_default_field_value("to_warehouse", "products", "t_warehouse")

	def submit(self):
		if self.is_enqueue_action():
			frappe.msgprint(
				_(
					"The task has been enqueued as a background job. In case there is any issue on processing in background, the system will add a comment about the error on this Stock Reconciliation and revert to the Draft stage"
				)
			)
			self.queue_action("submit", timeout=2000)
		else:
			self._submit()

	def cancel(self):
		if self.is_enqueue_action():
			frappe.msgprint(
				_(
					"The task has been enqueued as a background job. In case there is any issue on processing in background, the system will add a comment about the error on this Stock Reconciliation and revert to the Submitted stage"
				)
			)
			self.queue_action("cancel", timeout=2000)
		else:
			self._cancel()

	def is_enqueue_action(self, force=False) -> bool:
		if force:
			return True

		if frappe.flags.in_test:
			return False

		# If line products are more than 100 or record is older than 6 months
		if len(self.products) > 100 or month_diff(nowdate(), self.posting_date) > 6:
			return True

		return False

	def on_submit(self):
		self.update_stock_ledger()

		update_serial_nos_after_submit(self, "products")
		self.update_work_order()
		self.validate_subcontract_order()
		self.update_subcontract_order_supplied_products()
		self.update_subcontracting_order_status()
		self.update_pick_list_status()

		self.make_gl_entries()

		self.repost_future_sle_and_gle()
		self.update_cost_in_project()
		self.validate_reserved_serial_no_consumption()
		self.update_transferred_qty()
		self.update_quality_inspection()

		if self.work_order and self.purpose == "Manufacture":
			self.update_so_in_serial_number()

		if self.purpose == "Material Transfer" and self.add_to_transit:
			self.set_material_request_transfer_status("In Transit")
		if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
			self.set_material_request_transfer_status("Completed")

	def on_cancel(self):
		self.update_subcontract_order_supplied_products()
		self.update_subcontracting_order_status()

		if self.work_order and self.purpose == "Material Consumption for Manufacture":
			self.validate_work_order_status()

		self.update_work_order()
		self.update_stock_ledger()

		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Repost Product Valuation")

		self.make_gl_entries_on_cancel()
		self.repost_future_sle_and_gle()
		self.update_cost_in_project()
		self.update_transferred_qty()
		self.update_quality_inspection()
		self.delete_auto_created_batches()
		self.delete_linked_stock_entry()

		if self.purpose == "Material Transfer" and self.add_to_transit:
			self.set_material_request_transfer_status("Not Started")
		if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
			self.set_material_request_transfer_status("In Transit")

	def set_job_card_data(self):
		if self.job_card and not self.work_order:
			data = frappe.db.get_value(
				"Job Card", self.job_card, ["for_quantity", "work_order", "bom_no"], as_dict=1
			)
			self.fg_completed_qty = data.for_quantity
			self.work_order = data.work_order
			self.from_bom = 1
			self.bom_no = data.bom_no

	def validate_job_card_product(self):
		if not self.job_card:
			return

		if cint(frappe.db.get_single_value("Manufacturing Settings", "job_card_excess_transfer")):
			return

		for row in self.products:
			if row.job_card_product or not row.s_warehouse:
				continue

			msg = f"""Row #{row.idx}: The job card product reference
				is missing. Kindly create the stock entry
				from the job card. If you have added the row manually
				then you won't be able to add job card product reference."""

			frappe.throw(_(msg))

	def validate_work_order_status(self):
		pro_doc = frappe.get_doc("Work Order", self.work_order)
		if pro_doc.status == "Completed":
			frappe.throw(_("Cannot cancel transaction for Completed Work Order."))

	def validate_purpose(self):
		valid_purposes = [
			"Material Issue",
			"Material Receipt",
			"Material Transfer",
			"Material Transfer for Manufacture",
			"Manufacture",
			"Repack",
			"Send to Subcontractor",
			"Material Consumption for Manufacture",
		]

		if self.purpose not in valid_purposes:
			frappe.throw(_("Purpose must be one of {0}").format(comma_or(valid_purposes)))

		if self.job_card and self.purpose not in ["Material Transfer for Manufacture", "Repack"]:
			frappe.throw(
				_(
					"For job card {0}, you can only make the 'Material Transfer for Manufacture' type stock entry"
				).format(self.job_card)
			)

	def delete_linked_stock_entry(self):
		if self.purpose == "Send to Warehouse":
			for d in frappe.get_all(
				"Stock Entry",
				filters={"docstatus": 0, "outgoing_stock_entry": self.name, "purpose": "Receive at Warehouse"},
			):
				frappe.delete_doc("Stock Entry", d.name)

	def set_transfer_qty(self):
		for product in self.get("products"):
			if not flt(product.qty):
				frappe.throw(_("Row {0}: Qty is mandatory").format(product.idx), title=_("Zero quantity"))
			if not flt(product.conversion_factor):
				frappe.throw(_("Row {0}: UOM Conversion Factor is mandatory").format(product.idx))
			product.transfer_qty = flt(
				flt(product.qty) * flt(product.conversion_factor), self.precision("transfer_qty", product)
			)
			if not flt(product.transfer_qty):
				frappe.throw(
					_("Row {0}: Qty in Stock UOM can not be zero.").format(product.idx), title=_("Zero quantity")
				)

	def update_cost_in_project(self):
		if self.work_order and not frappe.db.get_value(
			"Work Order", self.work_order, "update_consumed_material_cost_in_project"
		):
			return

		if self.project:
			amount = frappe.db.sql(
				""" select ifnull(sum(sed.amount), 0)
				from
					`tabStock Entry` se, `tabStock Entry Detail` sed
				where
					se.docstatus = 1 and se.project = %s and sed.parent = se.name
					and (sed.t_warehouse is null or sed.t_warehouse = '')""",
				self.project,
				as_list=1,
			)

			amount = amount[0][0] if amount else 0
			additional_costs = frappe.db.sql(
				""" select ifnull(sum(sed.base_amount), 0)
				from
					`tabStock Entry` se, `tabLanded Cost Taxes and Charges` sed
				where
					se.docstatus = 1 and se.project = %s and sed.parent = se.name
					and se.purpose = 'Manufacture'""",
				self.project,
				as_list=1,
			)

			additional_cost_amt = additional_costs[0][0] if additional_costs else 0

			amount += additional_cost_amt
			frappe.db.set_value("Project", self.project, "total_consumed_material_cost", amount)

	def validate_product(self):
		stock_products = self.get_stock_products()
		serialized_products = self.get_serialized_products()
		for product in self.get("products"):
			if flt(product.qty) and flt(product.qty) < 0:
				frappe.throw(
					_("Row {0}: The product {1}, quantity must be positive number").format(
						product.idx, frappe.bold(product.product_code)
					)
				)

			if product.product_code not in stock_products:
				frappe.throw(_("{0} is not a stock Product").format(product.product_code))

			product_details = self.get_product_details(
				frappe._dict(
					{
						"product_code": product.product_code,
						"company": self.company,
						"project": self.project,
						"uom": product.uom,
						"s_warehouse": product.s_warehouse,
					}
				),
				for_update=True,
			)

			reset_fields = ("stock_uom", "product_name")
			for field in reset_fields:
				product.set(field, product_details.get(field))

			update_fields = ("uom", "description", "expense_account", "cost_center", "conversion_factor")

			for field in update_fields:
				if not product.get(field):
					product.set(field, product_details.get(field))
				if field == "conversion_factor" and product.uom == product_details.get("stock_uom"):
					product.set(field, product_details.get(field))

			if not product.transfer_qty and product.qty:
				product.transfer_qty = flt(
					flt(product.qty) * flt(product.conversion_factor), self.precision("transfer_qty", product)
				)

			if (
				self.purpose in ("Material Transfer", "Material Transfer for Manufacture")
				and not product.serial_no
				and product.product_code in serialized_products
			):
				frappe.throw(
					_("Row #{0}: Please specify Serial No for Product {1}").format(product.idx, product.product_code),
					frappe.MandatoryError,
				)

	def validate_qty(self):
		manufacture_purpose = ["Manufacture", "Material Consumption for Manufacture"]

		if self.purpose in manufacture_purpose and self.work_order:
			if not frappe.get_value("Work Order", self.work_order, "skip_transfer"):
				product_code = []
				for product in self.products:
					if cstr(product.t_warehouse) == "":
						req_products = frappe.get_all(
							"Work Order Product",
							filters={"parent": self.work_order, "product_code": product.product_code},
							fields=["product_code"],
						)

						transferred_materials = frappe.db.sql(
							"""
									select
										sum(qty) as qty
									from `tabStock Entry` se,`tabStock Entry Detail` sed
									where
										se.name = sed.parent and se.docstatus=1 and
										(se.purpose='Material Transfer for Manufacture' or se.purpose='Manufacture')
										and sed.product_code=%s and se.work_order= %s and ifnull(sed.t_warehouse, '') != ''
								""",
							(product.product_code, self.work_order),
							as_dict=1,
						)

						stock_qty = flt(product.qty)
						trans_qty = flt(transferred_materials[0].qty)
						if req_products:
							if stock_qty > trans_qty:
								product_code.append(product.product_code)

	def validate_fg_completed_qty(self):
		product_wise_qty = {}
		if self.purpose == "Manufacture" and self.work_order:
			for d in self.products:
				if d.is_finished_product:
					if self.process_loss_qty:
						d.qty = self.fg_completed_qty - self.process_loss_qty

					product_wise_qty.setdefault(d.product_code, []).append(d.qty)

		precision = frappe.get_precision("Stock Entry Detail", "qty")
		for product_code, qty_list in product_wise_qty.products():
			total = flt(sum(qty_list), precision)

			if (self.fg_completed_qty - total) > 0 and not self.process_loss_qty:
				self.process_loss_qty = flt(self.fg_completed_qty - total, precision)
				self.process_loss_percentage = flt(self.process_loss_qty * 100 / self.fg_completed_qty)

			if self.process_loss_qty:
				total += flt(self.process_loss_qty, precision)

			if self.fg_completed_qty != total:
				frappe.throw(
					_("The finished product {0} quantity {1} and For Quantity {2} cannot be different").format(
						frappe.bold(product_code), frappe.bold(total), frappe.bold(self.fg_completed_qty)
					)
				)

	def validate_difference_account(self):
		if not cint(erpnext.is_perpetual_inventory_enabled(self.company)):
			return

		for d in self.get("products"):
			if not d.expense_account:
				frappe.throw(
					_(
						"Please enter <b>Difference Account</b> or set default <b>Stock Adjustment Account</b> for company {0}"
					).format(frappe.bold(self.company))
				)

			elif (
				self.is_opening == "Yes"
				and frappe.db.get_value("Account", d.expense_account, "report_type") == "Profit and Loss"
			):
				frappe.throw(
					_(
						"Difference Account must be a Asset/Liability type account, since this Stock Entry is an Opening Entry"
					),
					OpeningEntryAccountError,
				)

	def validate_warehouse(self):
		"""perform various (sometimes conditional) validations on warehouse"""

		source_mandatory = [
			"Material Issue",
			"Material Transfer",
			"Send to Subcontractor",
			"Material Transfer for Manufacture",
			"Material Consumption for Manufacture",
		]

		target_mandatory = [
			"Material Receipt",
			"Material Transfer",
			"Send to Subcontractor",
			"Material Transfer for Manufacture",
		]

		validate_for_manufacture = any([d.bom_no for d in self.get("products")])

		if self.purpose in source_mandatory and self.purpose not in target_mandatory:
			self.to_warehouse = None
			for d in self.get("products"):
				d.t_warehouse = None
		elif self.purpose in target_mandatory and self.purpose not in source_mandatory:
			self.from_warehouse = None
			for d in self.get("products"):
				d.s_warehouse = None

		for d in self.get("products"):
			if not d.s_warehouse and not d.t_warehouse:
				d.s_warehouse = self.from_warehouse
				d.t_warehouse = self.to_warehouse

			if self.purpose in source_mandatory and not d.s_warehouse:
				if self.from_warehouse:
					d.s_warehouse = self.from_warehouse
				else:
					frappe.throw(_("Source warehouse is mandatory for row {0}").format(d.idx))

			if self.purpose in target_mandatory and not d.t_warehouse:
				if self.to_warehouse:
					d.t_warehouse = self.to_warehouse
				else:
					frappe.throw(_("Target warehouse is mandatory for row {0}").format(d.idx))

			if self.purpose == "Manufacture":
				if validate_for_manufacture:
					if d.is_finished_product or d.is_scrap_product:
						d.s_warehouse = None
						if not d.t_warehouse:
							frappe.throw(_("Target warehouse is mandatory for row {0}").format(d.idx))
					else:
						d.t_warehouse = None
						if not d.s_warehouse:
							frappe.throw(_("Source warehouse is mandatory for row {0}").format(d.idx))

			if cstr(d.s_warehouse) == cstr(d.t_warehouse) and self.purpose not in [
				"Material Transfer for Manufacture",
				"Material Transfer",
			]:
				frappe.throw(_("Source and target warehouse cannot be same for row {0}").format(d.idx))

			if not (d.s_warehouse or d.t_warehouse):
				frappe.throw(_("Atleast one warehouse is mandatory"))

	def validate_work_order(self):
		if self.purpose in (
			"Manufacture",
			"Material Transfer for Manufacture",
			"Material Consumption for Manufacture",
		):
			# check if work order is entered

			if (
				self.purpose == "Manufacture" or self.purpose == "Material Consumption for Manufacture"
			) and self.work_order:
				if not self.fg_completed_qty:
					frappe.throw(_("For Quantity (Manufactured Qty) is mandatory"))
				self.check_if_operations_completed()
				self.check_duplicate_entry_for_work_order()
		elif self.purpose != "Material Transfer":
			self.work_order = None

	def check_if_operations_completed(self):
		"""Check if Time Sheets are completed against before manufacturing to capture operating costs."""
		prod_order = frappe.get_doc("Work Order", self.work_order)
		allowance_percentage = flt(
			frappe.db.get_single_value("Manufacturing Settings", "overproduction_percentage_for_work_order")
		)

		for d in prod_order.get("operations"):
			total_completed_qty = flt(self.fg_completed_qty) + flt(prod_order.produced_qty)
			completed_qty = (
				d.completed_qty + d.process_loss_qty + (allowance_percentage / 100 * d.completed_qty)
			)
			if total_completed_qty > flt(completed_qty):
				job_card = frappe.db.get_value("Job Card", {"operation_id": d.name}, "name")
				if not job_card:
					frappe.throw(
						_("Work Order {0}: Job Card not found for the operation {1}").format(
							self.work_order, d.operation
						)
					)

				work_order_link = frappe.utils.get_link_to_form("Work Order", self.work_order)
				job_card_link = frappe.utils.get_link_to_form("Job Card", job_card)
				frappe.throw(
					_(
						"Row #{0}: Operation {1} is not completed for {2} qty of finished goods in Work Order {3}. Please update operation status via Job Card {4}."
					).format(
						d.idx,
						frappe.bold(d.operation),
						frappe.bold(total_completed_qty),
						work_order_link,
						job_card_link,
					),
					OperationsNotCompleteError,
				)

	def check_duplicate_entry_for_work_order(self):
		other_ste = [
			t[0]
			for t in frappe.db.get_values(
				"Stock Entry",
				{
					"work_order": self.work_order,
					"purpose": self.purpose,
					"docstatus": ["!=", 2],
					"name": ["!=", self.name],
				},
				"name",
			)
		]

		if other_ste:
			production_product, qty = frappe.db.get_value(
				"Work Order", self.work_order, ["production_product", "qty"]
			)
			args = other_ste + [production_product]
			fg_qty_already_entered = frappe.db.sql(
				"""select sum(transfer_qty)
				from `tabStock Entry Detail`
				where parent in (%s)
					and product_code = %s
					and ifnull(s_warehouse,'')='' """
				% (", ".join(["%s" * len(other_ste)]), "%s"),
				args,
			)[0][0]
			if fg_qty_already_entered and fg_qty_already_entered >= qty:
				frappe.throw(
					_("Stock Entries already created for Work Order {0}: {1}").format(
						self.work_order, ", ".join(other_ste)
					),
					DuplicateEntryForWorkOrderError,
				)

	def set_actual_qty(self):
		from erpnext.stock.stock_ledger import is_negative_stock_allowed

		for d in self.get("products"):
			allow_negative_stock = is_negative_stock_allowed(product_code=d.product_code)
			previous_sle = get_previous_sle(
				{
					"product_code": d.product_code,
					"warehouse": d.s_warehouse or d.t_warehouse,
					"posting_date": self.posting_date,
					"posting_time": self.posting_time,
				}
			)

			# get actual stock at source warehouse
			d.actual_qty = previous_sle.get("qty_after_transaction") or 0

			# validate qty during submit
			if (
				d.docstatus == 1
				and d.s_warehouse
				and not allow_negative_stock
				and flt(d.actual_qty, d.precision("actual_qty"))
				< flt(d.transfer_qty, d.precision("actual_qty"))
			):
				frappe.throw(
					_(
						"Row {0}: Quantity not available for {4} in warehouse {1} at posting time of the entry ({2} {3})"
					).format(
						d.idx,
						frappe.bold(d.s_warehouse),
						formatdate(self.posting_date),
						format_time(self.posting_time),
						frappe.bold(d.product_code),
					)
					+ "<br><br>"
					+ _("Available quantity is {0}, you need {1}").format(
						frappe.bold(flt(d.actual_qty, d.precision("actual_qty"))), frappe.bold(d.transfer_qty)
					),
					NegativeStockError,
					title=_("Insufficient Stock"),
				)

	@frappe.whitelist()
	def get_stock_and_rate(self):
		"""
		Updates rate and availability of all the products.
		Called from Update Rate and Availability button.
		"""
		self.set_work_order_details()
		self.set_transfer_qty()
		self.set_actual_qty()
		self.calculate_rate_and_amount()

	def calculate_rate_and_amount(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		self.set_basic_rate(reset_outgoing_rate, raise_error_if_no_rate)
		init_landed_taxes_and_totals(self)
		self.distribute_additional_costs()
		self.update_valuation_rate()
		self.set_total_incoming_outgoing_value()
		self.set_total_amount()

	def set_basic_rate(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		"""
		Set rate for outgoing, scrapped and finished products
		"""
		# Set rate for outgoing products
		outgoing_products_cost = self.set_rate_for_outgoing_products(
			reset_outgoing_rate, raise_error_if_no_rate
		)
		finished_product_qty = sum(d.transfer_qty for d in self.products if d.is_finished_product)

		products = []
		# Set basic rate for incoming products
		for d in self.get("products"):
			if d.s_warehouse or d.set_basic_rate_manually:
				continue

			if d.allow_zero_valuation_rate:
				d.basic_rate = 0.0
				products.append(d.product_code)

			elif d.is_finished_product:
				if self.purpose == "Manufacture":
					d.basic_rate = self.get_basic_rate_for_manufactured_product(
						finished_product_qty, outgoing_products_cost
					)
				elif self.purpose == "Repack":
					d.basic_rate = self.get_basic_rate_for_repacked_products(d.transfer_qty, outgoing_products_cost)

			if not d.basic_rate and not d.allow_zero_valuation_rate:
				d.basic_rate = get_valuation_rate(
					d.product_code,
					d.t_warehouse,
					self.doctype,
					self.name,
					d.allow_zero_valuation_rate,
					currency=erpnext.get_company_currency(self.company),
					company=self.company,
					raise_error_if_no_rate=raise_error_if_no_rate,
					batch_no=d.batch_no,
				)

			# do not round off basic rate to avoid precision loss
			d.basic_rate = flt(d.basic_rate)
			d.basic_amount = flt(flt(d.transfer_qty) * flt(d.basic_rate), d.precision("basic_amount"))

		if products:
			message = ""

			if len(products) > 1:
				message = _(
					"Products rate has been updated to zero as Allow Zero Valuation Rate is checked for the following products: {0}"
				).format(", ".join(frappe.bold(product) for product in products))
			else:
				message = _(
					"Product rate has been updated to zero as Allow Zero Valuation Rate is checked for product {0}"
				).format(frappe.bold(products[0]))

			frappe.msgprint(message, alert=True)

	def set_rate_for_outgoing_products(self, reset_outgoing_rate=True, raise_error_if_no_rate=True):
		outgoing_products_cost = 0.0
		for d in self.get("products"):
			if d.s_warehouse:
				if reset_outgoing_rate:
					args = self.get_args_for_incoming_rate(d)
					rate = get_incoming_rate(args, raise_error_if_no_rate)
					if rate > 0:
						d.basic_rate = rate

				d.basic_amount = flt(flt(d.transfer_qty) * flt(d.basic_rate), d.precision("basic_amount"))
				if not d.t_warehouse:
					outgoing_products_cost += flt(d.basic_amount)

		return outgoing_products_cost

	def get_args_for_incoming_rate(self, product):
		return frappe._dict(
			{
				"product_code": product.product_code,
				"warehouse": product.s_warehouse or product.t_warehouse,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": product.s_warehouse and -1 * flt(product.transfer_qty) or flt(product.transfer_qty),
				"serial_no": product.serial_no,
				"batch_no": product.batch_no,
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"company": self.company,
				"allow_zero_valuation": product.allow_zero_valuation_rate,
			}
		)

	def get_basic_rate_for_repacked_products(self, finished_product_qty, outgoing_products_cost):
		finished_products = [d.product_code for d in self.get("products") if d.is_finished_product]
		if len(finished_products) == 1:
			return flt(outgoing_products_cost / finished_product_qty)
		else:
			unique_finished_products = set(finished_products)
			if len(unique_finished_products) == 1:
				total_fg_qty = sum([flt(d.transfer_qty) for d in self.products if d.is_finished_product])
				return flt(outgoing_products_cost / total_fg_qty)

	def get_basic_rate_for_manufactured_product(self, finished_product_qty, outgoing_products_cost=0) -> float:
		scrap_products_cost = sum([flt(d.basic_amount) for d in self.get("products") if d.is_scrap_product])

		# Get raw materials cost from BOM if multiple material consumption entries
		if not outgoing_products_cost and frappe.db.get_single_value(
			"Manufacturing Settings", "material_consumption", cache=True
		):
			bom_products = self.get_bom_raw_materials(finished_product_qty)
			outgoing_products_cost = sum([flt(row.qty) * flt(row.rate) for row in bom_products.values()])

		return flt((outgoing_products_cost - scrap_products_cost) / finished_product_qty)

	def distribute_additional_costs(self):
		# If no incoming products, set additional costs blank
		if not any(d.product_code for d in self.products if d.t_warehouse):
			self.additional_costs = []

		self.total_additional_costs = sum(flt(t.base_amount) for t in self.get("additional_costs"))

		if self.purpose in ("Repack", "Manufacture"):
			incoming_products_cost = sum(flt(t.basic_amount) for t in self.get("products") if t.is_finished_product)
		else:
			incoming_products_cost = sum(flt(t.basic_amount) for t in self.get("products") if t.t_warehouse)

		if not incoming_products_cost:
			return

		for d in self.get("products"):
			if self.purpose in ("Repack", "Manufacture") and not d.is_finished_product:
				d.additional_cost = 0
				continue
			elif not d.t_warehouse:
				d.additional_cost = 0
				continue
			d.additional_cost = (flt(d.basic_amount) / incoming_products_cost) * self.total_additional_costs

	def update_valuation_rate(self):
		for d in self.get("products"):
			if d.transfer_qty:
				d.amount = flt(flt(d.basic_amount) + flt(d.additional_cost), d.precision("amount"))
				# Do not round off valuation rate to avoid precision loss
				d.valuation_rate = flt(d.basic_rate) + (flt(d.additional_cost) / flt(d.transfer_qty))

	def set_total_incoming_outgoing_value(self):
		self.total_incoming_value = self.total_outgoing_value = 0.0
		for d in self.get("products"):
			if d.t_warehouse:
				self.total_incoming_value += flt(d.amount)
			if d.s_warehouse:
				self.total_outgoing_value += flt(d.amount)

		self.value_difference = self.total_incoming_value - self.total_outgoing_value

	def set_total_amount(self):
		self.total_amount = None
		if self.purpose not in ["Manufacture", "Repack"]:
			self.total_amount = sum([flt(product.amount) for product in self.get("products")])

	def set_stock_entry_type(self):
		if self.purpose:
			self.stock_entry_type = frappe.get_cached_value(
				"Stock Entry Type", {"purpose": self.purpose}, "name"
			)

	def set_purpose_for_stock_entry(self):
		if self.stock_entry_type and not self.purpose:
			self.purpose = frappe.get_cached_value("Stock Entry Type", self.stock_entry_type, "purpose")

	def validate_duplicate_serial_no(self):
		warehouse_wise_serial_nos = {}

		# In case of repack the source and target serial nos could be same
		for warehouse in ["s_warehouse", "t_warehouse"]:
			serial_nos = []
			for row in self.products:
				if not (row.serial_no and row.get(warehouse)):
					continue

				for sn in get_serial_nos(row.serial_no):
					if sn in serial_nos:
						frappe.throw(
							_("The serial no {0} has added multiple times in the stock entry {1}").format(
								frappe.bold(sn), self.name
							)
						)

					serial_nos.append(sn)

	def validate_subcontract_order(self):
		"""Throw exception if more raw material is transferred against Subcontract Order than in
		the raw materials supplied table"""
		backflush_raw_materials_based_on = frappe.db.get_single_value(
			"Buying Settings", "backflush_raw_materials_of_subcontract_based_on"
		)

		qty_allowance = flt(frappe.db.get_single_value("Buying Settings", "over_transfer_allowance"))

		if not (self.purpose == "Send to Subcontractor" and self.get(self.subcontract_data.order_field)):
			return

		if backflush_raw_materials_based_on == "BOM":
			subcontract_order = frappe.get_doc(
				self.subcontract_data.order_doctype, self.get(self.subcontract_data.order_field)
			)
			for se_product in self.products:
				product_code = se_product.original_product or se_product.product_code
				precision = cint(frappe.db.get_default("float_precision")) or 3
				required_qty = sum(
					[flt(d.required_qty) for d in subcontract_order.supplied_products if d.rm_product_code == product_code]
				)

				total_allowed = required_qty + (required_qty * (qty_allowance / 100))

				if not required_qty:
					bom_no = frappe.db.get_value(
						f"{self.subcontract_data.order_doctype} Product",
						{
							"parent": self.get(self.subcontract_data.order_field),
							"product_code": se_product.subcontracted_product,
						},
						"bom",
					)

					if se_product.allow_alternative_product:
						original_product_code = frappe.get_value(
							"Product Alternative", {"alternative_product_code": product_code}, "product_code"
						)

						required_qty = sum(
							[
								flt(d.required_qty)
								for d in subcontract_order.supplied_products
								if d.rm_product_code == original_product_code
							]
						)

						total_allowed = required_qty + (required_qty * (qty_allowance / 100))

				if not required_qty:
					frappe.throw(
						_("Product {0} not found in 'Raw Materials Supplied' table in {1} {2}").format(
							se_product.product_code,
							self.subcontract_data.order_doctype,
							self.get(self.subcontract_data.order_field),
						)
					)

				se = frappe.qb.DocType("Stock Entry")
				se_detail = frappe.qb.DocType("Stock Entry Detail")

				total_supplied = (
					frappe.qb.from_(se)
					.inner_join(se_detail)
					.on(se.name == se_detail.parent)
					.select(Sum(se_detail.transfer_qty))
					.where(
						(se.purpose == "Send to Subcontractor")
						& (se.docstatus == 1)
						& (se_detail.product_code == se_product.product_code)
						& (
							(se.purchase_order == self.purchase_order)
							if self.subcontract_data.order_doctype == "Purchase Order"
							else (se.subcontracting_order == self.subcontracting_order)
						)
					)
				).run()[0][0]

				if flt(total_supplied, precision) > flt(total_allowed, precision):
					frappe.throw(
						_("Row {0}# Product {1} cannot be transferred more than {2} against {3} {4}").format(
							se_product.idx,
							se_product.product_code,
							total_allowed,
							self.subcontract_data.order_doctype,
							self.get(self.subcontract_data.order_field),
						)
					)
				elif not se_product.get(self.subcontract_data.rm_detail_field):
					filters = {
						"parent": self.get(self.subcontract_data.order_field),
						"docstatus": 1,
						"rm_product_code": se_product.product_code,
						"main_product_code": se_product.subcontracted_product,
					}

					order_rm_detail = frappe.db.get_value(
						self.subcontract_data.order_supplied_products_field, filters, "name"
					)
					if order_rm_detail:
						se_product.db_set(self.subcontract_data.rm_detail_field, order_rm_detail)
					else:
						if not se_product.allow_alternative_product:
							frappe.throw(
								_("Row {0}# Product {1} not found in 'Raw Materials Supplied' table in {2} {3}").format(
									se_product.idx,
									se_product.product_code,
									self.subcontract_data.order_doctype,
									self.get(self.subcontract_data.order_field),
								)
							)
		elif backflush_raw_materials_based_on == "Material Transferred for Subcontract":
			for row in self.products:
				if not row.subcontracted_product:
					frappe.throw(
						_("Row {0}: Subcontracted Product is mandatory for the raw material {1}").format(
							row.idx, frappe.bold(row.product_code)
						)
					)
				elif not row.get(self.subcontract_data.rm_detail_field):
					filters = {
						"parent": self.get(self.subcontract_data.order_field),
						"docstatus": 1,
						"rm_product_code": row.product_code,
						"main_product_code": row.subcontracted_product,
					}

					order_rm_detail = frappe.db.get_value(
						self.subcontract_data.order_supplied_products_field, filters, "name"
					)
					if order_rm_detail:
						row.db_set(self.subcontract_data.rm_detail_field, order_rm_detail)

	def validate_bom(self):
		for d in self.get("products"):
			if d.bom_no and d.is_finished_product:
				product_code = d.original_product or d.product_code
				validate_bom_no(product_code, d.bom_no)

	def validate_purchase_order(self):
		if self.purpose == "Send to Subcontractor" and self.get("purchase_order"):
			is_old_subcontracting_flow = frappe.db.get_value(
				"Purchase Order", self.purchase_order, "is_old_subcontracting_flow"
			)

			if not is_old_subcontracting_flow:
				frappe.throw(
					_("Please select Subcontracting Order instead of Purchase Order {0}").format(
						self.purchase_order
					)
				)

	def validate_subcontracting_order(self):
		if self.get("subcontracting_order") and self.purpose in [
			"Send to Subcontractor",
			"Material Transfer",
		]:
			sco_status = frappe.db.get_value("Subcontracting Order", self.subcontracting_order, "status")

			if sco_status == "Closed":
				frappe.throw(
					_("Cannot create Stock Entry against a closed Subcontracting Order {0}.").format(
						self.subcontracting_order
					)
				)

	def mark_finished_and_scrap_products(self):
		if self.purpose != "Repack" and any(
			[d.product_code for d in self.products if (d.is_finished_product and d.t_warehouse)]
		):
			return

		finished_product = self.get_finished_product()

		if not finished_product and self.purpose == "Manufacture":
			# In case of independent Manufacture entry, don't auto set
			# user must decide and set
			return

		for d in self.products:
			if d.t_warehouse and not d.s_warehouse:
				if self.purpose == "Repack" or d.product_code == finished_product:
					d.is_finished_product = 1
				else:
					d.is_scrap_product = 1
			else:
				d.is_finished_product = 0
				d.is_scrap_product = 0

	def get_finished_product(self):
		finished_product = None
		if self.work_order:
			finished_product = frappe.db.get_value("Work Order", self.work_order, "production_product")
		elif self.bom_no:
			finished_product = frappe.db.get_value("BOM", self.bom_no, "product")

		return finished_product

	def validate_finished_goods(self):
		"""
		1. Check if FG exists (mfg, repack)
		2. Check if Multiple FG Products are present (mfg)
		3. Check FG Product and Qty against WO if present (mfg)
		"""
		production_product, wo_qty, finished_products = None, 0, []

		wo_details = frappe.db.get_value("Work Order", self.work_order, ["production_product", "qty"])
		if wo_details:
			production_product, wo_qty = wo_details

		for d in self.get("products"):
			if d.is_finished_product:
				if not self.work_order:
					# Independent MFG Entry/ Repack Entry, no WO to match against
					finished_products.append(d.product_code)
					continue

				if d.product_code != production_product:
					frappe.throw(
						_("Finished Product {0} does not match with Work Order {1}").format(
							d.product_code, self.work_order
						)
					)
				elif flt(d.transfer_qty) > flt(self.fg_completed_qty):
					frappe.throw(
						_("Quantity in row {0} ({1}) must be same as manufactured quantity {2}").format(
							d.idx, d.transfer_qty, self.fg_completed_qty
						)
					)

				finished_products.append(d.product_code)

		if not finished_products:
			frappe.throw(
				msg=_("There must be atleast 1 Finished Good in this Stock Entry").format(self.name),
				title=_("Missing Finished Good"),
				exc=FinishedGoodError,
			)

		if self.purpose == "Manufacture":
			if len(set(finished_products)) > 1:
				frappe.throw(
					msg=_("Multiple products cannot be marked as finished product"),
					title=_("Note"),
					exc=FinishedGoodError,
				)

			allowance_percentage = flt(
				frappe.db.get_single_value(
					"Manufacturing Settings", "overproduction_percentage_for_work_order"
				)
			)
			allowed_qty = wo_qty + ((allowance_percentage / 100) * wo_qty)

			# No work order could mean independent Manufacture entry, if so skip validation
			if self.work_order and self.fg_completed_qty > allowed_qty:
				frappe.throw(
					_("For quantity {0} should not be greater than allowed quantity {1}").format(
						flt(self.fg_completed_qty), allowed_qty
					)
				)

	def update_stock_ledger(self):
		sl_entries = []
		finished_product_row = self.get_finished_product_row()

		# make sl entries for source warehouse first
		self.get_sle_for_source_warehouse(sl_entries, finished_product_row)

		# SLE for target warehouse
		self.get_sle_for_target_warehouse(sl_entries, finished_product_row)

		# reverse sl entries if cancel
		if self.docstatus == 2:
			sl_entries.reverse()

		self.make_sl_entries(sl_entries)

	def get_finished_product_row(self):
		finished_product_row = None
		if self.purpose in ("Manufacture", "Repack"):
			for d in self.get("products"):
				if d.is_finished_product:
					finished_product_row = d

		return finished_product_row

	def get_sle_for_source_warehouse(self, sl_entries, finished_product_row):
		for d in self.get("products"):
			if cstr(d.s_warehouse):
				sle = self.get_sl_entries(
					d, {"warehouse": cstr(d.s_warehouse), "actual_qty": -flt(d.transfer_qty), "incoming_rate": 0}
				)
				if cstr(d.t_warehouse):
					sle.dependant_sle_voucher_detail_no = d.name
				elif finished_product_row and (
					finished_product_row.product_code != d.product_code or finished_product_row.t_warehouse != d.s_warehouse
				):
					sle.dependant_sle_voucher_detail_no = finished_product_row.name

				sl_entries.append(sle)

	def get_sle_for_target_warehouse(self, sl_entries, finished_product_row):
		for d in self.get("products"):
			if cstr(d.t_warehouse):
				sle = self.get_sl_entries(
					d,
					{
						"warehouse": cstr(d.t_warehouse),
						"actual_qty": flt(d.transfer_qty),
						"incoming_rate": flt(d.valuation_rate),
					},
				)
				if cstr(d.s_warehouse) or (finished_product_row and d.name == finished_product_row.name):
					sle.recalculate_rate = 1

				sl_entries.append(sle)

	def get_gl_entries(self, warehouse_account):
		gl_entries = super(StockEntry, self).get_gl_entries(warehouse_account)

		if self.purpose in ("Repack", "Manufacture"):
			total_basic_amount = sum(flt(t.basic_amount) for t in self.get("products") if t.is_finished_product)
		else:
			total_basic_amount = sum(flt(t.basic_amount) for t in self.get("products") if t.t_warehouse)

		divide_based_on = total_basic_amount

		if self.get("additional_costs") and not total_basic_amount:
			# if total_basic_amount is 0, distribute additional charges based on qty
			divide_based_on = sum(product.qty for product in list(self.get("products")))

		product_account_wise_additional_cost = {}

		for t in self.get("additional_costs"):
			for d in self.get("products"):
				if self.purpose in ("Repack", "Manufacture") and not d.is_finished_product:
					continue
				elif not d.t_warehouse:
					continue

				product_account_wise_additional_cost.setdefault((d.product_code, d.name), {})
				product_account_wise_additional_cost[(d.product_code, d.name)].setdefault(
					t.expense_account, {"amount": 0.0, "base_amount": 0.0}
				)

				multiply_based_on = d.basic_amount if total_basic_amount else d.qty

				product_account_wise_additional_cost[(d.product_code, d.name)][t.expense_account]["amount"] += (
					flt(t.amount * multiply_based_on) / divide_based_on
				)

				product_account_wise_additional_cost[(d.product_code, d.name)][t.expense_account]["base_amount"] += (
					flt(t.base_amount * multiply_based_on) / divide_based_on
				)

		if product_account_wise_additional_cost:
			for d in self.get("products"):
				for account, amount in product_account_wise_additional_cost.get(
					(d.product_code, d.name), {}
				).products():
					if not amount:
						continue

					gl_entries.append(
						self.get_gl_dict(
							{
								"account": account,
								"against": d.expense_account,
								"cost_center": d.cost_center,
								"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
								"credit_in_account_currency": flt(amount["amount"]),
								"credit": flt(amount["base_amount"]),
							},
							product=d,
						)
					)

					gl_entries.append(
						self.get_gl_dict(
							{
								"account": d.expense_account,
								"against": account,
								"cost_center": d.cost_center,
								"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
								"credit": -1
								* amount["base_amount"],  # put it as negative credit instead of debit purposefully
							},
							product=d,
						)
					)

		return process_gl_map(gl_entries)

	def update_work_order(self):
		def _validate_work_order(pro_doc):
			msg, title = "", ""
			if flt(pro_doc.docstatus) != 1:
				msg = f"Work Order {self.work_order} must be submitted"

			if pro_doc.status == "Stopped":
				msg = f"Transaction not allowed against stopped Work Order {self.work_order}"

			if self.is_return and pro_doc.status not in ["Completed", "Closed"]:
				title = _("Stock Return")
				msg = f"Work Order {self.work_order} must be completed or closed"

			if msg:
				frappe.throw(_(msg), title=title)

		if self.job_card:
			job_doc = frappe.get_doc("Job Card", self.job_card)
			job_doc.set_transferred_qty(update_status=True)
			job_doc.set_transferred_qty_in_job_card_product(self)

		if self.work_order:
			pro_doc = frappe.get_doc("Work Order", self.work_order)
			_validate_work_order(pro_doc)

			if self.fg_completed_qty:
				pro_doc.run_method("update_work_order_qty")
				if self.purpose == "Manufacture":
					pro_doc.run_method("update_planned_qty")
					pro_doc.update_batch_produced_qty(self)

			pro_doc.run_method("update_status")
			if not pro_doc.operations:
				pro_doc.set_actual_dates()

	@frappe.whitelist()
	def get_product_details(self, args=None, for_update=False):
		product = frappe.db.sql(
			"""select i.name, i.stock_uom, i.description, i.image, i.product_name, i.product_group,
				i.has_batch_no, i.sample_quantity, i.has_serial_no, i.allow_alternative_product,
				id.expense_account, id.buying_cost_center
			from `tabProduct` i LEFT JOIN `tabProduct Default` id ON i.name=id.parent and id.company=%s
			where i.name=%s
				and i.disabled=0
				and (i.end_of_life is null or i.end_of_life<'1900-01-01' or i.end_of_life > %s)""",
			(self.company, args.get("product_code"), nowdate()),
			as_dict=1,
		)

		if not product:
			frappe.throw(
				_("Product {0} is not active or end of life has been reached").format(args.get("product_code"))
			)

		product = product[0]
		product_group_defaults = get_product_group_defaults(product.name, self.company)
		brand_defaults = get_brand_defaults(product.name, self.company)

		ret = frappe._dict(
			{
				"uom": product.stock_uom,
				"stock_uom": product.stock_uom,
				"description": product.description,
				"image": product.image,
				"product_name": product.product_name,
				"cost_center": get_default_cost_center(
					args, product, product_group_defaults, brand_defaults, self.company
				),
				"qty": args.get("qty"),
				"transfer_qty": args.get("qty"),
				"conversion_factor": 1,
				"batch_no": "",
				"actual_qty": 0,
				"basic_rate": 0,
				"serial_no": "",
				"has_serial_no": product.has_serial_no,
				"has_batch_no": product.has_batch_no,
				"sample_quantity": product.sample_quantity,
				"expense_account": product.expense_account,
			}
		)

		if self.purpose == "Send to Subcontractor":
			ret["allow_alternative_product"] = product.allow_alternative_product

		# update uom
		if args.get("uom") and for_update:
			ret.update(get_uom_details(args.get("product_code"), args.get("uom"), args.get("qty")))

		if self.purpose == "Material Issue":
			ret["expense_account"] = (
				product.get("expense_account")
				or product_group_defaults.get("expense_account")
				or frappe.get_cached_value("Company", self.company, "default_expense_account")
			)

		for company_field, field in {
			"stock_adjustment_account": "expense_account",
			"cost_center": "cost_center",
		}.products():
			if not ret.get(field):
				ret[field] = frappe.get_cached_value("Company", self.company, company_field)

		args["posting_date"] = self.posting_date
		args["posting_time"] = self.posting_time

		stock_and_rate = get_warehouse_details(args) if args.get("warehouse") else {}
		ret.update(stock_and_rate)

		# automatically select batch for outgoing product
		if (
			args.get("s_warehouse", None)
			and args.get("qty")
			and ret.get("has_batch_no")
			and not args.get("batch_no")
		):
			args.batch_no = get_batch_no(args["product_code"], args["s_warehouse"], args["qty"])

		if (
			self.purpose == "Send to Subcontractor"
			and self.get(self.subcontract_data.order_field)
			and args.get("product_code")
		):
			subcontract_products = frappe.get_all(
				self.subcontract_data.order_supplied_products_field,
				{"parent": self.get(self.subcontract_data.order_field), "rm_product_code": args.get("product_code")},
				"main_product_code",
			)

			if subcontract_products and len(subcontract_products) == 1:
				ret["subcontracted_product"] = subcontract_products[0].main_product_code

		return ret

	@frappe.whitelist()
	def set_products_for_stock_in(self):
		self.products = []

		if self.outgoing_stock_entry and self.purpose == "Material Transfer":
			doc = frappe.get_doc("Stock Entry", self.outgoing_stock_entry)

			if doc.per_transferred == 100:
				frappe.throw(_("Goods are already received against the outward entry {0}").format(doc.name))

			for d in doc.products:
				self.append(
					"products",
					{
						"s_warehouse": d.t_warehouse,
						"product_code": d.product_code,
						"qty": d.qty,
						"uom": d.uom,
						"against_stock_entry": d.parent,
						"ste_detail": d.name,
						"stock_uom": d.stock_uom,
						"conversion_factor": d.conversion_factor,
						"serial_no": d.serial_no,
						"batch_no": d.batch_no,
					},
				)

	@frappe.whitelist()
	def get_products(self):
		self.set("products", [])
		self.validate_work_order()

		if not self.posting_date or not self.posting_time:
			frappe.throw(_("Posting date and posting time is mandatory"))

		self.set_work_order_details()
		self.flags.backflush_based_on = frappe.db.get_single_value(
			"Manufacturing Settings", "backflush_raw_materials_based_on"
		)

		if self.bom_no:

			backflush_based_on = frappe.db.get_single_value(
				"Manufacturing Settings", "backflush_raw_materials_based_on"
			)

			if self.purpose in [
				"Material Issue",
				"Material Transfer",
				"Manufacture",
				"Repack",
				"Send to Subcontractor",
				"Material Transfer for Manufacture",
				"Material Consumption for Manufacture",
			]:

				if self.work_order and self.purpose == "Material Transfer for Manufacture":
					product_dict = self.get_pending_raw_materials(backflush_based_on)
					if self.to_warehouse and self.pro_doc:
						for product in product_dict.values():
							product["to_warehouse"] = self.pro_doc.wip_warehouse
					self.add_to_stock_entry_detail(product_dict)

				elif (
					self.work_order
					and (self.purpose == "Manufacture" or self.purpose == "Material Consumption for Manufacture")
					and not self.pro_doc.skip_transfer
					and self.flags.backflush_based_on == "Material Transferred for Manufacture"
				):
					self.add_transfered_raw_materials_in_products()

				elif (
					self.work_order
					and (self.purpose == "Manufacture" or self.purpose == "Material Consumption for Manufacture")
					and self.flags.backflush_based_on == "BOM"
					and frappe.db.get_single_value("Manufacturing Settings", "material_consumption") == 1
				):
					self.get_unconsumed_raw_materials()

				else:
					if not self.fg_completed_qty:
						frappe.throw(_("Manufacturing Quantity is mandatory"))

					product_dict = self.get_bom_raw_materials(self.fg_completed_qty)

					# Get Subcontract Order Supplied Products Details
					if self.get(self.subcontract_data.order_field) and self.purpose == "Send to Subcontractor":
						# Get Subcontract Order Supplied Products Details
						parent = frappe.qb.DocType(self.subcontract_data.order_doctype)
						child = frappe.qb.DocType(self.subcontract_data.order_supplied_products_field)

						product_wh = (
							frappe.qb.from_(parent)
							.inner_join(child)
							.on(parent.name == child.parent)
							.select(child.rm_product_code, child.reserve_warehouse)
							.where(parent.name == self.get(self.subcontract_data.order_field))
						).run(as_list=True)

						product_wh = frappe._dict(product_wh)

					for product in product_dict.values():
						if self.pro_doc and cint(self.pro_doc.from_wip_warehouse):
							product["from_warehouse"] = self.pro_doc.wip_warehouse
						# Get Reserve Warehouse from Subcontract Order
						if self.get(self.subcontract_data.order_field) and self.purpose == "Send to Subcontractor":
							product["from_warehouse"] = product_wh.get(product.product_code)
						product["to_warehouse"] = self.to_warehouse if self.purpose == "Send to Subcontractor" else ""

					self.add_to_stock_entry_detail(product_dict)

			# fetch the serial_no of the first stock entry for the second stock entry
			if self.work_order and self.purpose == "Manufacture":
				work_order = frappe.get_doc("Work Order", self.work_order)
				add_additional_cost(self, work_order)

			# add finished goods product
			if self.purpose in ("Manufacture", "Repack"):
				self.set_process_loss_qty()
				self.load_products_from_bom()

		self.set_scrap_products()
		self.set_actual_qty()
		self.validate_customer_provided_product()
		self.calculate_rate_and_amount(raise_error_if_no_rate=False)

	def set_scrap_products(self):
		if self.purpose != "Send to Subcontractor" and self.purpose in ["Manufacture", "Repack"]:
			scrap_product_dict = self.get_bom_scrap_material(self.fg_completed_qty)
			for product in scrap_product_dict.values():
				if self.pro_doc and self.pro_doc.scrap_warehouse:
					product["to_warehouse"] = self.pro_doc.scrap_warehouse

			self.add_to_stock_entry_detail(scrap_product_dict, bom_no=self.bom_no)

	def set_process_loss_qty(self):
		if self.purpose not in ("Manufacture", "Repack"):
			return

		precision = self.precision("process_loss_qty")
		if self.work_order:
			data = frappe.get_all(
				"Work Order Operation",
				filters={"parent": self.work_order},
				fields=["max(process_loss_qty) as process_loss_qty"],
			)

			if data and data[0].process_loss_qty is not None:
				process_loss_qty = data[0].process_loss_qty
				if flt(self.process_loss_qty, precision) != flt(process_loss_qty, precision):
					self.process_loss_qty = flt(process_loss_qty, precision)

					frappe.msgprint(
						_("The Process Loss Qty has reset as per job cards Process Loss Qty"), alert=True
					)

		if not self.process_loss_percentage and not self.process_loss_qty:
			self.process_loss_percentage = frappe.get_cached_value(
				"BOM", self.bom_no, "process_loss_percentage"
			)

		if self.process_loss_percentage and not self.process_loss_qty:
			self.process_loss_qty = flt(
				(flt(self.fg_completed_qty) * flt(self.process_loss_percentage)) / 100
			)
		elif self.process_loss_qty and not self.process_loss_percentage:
			self.process_loss_percentage = flt(
				(flt(self.process_loss_qty) / flt(self.fg_completed_qty)) * 100
			)

	def set_work_order_details(self):
		if not getattr(self, "pro_doc", None):
			self.pro_doc = frappe._dict()

		if self.work_order:
			# common validations
			if not self.pro_doc:
				self.pro_doc = frappe.get_doc("Work Order", self.work_order)

			if self.pro_doc:
				self.bom_no = self.pro_doc.bom_no
			else:
				# invalid work order
				self.work_order = None

	def load_products_from_bom(self):
		if self.work_order:
			product_code = self.pro_doc.production_product
			to_warehouse = self.pro_doc.fg_warehouse
		else:
			product_code = frappe.db.get_value("BOM", self.bom_no, "product")
			to_warehouse = self.to_warehouse

		product = get_product_defaults(product_code, self.company)

		if not self.work_order and not to_warehouse:
			# in case of BOM
			to_warehouse = product.get("default_warehouse")

		args = {
			"to_warehouse": to_warehouse,
			"from_warehouse": "",
			"qty": flt(self.fg_completed_qty) - flt(self.process_loss_qty),
			"product_name": product.product_name,
			"description": product.description,
			"stock_uom": product.stock_uom,
			"expense_account": product.get("expense_account"),
			"cost_center": product.get("buying_cost_center"),
			"is_finished_product": 1,
		}

		if (
			self.work_order
			and self.pro_doc.has_batch_no
			and cint(
				frappe.db.get_single_value(
					"Manufacturing Settings", "make_serial_no_batch_from_work_order", cache=True
				)
			)
		):
			self.set_batchwise_finished_goods(args, product)
		else:
			self.add_finished_goods(args, product)

	def set_batchwise_finished_goods(self, args, product):
		filters = {
			"reference_name": self.pro_doc.name,
			"reference_doctype": self.pro_doc.doctype,
			"qty_to_produce": (">", 0),
			"batch_qty": ("=", 0),
		}

		fields = ["qty_to_produce as qty", "produced_qty", "name"]

		data = frappe.get_all("Batch", filters=filters, fields=fields, order_by="creation asc")

		if not data:
			self.add_finished_goods(args, product)
		else:
			self.add_batchwise_finished_good(data, args, product)

	def add_batchwise_finished_good(self, data, args, product):
		qty = flt(self.fg_completed_qty)

		for row in data:
			batch_qty = flt(row.qty) - flt(row.produced_qty)
			if not batch_qty:
				continue

			if qty <= 0:
				break

			fg_qty = batch_qty
			if batch_qty >= qty:
				fg_qty = qty

			qty -= batch_qty
			args["qty"] = fg_qty
			args["batch_no"] = row.name

			self.add_finished_goods(args, product)

	def add_finished_goods(self, args, product):
		self.add_to_stock_entry_detail({product.name: args}, bom_no=self.bom_no)

	def get_bom_raw_materials(self, qty):
		from erpnext.manufacturing.doctype.bom.bom import get_bom_products_as_dict

		# product dict = { product_code: {qty, description, stock_uom} }
		product_dict = get_bom_products_as_dict(
			self.bom_no,
			self.company,
			qty=qty,
			fetch_exploded=self.use_multi_level_bom,
			fetch_qty_in_stock_uom=False,
		)

		used_alternative_products = get_used_alternative_products(
			subcontract_order_field=self.subcontract_data.order_field, work_order=self.work_order
		)
		for product in product_dict.values():
			# if source warehouse presents in BOM set from_warehouse as bom source_warehouse
			if product["allow_alternative_product"]:
				product["allow_alternative_product"] = frappe.db.get_value(
					"Work Order", self.work_order, "allow_alternative_product"
				)

			product.from_warehouse = self.from_warehouse or product.source_warehouse or product.default_warehouse
			if product.product_code in used_alternative_products:
				alternative_product_data = used_alternative_products.get(product.product_code)
				product.product_code = alternative_product_data.product_code
				product.product_name = alternative_product_data.product_name
				product.stock_uom = alternative_product_data.stock_uom
				product.uom = alternative_product_data.uom
				product.conversion_factor = alternative_product_data.conversion_factor
				product.description = alternative_product_data.description

		return product_dict

	def get_bom_scrap_material(self, qty):
		from erpnext.manufacturing.doctype.bom.bom import get_bom_products_as_dict

		# product dict = { product_code: {qty, description, stock_uom} }
		product_dict = (
			get_bom_products_as_dict(self.bom_no, self.company, qty=qty, fetch_exploded=0, fetch_scrap_products=1)
			or {}
		)

		for product in product_dict.values():
			product.from_warehouse = ""
			product.is_scrap_product = 1

		for row in self.get_scrap_products_from_job_card():
			if row.stock_qty <= 0:
				continue

			product_row = product_dict.get(row.product_code)
			if not product_row:
				product_row = frappe._dict({})

			product_row.update(
				{
					"uom": row.stock_uom,
					"from_warehouse": "",
					"qty": row.stock_qty + flt(product_row.stock_qty),
					"converison_factor": 1,
					"is_scrap_product": 1,
					"product_name": row.product_name,
					"description": row.description,
					"allow_zero_valuation_rate": 1,
				}
			)

			product_dict[row.product_code] = product_row

		return product_dict

	def get_scrap_products_from_job_card(self):
		if not self.pro_doc:
			self.set_work_order_details()

		if not self.pro_doc.operations:
			return []

		job_card = frappe.qb.DocType("Job Card")
		job_card_scrap_product = frappe.qb.DocType("Job Card Scrap Product")

		scrap_products = (
			frappe.qb.from_(job_card)
			.select(
				Sum(job_card_scrap_product.stock_qty).as_("stock_qty"),
				job_card_scrap_product.product_code,
				job_card_scrap_product.product_name,
				job_card_scrap_product.description,
				job_card_scrap_product.stock_uom,
			)
			.join(job_card_scrap_product)
			.on(job_card_scrap_product.parent == job_card.name)
			.where(
				(job_card_scrap_product.product_code.isnotnull())
				& (job_card.work_order == self.work_order)
				& (job_card.docstatus == 1)
			)
			.groupby(job_card_scrap_product.product_code)
		).run(as_dict=1)

		pending_qty = flt(self.get_completed_job_card_qty()) - flt(self.pro_doc.produced_qty)

		used_scrap_products = self.get_used_scrap_products()
		for row in scrap_products:
			row.stock_qty -= flt(used_scrap_products.get(row.product_code))
			row.stock_qty = (row.stock_qty) * flt(self.fg_completed_qty) / flt(pending_qty)

			if used_scrap_products.get(row.product_code):
				used_scrap_products[row.product_code] -= row.stock_qty

			if cint(frappe.get_cached_value("UOM", row.stock_uom, "must_be_whole_number")):
				row.stock_qty = frappe.utils.ceil(row.stock_qty)

		return scrap_products

	def get_completed_job_card_qty(self):
		return flt(min([d.completed_qty for d in self.pro_doc.operations]))

	def get_used_scrap_products(self):
		used_scrap_products = defaultdict(float)
		data = frappe.get_all(
			"Stock Entry",
			fields=["`tabStock Entry Detail`.`product_code`", "`tabStock Entry Detail`.`qty`"],
			filters=[
				["Stock Entry", "work_order", "=", self.work_order],
				["Stock Entry Detail", "is_scrap_product", "=", 1],
				["Stock Entry", "docstatus", "=", 1],
				["Stock Entry", "purpose", "in", ["Repack", "Manufacture"]],
			],
		)

		for row in data:
			used_scrap_products[row.product_code] += row.qty

		return used_scrap_products

	def get_unconsumed_raw_materials(self):
		wo = frappe.get_doc("Work Order", self.work_order)
		wo_products = frappe.get_all(
			"Work Order Product",
			filters={"parent": self.work_order},
			fields=["product_code", "source_warehouse", "required_qty", "consumed_qty", "transferred_qty"],
		)

		work_order_qty = wo.material_transferred_for_manufacturing or wo.qty
		for product in wo_products:
			product_account_details = get_product_defaults(product.product_code, self.company)
			# Take into account consumption if there are any.

			wo_product_qty = product.transferred_qty or product.required_qty

			wo_qty_consumed = flt(wo_product_qty) - flt(product.consumed_qty)
			wo_qty_to_produce = flt(work_order_qty) - flt(wo.produced_qty)

			req_qty_each = (wo_qty_consumed) / (wo_qty_to_produce or 1)

			qty = req_qty_each * flt(self.fg_completed_qty)

			if qty > 0:
				self.add_to_stock_entry_detail(
					{
						product.product_code: {
							"from_warehouse": wo.wip_warehouse or product.source_warehouse,
							"to_warehouse": "",
							"qty": qty,
							"product_name": product.product_name,
							"description": product.description,
							"stock_uom": product_account_details.stock_uom,
							"expense_account": product_account_details.get("expense_account"),
							"cost_center": product_account_details.get("buying_cost_center"),
						}
					}
				)

	def add_transfered_raw_materials_in_products(self) -> None:
		available_materials = get_available_materials(self.work_order)

		wo_data = frappe.db.get_value(
			"Work Order",
			self.work_order,
			["qty", "produced_qty", "material_transferred_for_manufacturing as trans_qty"],
			as_dict=1,
		)

		for key, row in available_materials.products():
			remaining_qty_to_produce = flt(wo_data.trans_qty) - flt(wo_data.produced_qty)
			if remaining_qty_to_produce <= 0 and not self.is_return:
				continue

			qty = flt(row.qty)
			if not self.is_return:
				qty = (flt(row.qty) * flt(self.fg_completed_qty)) / remaining_qty_to_produce

			product = row.product_details
			if cint(frappe.get_cached_value("UOM", product.stock_uom, "must_be_whole_number")):
				qty = frappe.utils.ceil(qty)

			if row.batch_details:
				batches = sorted(row.batch_details.products(), key=lambda x: x[0])
				for batch_no, batch_qty in batches:
					if qty <= 0 or batch_qty <= 0:
						continue

					if batch_qty > qty:
						batch_qty = qty

					product.batch_no = batch_no
					self.update_product_in_stock_entry_detail(row, product, batch_qty)

					row.batch_details[batch_no] -= batch_qty
					qty -= batch_qty
			else:
				self.update_product_in_stock_entry_detail(row, product, qty)

	def update_product_in_stock_entry_detail(self, row, product, qty) -> None:
		if not qty:
			return

		ste_product_details = {
			"from_warehouse": product.warehouse,
			"to_warehouse": "",
			"qty": qty,
			"product_name": product.product_name,
			"batch_no": product.batch_no,
			"description": product.description,
			"stock_uom": product.stock_uom,
			"expense_account": product.expense_account,
			"cost_center": product.buying_cost_center,
			"original_product": product.original_product,
		}

		if self.is_return:
			ste_product_details["to_warehouse"] = product.s_warehouse

		if row.serial_nos:
			serial_nos = row.serial_nos
			if product.batch_no:
				serial_nos = self.get_serial_nos_based_on_transferred_batch(product.batch_no, row.serial_nos)

			serial_nos = serial_nos[0 : cint(qty)]
			ste_product_details["serial_no"] = "\n".join(serial_nos)

			# remove consumed serial nos from list
			for sn in serial_nos:
				row.serial_nos.remove(sn)

		self.add_to_stock_entry_detail({product.product_code: ste_product_details})

	@staticmethod
	def get_serial_nos_based_on_transferred_batch(batch_no, serial_nos) -> list:
		serial_nos = frappe.get_all(
			"Serial No", filters={"batch_no": batch_no, "name": ("in", serial_nos)}, order_by="creation"
		)

		return [d.name for d in serial_nos]

	def get_pending_raw_materials(self, backflush_based_on=None):
		"""
		issue (product quantity) that is pending to issue or desire to transfer,
		whichever is less
		"""
		product_dict = self.get_pro_order_required_products(backflush_based_on)

		max_qty = flt(self.pro_doc.qty)

		allow_overproduction = False
		overproduction_percentage = flt(
			frappe.db.get_single_value("Manufacturing Settings", "overproduction_percentage_for_work_order")
		)

		to_transfer_qty = flt(self.pro_doc.material_transferred_for_manufacturing) + flt(
			self.fg_completed_qty
		)
		transfer_limit_qty = max_qty + ((max_qty * overproduction_percentage) / 100)

		if transfer_limit_qty >= to_transfer_qty:
			allow_overproduction = True

		for product, product_details in product_dict.products():
			pending_to_issue = flt(product_details.required_qty) - flt(product_details.transferred_qty)
			desire_to_transfer = flt(self.fg_completed_qty) * flt(product_details.required_qty) / max_qty

			if (
				desire_to_transfer <= pending_to_issue
				or (desire_to_transfer > 0 and backflush_based_on == "Material Transferred for Manufacture")
				or allow_overproduction
			):
				# "No need for transfer but qty still pending to transfer" case can occur
				# when transferring multiple RM in different Stock Entries
				product_dict[product]["qty"] = desire_to_transfer if (desire_to_transfer > 0) else pending_to_issue
			elif pending_to_issue > 0:
				product_dict[product]["qty"] = pending_to_issue
			else:
				product_dict[product]["qty"] = 0

		# delete products with 0 qty
		list_of_products = list(product_dict.keys())
		for product in list_of_products:
			if not product_dict[product]["qty"]:
				del product_dict[product]

		# show some message
		if not len(product_dict):
			frappe.msgprint(_("""All products have already been transferred for this Work Order."""))

		return product_dict

	def get_pro_order_required_products(self, backflush_based_on=None):
		"""
		Gets Work Order Required Products only if Stock Entry purpose is **Material Transferred for Manufacture**.
		"""
		product_dict, job_card_products = frappe._dict(), []
		work_order = frappe.get_doc("Work Order", self.work_order)

		consider_job_card = work_order.transfer_material_against == "Job Card" and self.get("job_card")
		if consider_job_card:
			job_card_products = self.get_job_card_product_codes(self.get("job_card"))

		if not frappe.db.get_value("Warehouse", work_order.wip_warehouse, "is_group"):
			wip_warehouse = work_order.wip_warehouse
		else:
			wip_warehouse = None

		for d in work_order.get("required_products"):
			if consider_job_card and (d.product_code not in job_card_products):
				continue

			transfer_pending = flt(d.required_qty) > flt(d.transferred_qty)
			can_transfer = transfer_pending or (
				backflush_based_on == "Material Transferred for Manufacture"
			)

			if not can_transfer:
				continue

			if d.include_product_in_manufacturing:
				product_row = d.as_dict()
				product_row["idx"] = len(product_dict) + 1

				if consider_job_card:
					job_card_product = frappe.db.get_value(
						"Job Card Product", {"product_code": d.product_code, "parent": self.get("job_card")}
					)
					product_row["job_card_product"] = job_card_product or None

				if d.source_warehouse and not frappe.db.get_value("Warehouse", d.source_warehouse, "is_group"):
					product_row["from_warehouse"] = d.source_warehouse

				product_row["to_warehouse"] = wip_warehouse
				if product_row["allow_alternative_product"]:
					product_row["allow_alternative_product"] = work_order.allow_alternative_product

				product_dict.setdefault(d.product_code, product_row)

		return product_dict

	def get_job_card_product_codes(self, job_card=None):
		if not job_card:
			return []

		job_card_products = frappe.get_all(
			"Job Card Product", filters={"parent": job_card}, fields=["product_code"], distinct=True
		)
		return [d.product_code for d in job_card_products]

	def add_to_stock_entry_detail(self, product_dict, bom_no=None):
		for d in product_dict:
			product_row = product_dict[d]
			stock_uom = product_row.get("stock_uom") or frappe.db.get_value("Product", d, "stock_uom")

			se_child = self.append("products")
			se_child.s_warehouse = product_row.get("from_warehouse")
			se_child.t_warehouse = product_row.get("to_warehouse")
			se_child.product_code = product_row.get("product_code") or cstr(d)
			se_child.uom = product_row["uom"] if product_row.get("uom") else stock_uom
			se_child.stock_uom = stock_uom
			se_child.qty = flt(product_row["qty"], se_child.precision("qty"))
			se_child.allow_alternative_product = product_row.get("allow_alternative_product", 0)
			se_child.subcontracted_product = product_row.get("main_product_code")
			se_child.cost_center = product_row.get("cost_center") or get_default_cost_center(
				product_row, company=self.company
			)
			se_child.is_finished_product = product_row.get("is_finished_product", 0)
			se_child.is_scrap_product = product_row.get("is_scrap_product", 0)
			se_child.po_detail = product_row.get("po_detail")
			se_child.sco_rm_detail = product_row.get("sco_rm_detail")

			for field in [
				self.subcontract_data.rm_detail_field,
				"original_product",
				"expense_account",
				"description",
				"product_name",
				"serial_no",
				"batch_no",
				"allow_zero_valuation_rate",
			]:
				if product_row.get(field):
					se_child.set(field, product_row.get(field))

			if se_child.s_warehouse == None:
				se_child.s_warehouse = self.from_warehouse
			if se_child.t_warehouse == None:
				se_child.t_warehouse = self.to_warehouse

			# in stock uom
			se_child.conversion_factor = flt(product_row.get("conversion_factor")) or 1
			se_child.transfer_qty = flt(
				product_row["qty"] * se_child.conversion_factor, se_child.precision("qty")
			)

			se_child.bom_no = bom_no  # to be assigned for finished product
			se_child.job_card_product = product_row.get("job_card_product") if self.get("job_card") else None

	def validate_with_material_request(self):
		for product in self.get("products"):
			material_request = product.material_request or None
			material_request_product = product.material_request_product or None
			if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
				parent_se = frappe.get_value(
					"Stock Entry Detail",
					product.ste_detail,
					["material_request", "material_request_product"],
					as_dict=True,
				)
				if parent_se:
					material_request = parent_se.material_request
					material_request_product = parent_se.material_request_product

			if material_request:
				mreq_product = frappe.db.get_value(
					"Material Request Product",
					{"name": material_request_product, "parent": material_request},
					["product_code", "warehouse", "idx"],
					as_dict=True,
				)
				if mreq_product.product_code != product.product_code:
					frappe.throw(
						_("Product for row {0} does not match Material Request").format(product.idx),
						frappe.MappingMismatchError,
					)
				elif self.purpose == "Material Transfer" and self.add_to_transit:
					continue

	def validate_batch(self):
		if self.purpose in [
			"Material Transfer for Manufacture",
			"Manufacture",
			"Repack",
			"Send to Subcontractor",
		]:
			for product in self.get("products"):
				if product.batch_no:
					disabled = frappe.db.get_value("Batch", product.batch_no, "disabled")
					if disabled == 0:
						expiry_date = frappe.db.get_value("Batch", product.batch_no, "expiry_date")
						if expiry_date:
							if getdate(self.posting_date) > getdate(expiry_date):
								frappe.throw(_("Batch {0} of Product {1} has expired.").format(product.batch_no, product.product_code))
					else:
						frappe.throw(_("Batch {0} of Product {1} is disabled.").format(product.batch_no, product.product_code))

	def update_subcontract_order_supplied_products(self):
		if self.get(self.subcontract_data.order_field) and (
			self.purpose in ["Send to Subcontractor", "Material Transfer"] or self.is_return
		):

			# Get Subcontract Order Supplied Products Details
			order_supplied_products = frappe.db.get_all(
				self.subcontract_data.order_supplied_products_field,
				filters={"parent": self.get(self.subcontract_data.order_field)},
				fields=["name", "rm_product_code", "reserve_warehouse"],
			)

			# Get Products Supplied in Stock Entries against Subcontract Order
			supplied_products = get_supplied_products(
				self.get(self.subcontract_data.order_field),
				self.subcontract_data.rm_detail_field,
				self.subcontract_data.order_field,
			)

			for row in order_supplied_products:
				key, product = row.name, {}
				if not supplied_products.get(key):
					# no stock transferred against Subcontract Order Supplied Products row
					product = {"supplied_qty": 0, "returned_qty": 0, "total_supplied_qty": 0}
				else:
					product = supplied_products.get(key)

				frappe.db.set_value(self.subcontract_data.order_supplied_products_field, row.name, product)

			# RM Product-Reserve Warehouse Dict
			product_wh = {x.get("rm_product_code"): x.get("reserve_warehouse") for x in order_supplied_products}

			for d in self.get("products"):
				# Update reserved sub contracted quantity in bin based on Supplied Product Details and
				product_code = d.get("original_product") or d.get("product_code")
				reserve_warehouse = product_wh.get(product_code)
				if not (reserve_warehouse and product_code):
					continue
				stock_bin = get_bin(product_code, reserve_warehouse)
				stock_bin.update_reserved_qty_for_sub_contracting()

	def update_so_in_serial_number(self):
		so_name, product_code = frappe.db.get_value(
			"Work Order", self.work_order, ["sales_order", "production_product"]
		)
		if so_name and product_code:
			qty_to_reserve = get_reserved_qty_for_so(so_name, product_code)
			if qty_to_reserve:
				reserved_qty = frappe.db.sql(
					"""select count(name) from `tabSerial No` where product_code=%s and
					sales_order=%s""",
					(product_code, so_name),
				)
				if reserved_qty and reserved_qty[0][0]:
					qty_to_reserve -= reserved_qty[0][0]
				if qty_to_reserve > 0:
					for product in self.products:
						has_serial_no = frappe.get_cached_value("Product", product.product_code, "has_serial_no")
						if product.product_code == product_code and has_serial_no:
							serial_nos = (product.serial_no).split("\n")
							for serial_no in serial_nos:
								if qty_to_reserve > 0:
									frappe.db.set_value("Serial No", serial_no, "sales_order", so_name)
									qty_to_reserve -= 1

	def validate_reserved_serial_no_consumption(self):
		for product in self.products:
			if product.s_warehouse and not product.t_warehouse and product.serial_no:
				for sr in get_serial_nos(product.serial_no):
					sales_order = frappe.db.get_value("Serial No", sr, "sales_order")
					if sales_order:
						msg = _(
							"(Serial No: {0}) cannot be consumed as it's reserverd to fullfill Sales Order {1}."
						).format(sr, sales_order)

						frappe.throw(_("Product {0} {1}").format(product.product_code, msg))

	def update_transferred_qty(self):
		if self.purpose == "Material Transfer" and self.outgoing_stock_entry:
			stock_entries = {}
			stock_entries_child_list = []
			for d in self.products:
				if not (d.against_stock_entry and d.ste_detail):
					continue

				stock_entries_child_list.append(d.ste_detail)
				transferred_qty = frappe.get_all(
					"Stock Entry Detail",
					fields=["sum(qty) as qty"],
					filters={
						"against_stock_entry": d.against_stock_entry,
						"ste_detail": d.ste_detail,
						"docstatus": 1,
					},
				)

				stock_entries[(d.against_stock_entry, d.ste_detail)] = (
					transferred_qty[0].qty if transferred_qty and transferred_qty[0] else 0.0
				) or 0.0

			if not stock_entries:
				return None

			cond = ""
			for data, transferred_qty in stock_entries.products():
				cond += """ WHEN (parent = %s and name = %s) THEN %s
					""" % (
					frappe.db.escape(data[0]),
					frappe.db.escape(data[1]),
					transferred_qty,
				)

			if stock_entries_child_list:
				frappe.db.sql(
					""" UPDATE `tabStock Entry Detail`
					SET
						transferred_qty = CASE {cond} END
					WHERE
						name in ({ste_details}) """.format(
						cond=cond, ste_details=",".join(["%s"] * len(stock_entries_child_list))
					),
					tuple(stock_entries_child_list),
				)

			args = {
				"source_dt": "Stock Entry Detail",
				"target_field": "transferred_qty",
				"target_ref_field": "qty",
				"target_dt": "Stock Entry Detail",
				"join_field": "ste_detail",
				"target_parent_dt": "Stock Entry",
				"target_parent_field": "per_transferred",
				"source_field": "qty",
				"percent_join_field": "against_stock_entry",
			}

			self._update_percent_field_in_targets(args, update_modified=True)

	def update_quality_inspection(self):
		if self.inspection_required:
			reference_type = reference_name = ""
			if self.docstatus == 1:
				reference_name = self.name
				reference_type = "Stock Entry"

			for d in self.products:
				if d.quality_inspection:
					frappe.db.set_value(
						"Quality Inspection",
						d.quality_inspection,
						{"reference_type": reference_type, "reference_name": reference_name},
					)

	def set_material_request_transfer_status(self, status):
		material_requests = []
		if self.outgoing_stock_entry:
			parent_se = frappe.get_value("Stock Entry", self.outgoing_stock_entry, "add_to_transit")

		for product in self.products:
			material_request = product.material_request or None
			if self.purpose == "Material Transfer" and material_request not in material_requests:
				if self.outgoing_stock_entry and parent_se:
					material_request = frappe.get_value("Stock Entry Detail", product.ste_detail, "material_request")

			if material_request and material_request not in material_requests:
				material_requests.append(material_request)
				frappe.db.set_value("Material Request", material_request, "transfer_status", status)

	def set_serial_no_batch_for_finished_good(self):
		serial_nos = []
		if self.pro_doc.serial_no:
			serial_nos = self.get_serial_nos_for_fg() or []

		for row in self.products:
			if row.is_finished_product and row.product_code == self.pro_doc.production_product:
				if serial_nos:
					row.serial_no = "\n".join(serial_nos[0 : cint(row.qty)])

	def get_serial_nos_for_fg(self):
		fields = [
			"`tabStock Entry`.`name`",
			"`tabStock Entry Detail`.`qty`",
			"`tabStock Entry Detail`.`serial_no`",
			"`tabStock Entry Detail`.`batch_no`",
		]

		filters = [
			["Stock Entry", "work_order", "=", self.work_order],
			["Stock Entry", "purpose", "=", "Manufacture"],
			["Stock Entry", "docstatus", "<", 2],
			["Stock Entry Detail", "product_code", "=", self.pro_doc.production_product],
		]

		stock_entries = frappe.get_all("Stock Entry", fields=fields, filters=filters)
		return self.get_available_serial_nos(stock_entries)

	def get_available_serial_nos(self, stock_entries):
		used_serial_nos = []
		for row in stock_entries:
			if row.serial_no:
				used_serial_nos.extend(get_serial_nos(row.serial_no))

		return sorted(list(set(get_serial_nos(self.pro_doc.serial_no)) - set(used_serial_nos)))

	def update_subcontracting_order_status(self):
		if self.subcontracting_order and self.purpose in ["Send to Subcontractor", "Material Transfer"]:
			from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
				update_subcontracting_order_status,
			)

			update_subcontracting_order_status(self.subcontracting_order)

	def update_pick_list_status(self):
		from erpnext.stock.doctype.pick_list.pick_list import update_pick_list_status

		update_pick_list_status(self.pick_list)

	def set_missing_values(self):
		"Updates rate and availability of all the products of mapped doc."
		self.set_transfer_qty()
		self.set_actual_qty()
		self.calculate_rate_and_amount()


@frappe.whitelist()
def move_sample_to_retention_warehouse(company, products):
	if isinstance(products, str):
		products = json.loads(products)
	retention_warehouse = frappe.db.get_single_value("Stock Settings", "sample_retention_warehouse")
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = company
	stock_entry.purpose = "Material Transfer"
	stock_entry.set_stock_entry_type()
	for product in products:
		if product.get("sample_quantity") and product.get("batch_no"):
			sample_quantity = validate_sample_quantity(
				product.get("product_code"),
				product.get("sample_quantity"),
				product.get("transfer_qty") or product.get("qty"),
				product.get("batch_no"),
			)
			if sample_quantity:
				sample_serial_nos = ""
				if product.get("serial_no"):
					serial_nos = (product.get("serial_no")).split()
					if serial_nos and len(serial_nos) > product.get("sample_quantity"):
						serial_no_list = serial_nos[: -(len(serial_nos) - product.get("sample_quantity"))]
						sample_serial_nos = "\n".join(serial_no_list)

				stock_entry.append(
					"products",
					{
						"product_code": product.get("product_code"),
						"s_warehouse": product.get("t_warehouse"),
						"t_warehouse": retention_warehouse,
						"qty": product.get("sample_quantity"),
						"basic_rate": product.get("valuation_rate"),
						"uom": product.get("uom"),
						"stock_uom": product.get("stock_uom"),
						"conversion_factor": product.get("conversion_factor") or 1.0,
						"serial_no": sample_serial_nos,
						"batch_no": product.get("batch_no"),
					},
				)
	if stock_entry.get("products"):
		return stock_entry.as_dict()


@frappe.whitelist()
def make_stock_in_entry(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.stock_entry_type = "Material Transfer"
		target.set_missing_values()

	def update_product(source_doc, target_doc, source_parent):
		target_doc.t_warehouse = ""

		if source_doc.material_request_product and source_doc.material_request:
			add_to_transit = frappe.db.get_value("Stock Entry", source_name, "add_to_transit")
			if add_to_transit:
				warehouse = frappe.get_value(
					"Material Request Product", source_doc.material_request_product, "warehouse"
				)
				target_doc.t_warehouse = warehouse

		target_doc.s_warehouse = source_doc.t_warehouse
		target_doc.qty = source_doc.qty - source_doc.transferred_qty

	doclist = get_mapped_doc(
		"Stock Entry",
		source_name,
		{
			"Stock Entry": {
				"doctype": "Stock Entry",
				"field_map": {"name": "outgoing_stock_entry"},
				"validation": {"docstatus": ["=", 1]},
			},
			"Stock Entry Detail": {
				"doctype": "Stock Entry Detail",
				"field_map": {
					"name": "ste_detail",
					"parent": "against_stock_entry",
					"serial_no": "serial_no",
					"batch_no": "batch_no",
				},
				"postprocess": update_product,
				"condition": lambda doc: flt(doc.qty) - flt(doc.transferred_qty) > 0.01,
			},
		},
		target_doc,
		set_missing_values,
	)

	return doclist


@frappe.whitelist()
def get_work_order_details(work_order, company):
	work_order = frappe.get_doc("Work Order", work_order)
	pending_qty_to_produce = flt(work_order.qty) - flt(work_order.produced_qty)

	return {
		"from_bom": 1,
		"bom_no": work_order.bom_no,
		"use_multi_level_bom": work_order.use_multi_level_bom,
		"wip_warehouse": work_order.wip_warehouse,
		"fg_warehouse": work_order.fg_warehouse,
		"fg_completed_qty": pending_qty_to_produce,
	}


def get_operating_cost_per_unit(work_order=None, bom_no=None):
	operating_cost_per_unit = 0
	if work_order:
		if not bom_no:
			bom_no = work_order.bom_no

		for d in work_order.get("operations"):
			if flt(d.completed_qty):
				operating_cost_per_unit += flt(d.actual_operating_cost) / flt(d.completed_qty)
			elif work_order.qty:
				operating_cost_per_unit += flt(d.planned_operating_cost) / flt(work_order.qty)

	# Get operating cost from BOM if not found in work_order.
	if not operating_cost_per_unit and bom_no:
		bom = frappe.db.get_value("BOM", bom_no, ["operating_cost", "quantity"], as_dict=1)
		if bom.quantity:
			operating_cost_per_unit = flt(bom.operating_cost) / flt(bom.quantity)

	if (
		work_order
		and work_order.produced_qty
		and cint(
			frappe.db.get_single_value(
				"Manufacturing Settings", "add_corrective_operation_cost_in_finished_good_valuation"
			)
		)
	):
		operating_cost_per_unit += flt(work_order.corrective_operation_cost) / flt(
			work_order.produced_qty
		)

	return operating_cost_per_unit


def get_used_alternative_products(
	subcontract_order=None, subcontract_order_field="subcontracting_order", work_order=None
):
	cond = ""

	if subcontract_order:
		cond = f"and ste.purpose = 'Send to Subcontractor' and ste.{subcontract_order_field} = '{subcontract_order}'"
	elif work_order:
		cond = "and ste.purpose = 'Material Transfer for Manufacture' and ste.work_order = '{0}'".format(
			work_order
		)

	if not cond:
		return {}

	used_alternative_products = {}
	data = frappe.db.sql(
		""" select sted.original_product, sted.uom, sted.conversion_factor,
			sted.product_code, sted.product_name, sted.conversion_factor,sted.stock_uom, sted.description
		from
			`tabStock Entry` ste, `tabStock Entry Detail` sted
		where
			sted.parent = ste.name and ste.docstatus = 1 and sted.original_product !=  sted.product_code
			{0} """.format(
			cond
		),
		as_dict=1,
	)

	for d in data:
		used_alternative_products[d.original_product] = d

	return used_alternative_products


def get_valuation_rate_for_finished_good_entry(work_order):
	work_order_qty = flt(
		frappe.get_cached_value("Work Order", work_order, "material_transferred_for_manufacturing")
	)

	field = "(SUM(total_outgoing_value) / %s) as valuation_rate" % (work_order_qty)

	stock_data = frappe.get_all(
		"Stock Entry",
		fields=field,
		filters={
			"docstatus": 1,
			"purpose": "Material Transfer for Manufacture",
			"work_order": work_order,
		},
	)

	if stock_data:
		return stock_data[0].valuation_rate


@frappe.whitelist()
def get_uom_details(product_code, uom, qty):
	"""Returns dict `{"conversion_factor": [value], "transfer_qty": qty * [value]}`
	:param args: dict with `product_code`, `uom` and `qty`"""
	conversion_factor = get_conversion_factor(product_code, uom).get("conversion_factor")

	if not conversion_factor:
		frappe.msgprint(
			_("UOM conversion factor required for UOM: {0} in Product: {1}").format(uom, product_code)
		)
		ret = {"uom": ""}
	else:
		ret = {
			"conversion_factor": flt(conversion_factor),
			"transfer_qty": flt(qty) * flt(conversion_factor),
		}
	return ret


@frappe.whitelist()
def get_expired_batch_products():
	return frappe.db.sql(
		"""select b.product, sum(sle.actual_qty) as qty, sle.batch_no, sle.warehouse, sle.stock_uom\
	from `tabBatch` b, `tabStock Ledger Entry` sle
	where b.expiry_date <= %s
	and b.expiry_date is not NULL
	and b.batch_id = sle.batch_no and sle.is_cancelled = 0
	group by sle.warehouse, sle.product_code, sle.batch_no""",
		(nowdate()),
		as_dict=1,
	)


@frappe.whitelist()
def get_warehouse_details(args):
	if isinstance(args, str):
		args = json.loads(args)

	args = frappe._dict(args)

	ret = {}
	if args.warehouse and args.product_code:
		args.update(
			{
				"posting_date": args.posting_date,
				"posting_time": args.posting_time,
			}
		)
		ret = {
			"actual_qty": get_previous_sle(args).get("qty_after_transaction") or 0,
			"basic_rate": get_incoming_rate(args),
		}
	return ret


@frappe.whitelist()
def validate_sample_quantity(product_code, sample_quantity, qty, batch_no=None):
	if cint(qty) < cint(sample_quantity):
		frappe.throw(
			_("Sample quantity {0} cannot be more than received quantity {1}").format(sample_quantity, qty)
		)
	retention_warehouse = frappe.db.get_single_value("Stock Settings", "sample_retention_warehouse")
	retainted_qty = 0
	if batch_no:
		retainted_qty = get_batch_qty(batch_no, retention_warehouse, product_code)
	max_retain_qty = frappe.get_value("Product", product_code, "sample_quantity")
	if retainted_qty >= max_retain_qty:
		frappe.msgprint(
			_(
				"Maximum Samples - {0} have already been retained for Batch {1} and Product {2} in Batch {3}."
			).format(retainted_qty, batch_no, product_code, batch_no),
			alert=True,
		)
		sample_quantity = 0
	qty_diff = max_retain_qty - retainted_qty
	if cint(sample_quantity) > cint(qty_diff):
		frappe.msgprint(
			_("Maximum Samples - {0} can be retained for Batch {1} and Product {2}.").format(
				max_retain_qty, batch_no, product_code
			),
			alert=True,
		)
		sample_quantity = qty_diff
	return sample_quantity


def get_supplied_products(
	subcontract_order, rm_detail_field="sco_rm_detail", subcontract_order_field="subcontracting_order"
):
	fields = [
		"`tabStock Entry Detail`.`transfer_qty`",
		"`tabStock Entry`.`is_return`",
		f"`tabStock Entry Detail`.`{rm_detail_field}`",
		"`tabStock Entry Detail`.`product_code`",
	]

	filters = [
		["Stock Entry", "docstatus", "=", 1],
		["Stock Entry", subcontract_order_field, "=", subcontract_order],
	]

	supplied_product_details = {}
	for row in frappe.get_all("Stock Entry", fields=fields, filters=filters):
		if not row.get(rm_detail_field):
			continue

		key = row.get(rm_detail_field)
		if key not in supplied_product_details:
			supplied_product_details.setdefault(
				key, frappe._dict({"supplied_qty": 0, "returned_qty": 0, "total_supplied_qty": 0})
			)

		supplied_product = supplied_product_details[key]

		if row.is_return:
			supplied_product.returned_qty += row.transfer_qty
		else:
			supplied_product.supplied_qty += row.transfer_qty

		supplied_product.total_supplied_qty = flt(supplied_product.supplied_qty) - flt(
			supplied_product.returned_qty
		)

	return supplied_product_details


@frappe.whitelist()
def get_products_from_subcontract_order(source_name, target_doc=None):
	from erpnext.controllers.subcontracting_controller import make_rm_stock_entry

	if isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))

	order_doctype = "Purchase Order" if target_doc.purchase_order else "Subcontracting Order"
	target_doc = make_rm_stock_entry(
		subcontract_order=source_name, order_doctype=order_doctype, target_doc=target_doc
	)

	return target_doc


def get_available_materials(work_order) -> dict:
	data = get_stock_entry_data(work_order)

	available_materials = {}
	for row in data:
		key = (row.product_code, row.warehouse)
		if row.purpose != "Material Transfer for Manufacture":
			key = (row.product_code, row.s_warehouse)

		if key not in available_materials:
			available_materials.setdefault(
				key,
				frappe._dict(
					{"product_details": row, "batch_details": defaultdict(float), "qty": 0, "serial_nos": []}
				),
			)

		product_data = available_materials[key]

		if row.purpose == "Material Transfer for Manufacture":
			product_data.qty += row.qty
			if row.batch_no:
				product_data.batch_details[row.batch_no] += row.qty

			if row.serial_no:
				product_data.serial_nos.extend(get_serial_nos(row.serial_no))
				product_data.serial_nos.sort()
		else:
			# Consume raw material qty in case of 'Manufacture' or 'Material Consumption for Manufacture'

			product_data.qty -= row.qty
			if row.batch_no:
				product_data.batch_details[row.batch_no] -= row.qty

			if row.serial_no:
				for serial_no in get_serial_nos(row.serial_no):
					product_data.serial_nos.remove(serial_no)

	return available_materials


def get_stock_entry_data(work_order):
	stock_entry = frappe.qb.DocType("Stock Entry")
	stock_entry_detail = frappe.qb.DocType("Stock Entry Detail")

	return (
		frappe.qb.from_(stock_entry)
		.from_(stock_entry_detail)
		.select(
			stock_entry_detail.product_name,
			stock_entry_detail.original_product,
			stock_entry_detail.product_code,
			stock_entry_detail.qty,
			(stock_entry_detail.t_warehouse).as_("warehouse"),
			(stock_entry_detail.s_warehouse).as_("s_warehouse"),
			stock_entry_detail.description,
			stock_entry_detail.stock_uom,
			stock_entry_detail.expense_account,
			stock_entry_detail.cost_center,
			stock_entry_detail.batch_no,
			stock_entry_detail.serial_no,
			stock_entry.purpose,
		)
		.where(
			(stock_entry.name == stock_entry_detail.parent)
			& (stock_entry.work_order == work_order)
			& (stock_entry.docstatus == 1)
			& (stock_entry_detail.s_warehouse.isnotnull())
			& (
				stock_entry.purpose.isin(
					["Manufacture", "Material Consumption for Manufacture", "Material Transfer for Manufacture"]
				)
			)
		)
		.orderby(stock_entry.creation, stock_entry_detail.product_code, stock_entry_detail.idx)
	).run(as_dict=1)
