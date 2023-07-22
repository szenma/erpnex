# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json
from collections import defaultdict
from typing import List, Tuple

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_link_to_form, getdate

import erpnext
from erpnext.accounts.general_ledger import (
	make_gl_entries,
	make_reverse_gl_entries,
	process_gl_map,
)
from erpnext.accounts.utils import get_fiscal_year
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.stock import get_warehouse_account_map
from erpnext.stock.doctype.inventory_dimension.inventory_dimension import (
	get_evaluated_inventory_dimension,
)
from erpnext.stock.stock_ledger import get_products_to_be_repost


class QualityInspectionRequiredError(frappe.ValidationError):
	pass


class QualityInspectionRejectedError(frappe.ValidationError):
	pass


class QualityInspectionNotSubmittedError(frappe.ValidationError):
	pass


class BatchExpiredError(frappe.ValidationError):
	pass


class StockController(AccountsController):
	def validate(self):
		super(StockController, self).validate()
		if not self.get("is_return"):
			self.validate_inspection()
		self.validate_serialized_batch()
		self.clean_serial_nos()
		self.validate_customer_provided_product()
		self.set_rate_of_stock_uom()
		self.validate_internal_transfer()
		self.validate_putaway_capacity()

	def make_gl_entries(self, gl_entries=None, from_repost=False):
		if self.docstatus == 2:
			make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)

		provisional_accounting_for_non_stock_products = cint(
			frappe.get_cached_value(
				"Company", self.company, "enable_provisional_accounting_for_non_stock_products"
			)
		)

		if (
			cint(erpnext.is_perpetual_inventory_enabled(self.company))
			or provisional_accounting_for_non_stock_products
		):
			warehouse_account = get_warehouse_account_map(self.company)

			if self.docstatus == 1:
				if not gl_entries:
					gl_entries = self.get_gl_entries(warehouse_account)
				make_gl_entries(gl_entries, from_repost=from_repost)

		elif self.doctype in ["Purchase Receipt", "Purchase Invoice"] and self.docstatus == 1:
			gl_entries = []
			gl_entries = self.get_asset_gl_entry(gl_entries)
			make_gl_entries(gl_entries, from_repost=from_repost)

	def validate_serialized_batch(self):
		from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

		is_material_issue = False
		if self.doctype == "Stock Entry" and self.purpose == "Material Issue":
			is_material_issue = True

		for d in self.get("products"):
			if hasattr(d, "serial_no") and hasattr(d, "batch_no") and d.serial_no and d.batch_no:
				serial_nos = frappe.get_all(
					"Serial No",
					fields=["batch_no", "name", "warehouse"],
					filters={"name": ("in", get_serial_nos(d.serial_no))},
				)

				for row in serial_nos:
					if row.warehouse and row.batch_no != d.batch_no:
						frappe.throw(
							_("Row #{0}: Serial No {1} does not belong to Batch {2}").format(
								d.idx, row.name, d.batch_no
							)
						)

			if is_material_issue:
				continue

			if flt(d.qty) > 0.0 and d.get("batch_no") and self.get("posting_date") and self.docstatus < 2:
				expiry_date = frappe.get_cached_value("Batch", d.get("batch_no"), "expiry_date")

				if expiry_date and getdate(expiry_date) < getdate(self.posting_date):
					frappe.throw(
						_("Row #{0}: The batch {1} has already expired.").format(
							d.idx, get_link_to_form("Batch", d.get("batch_no"))
						),
						BatchExpiredError,
					)

	def clean_serial_nos(self):
		from erpnext.stock.doctype.serial_no.serial_no import clean_serial_no_string

		for row in self.get("products"):
			if hasattr(row, "serial_no") and row.serial_no:
				# remove extra whitespace and store one serial no on each line
				row.serial_no = clean_serial_no_string(row.serial_no)

		for row in self.get("packed_products") or []:
			if hasattr(row, "serial_no") and row.serial_no:
				# remove extra whitespace and store one serial no on each line
				row.serial_no = clean_serial_no_string(row.serial_no)

	def get_gl_entries(
		self, warehouse_account=None, default_expense_account=None, default_cost_center=None
	):

		if not warehouse_account:
			warehouse_account = get_warehouse_account_map(self.company)

		sle_map = self.get_stock_ledger_details()
		voucher_details = self.get_voucher_details(default_expense_account, default_cost_center, sle_map)

		gl_list = []
		warehouse_with_no_account = []
		precision = self.get_debit_field_precision()
		for product_row in voucher_details:
			sle_list = sle_map.get(product_row.name)
			sle_rounding_diff = 0.0
			if sle_list:
				for sle in sle_list:
					if warehouse_account.get(sle.warehouse):
						# from warehouse account

						sle_rounding_diff += flt(sle.stock_value_difference)

						self.check_expense_account(product_row)

						# expense account/ target_warehouse / source_warehouse
						if product_row.get("target_warehouse"):
							warehouse = product_row.get("target_warehouse")
							expense_account = warehouse_account[warehouse]["account"]
						else:
							expense_account = product_row.expense_account

						gl_list.append(
							self.get_gl_dict(
								{
									"account": warehouse_account[sle.warehouse]["account"],
									"against": expense_account,
									"cost_center": product_row.cost_center,
									"project": product_row.project or self.get("project"),
									"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
									"debit": flt(sle.stock_value_difference, precision),
									"is_opening": product_row.get("is_opening") or self.get("is_opening") or "No",
								},
								warehouse_account[sle.warehouse]["account_currency"],
								product=product_row,
							)
						)

						gl_list.append(
							self.get_gl_dict(
								{
									"account": expense_account,
									"against": warehouse_account[sle.warehouse]["account"],
									"cost_center": product_row.cost_center,
									"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
									"debit": -1 * flt(sle.stock_value_difference, precision),
									"project": product_row.get("project") or self.get("project"),
									"is_opening": product_row.get("is_opening") or self.get("is_opening") or "No",
								},
								product=product_row,
							)
						)
					elif sle.warehouse not in warehouse_with_no_account:
						warehouse_with_no_account.append(sle.warehouse)

			if abs(sle_rounding_diff) > (1.0 / (10**precision)) and self.is_internal_transfer():
				warehouse_asset_account = ""
				if self.get("is_internal_customer"):
					warehouse_asset_account = warehouse_account[product_row.get("target_warehouse")]["account"]
				elif self.get("is_internal_supplier"):
					warehouse_asset_account = warehouse_account[product_row.get("warehouse")]["account"]

				expense_account = frappe.get_cached_value("Company", self.company, "default_expense_account")
				if not expense_account:
					frappe.throw(
						_(
							"Please set default cost of goods sold account in company {0} for booking rounding gain and loss during stock transfer"
						).format(frappe.bold(self.company))
					)

				gl_list.append(
					self.get_gl_dict(
						{
							"account": expense_account,
							"against": warehouse_asset_account,
							"cost_center": product_row.cost_center,
							"project": product_row.project or self.get("project"),
							"remarks": _("Rounding gain/loss Entry for Stock Transfer"),
							"debit": sle_rounding_diff,
							"is_opening": product_row.get("is_opening") or self.get("is_opening") or "No",
						},
						warehouse_account[sle.warehouse]["account_currency"],
						product=product_row,
					)
				)

				gl_list.append(
					self.get_gl_dict(
						{
							"account": warehouse_asset_account,
							"against": expense_account,
							"cost_center": product_row.cost_center,
							"remarks": _("Rounding gain/loss Entry for Stock Transfer"),
							"credit": sle_rounding_diff,
							"project": product_row.get("project") or self.get("project"),
							"is_opening": product_row.get("is_opening") or self.get("is_opening") or "No",
						},
						product=product_row,
					)
				)

		if warehouse_with_no_account:
			for wh in warehouse_with_no_account:
				if frappe.get_cached_value("Warehouse", wh, "company"):
					frappe.throw(
						_(
							"Warehouse {0} is not linked to any account, please mention the account in the warehouse record or set default inventory account in company {1}."
						).format(wh, self.company)
					)

		return process_gl_map(gl_list, precision=precision)

	def get_debit_field_precision(self):
		if not frappe.flags.debit_field_precision:
			frappe.flags.debit_field_precision = frappe.get_precision(
				"GL Entry", "debit_in_account_currency"
			)

		return frappe.flags.debit_field_precision

	def get_voucher_details(self, default_expense_account, default_cost_center, sle_map):
		if self.doctype == "Stock Reconciliation":
			reconciliation_purpose = frappe.db.get_value(self.doctype, self.name, "purpose")
			is_opening = "Yes" if reconciliation_purpose == "Opening Stock" else "No"
			details = []
			for voucher_detail_no in sle_map:
				details.append(
					frappe._dict(
						{
							"name": voucher_detail_no,
							"expense_account": default_expense_account,
							"cost_center": default_cost_center,
							"is_opening": is_opening,
						}
					)
				)
			return details
		else:
			details = self.get("products")

			if default_expense_account or default_cost_center:
				for d in details:
					if default_expense_account and not d.get("expense_account"):
						d.expense_account = default_expense_account
					if default_cost_center and not d.get("cost_center"):
						d.cost_center = default_cost_center

			return details

	def get_products_and_warehouses(self) -> Tuple[List[str], List[str]]:
		"""Get list of products and warehouses affected by a transaction"""

		if not (hasattr(self, "products") or hasattr(self, "packed_products")):
			return [], []

		product_rows = (self.get("products") or []) + (self.get("packed_products") or [])

		products = {d.product_code for d in product_rows if d.product_code}

		warehouses = set()
		for d in product_rows:
			if d.get("warehouse"):
				warehouses.add(d.warehouse)

			if self.doctype == "Stock Entry":
				if d.get("s_warehouse"):
					warehouses.add(d.s_warehouse)
				if d.get("t_warehouse"):
					warehouses.add(d.t_warehouse)

		return list(products), list(warehouses)

	def get_stock_ledger_details(self):
		stock_ledger = {}
		stock_ledger_entries = frappe.db.sql(
			"""
			select
				name, warehouse, stock_value_difference, valuation_rate,
				voucher_detail_no, product_code, posting_date, posting_time,
				actual_qty, qty_after_transaction
			from
				`tabStock Ledger Entry`
			where
				voucher_type=%s and voucher_no=%s and is_cancelled = 0
		""",
			(self.doctype, self.name),
			as_dict=True,
		)

		for sle in stock_ledger_entries:
			stock_ledger.setdefault(sle.voucher_detail_no, []).append(sle)
		return stock_ledger

	def make_batches(self, warehouse_field):
		"""Create batches if required. Called before submit"""
		for d in self.products:
			if d.get(warehouse_field) and not d.batch_no:
				has_batch_no, create_new_batch = frappe.get_cached_value(
					"Product", d.product_code, ["has_batch_no", "create_new_batch"]
				)

				if has_batch_no and create_new_batch:
					d.batch_no = (
						frappe.get_doc(
							dict(
								doctype="Batch",
								product=d.product_code,
								supplier=getattr(self, "supplier", None),
								reference_doctype=self.doctype,
								reference_name=self.name,
							)
						)
						.insert()
						.name
					)

	def check_expense_account(self, product):
		if not product.get("expense_account"):
			msg = _("Please set an Expense Account in the Products table")
			frappe.throw(
				_("Row #{0}: Expense Account not set for the Product {1}. {2}").format(
					product.idx, frappe.bold(product.product_code), msg
				),
				title=_("Expense Account Missing"),
			)

		else:
			is_expense_account = (
				frappe.get_cached_value("Account", product.get("expense_account"), "report_type")
				== "Profit and Loss"
			)
			if (
				self.doctype
				not in (
					"Purchase Receipt",
					"Purchase Invoice",
					"Stock Reconciliation",
					"Stock Entry",
					"Subcontracting Receipt",
				)
				and not is_expense_account
			):
				frappe.throw(
					_("Expense / Difference account ({0}) must be a 'Profit or Loss' account").format(
						product.get("expense_account")
					)
				)
			if is_expense_account and not product.get("cost_center"):
				frappe.throw(
					_("{0} {1}: Cost Center is mandatory for Product {2}").format(
						_(self.doctype), self.name, product.get("product_code")
					)
				)

	def delete_auto_created_batches(self):
		for d in self.products:
			if not d.batch_no:
				continue

			frappe.db.set_value(
				"Serial No", {"batch_no": d.batch_no, "status": "Inactive"}, "batch_no", None
			)

			d.batch_no = None
			d.db_set("batch_no", None)

		for data in frappe.get_all(
			"Batch", {"reference_name": self.name, "reference_doctype": self.doctype}
		):
			frappe.delete_doc("Batch", data.name)

	def get_sl_entries(self, d, args):
		sl_dict = frappe._dict(
			{
				"product_code": d.get("product_code", None),
				"warehouse": d.get("warehouse", None),
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"fiscal_year": get_fiscal_year(self.posting_date, company=self.company)[0],
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"voucher_detail_no": d.name,
				"actual_qty": (self.docstatus == 1 and 1 or -1) * flt(d.get("stock_qty")),
				"stock_uom": frappe.get_cached_value(
					"Product", args.get("product_code") or d.get("product_code"), "stock_uom"
				),
				"incoming_rate": 0,
				"company": self.company,
				"batch_no": cstr(d.get("batch_no")).strip(),
				"serial_no": d.get("serial_no"),
				"project": d.get("project") or self.get("project"),
				"is_cancelled": 1 if self.docstatus == 2 else 0,
			}
		)

		sl_dict.update(args)
		self.update_inventory_dimensions(d, sl_dict)

		return sl_dict

	def update_inventory_dimensions(self, row, sl_dict) -> None:
		# To handle delivery note and sales invoice
		if row.get("product_row"):
			row = row.get("product_row")

		dimensions = get_evaluated_inventory_dimension(row, sl_dict, parent_doc=self)
		for dimension in dimensions:
			if not dimension:
				continue

			if self.doctype in [
				"Purchase Invoice",
				"Purchase Receipt",
				"Sales Invoice",
				"Delivery Note",
				"Stock Entry",
			]:
				if (
					(
						sl_dict.actual_qty > 0
						and not self.get("is_return")
						or sl_dict.actual_qty < 0
						and self.get("is_return")
					)
					and self.doctype in ["Purchase Invoice", "Purchase Receipt"]
				) or (
					(
						sl_dict.actual_qty < 0
						and not self.get("is_return")
						or sl_dict.actual_qty > 0
						and self.get("is_return")
					)
					and self.doctype in ["Sales Invoice", "Delivery Note", "Stock Entry"]
				):
					sl_dict[dimension.target_fieldname] = row.get(dimension.source_fieldname)
				else:
					fieldname_start_with = "to"
					if self.doctype in ["Purchase Invoice", "Purchase Receipt"]:
						fieldname_start_with = "from"

					fieldname = f"{fieldname_start_with}_{dimension.source_fieldname}"
					sl_dict[dimension.target_fieldname] = row.get(fieldname)

					if not sl_dict.get(dimension.target_fieldname):
						sl_dict[dimension.target_fieldname] = row.get(dimension.source_fieldname)

			elif row.get(dimension.source_fieldname):
				sl_dict[dimension.target_fieldname] = row.get(dimension.source_fieldname)

			if not sl_dict.get(dimension.target_fieldname) and dimension.fetch_from_parent:
				sl_dict[dimension.target_fieldname] = self.get(dimension.fetch_from_parent)

				# Get value based on doctype name
				if not sl_dict.get(dimension.target_fieldname):
					fieldname = next(
						(
							field.fieldname
							for field in frappe.get_meta(self.doctype).fields
							if field.options == dimension.fetch_from_parent
						),
						None,
					)

					if fieldname and self.get(fieldname):
						sl_dict[dimension.target_fieldname] = self.get(fieldname)

				if sl_dict[dimension.target_fieldname] and self.docstatus == 1:
					row.db_set(dimension.source_fieldname, sl_dict[dimension.target_fieldname])

	def make_sl_entries(self, sl_entries, allow_negative_stock=False, via_landed_cost_voucher=False):
		from erpnext.stock.stock_ledger import make_sl_entries

		make_sl_entries(sl_entries, allow_negative_stock, via_landed_cost_voucher)

	def make_gl_entries_on_cancel(self):
		if frappe.db.sql(
			"""select name from `tabGL Entry` where voucher_type=%s
			and voucher_no=%s""",
			(self.doctype, self.name),
		):
			self.make_gl_entries()

	def get_serialized_products(self):
		serialized_products = []
		product_codes = list(set(d.product_code for d in self.get("products")))
		if product_codes:
			serialized_products = frappe.db.sql_list(
				"""select name from `tabProduct`
				where has_serial_no=1 and name in ({})""".format(
					", ".join(["%s"] * len(product_codes))
				),
				tuple(product_codes),
			)

		return serialized_products

	def validate_warehouse(self):
		from erpnext.stock.utils import validate_disabled_warehouse, validate_warehouse_company

		warehouses = list(set(d.warehouse for d in self.get("products") if getattr(d, "warehouse", None)))

		target_warehouses = list(
			set([d.target_warehouse for d in self.get("products") if getattr(d, "target_warehouse", None)])
		)

		warehouses.extend(target_warehouses)

		from_warehouse = list(
			set([d.from_warehouse for d in self.get("products") if getattr(d, "from_warehouse", None)])
		)

		warehouses.extend(from_warehouse)

		for w in warehouses:
			validate_disabled_warehouse(w)
			validate_warehouse_company(w, self.company)

	def update_billing_percentage(self, update_modified=True):
		target_ref_field = "amount"
		if self.doctype == "Delivery Note":
			target_ref_field = "amount - (returned_qty * rate)"

		self._update_percent_field(
			{
				"target_dt": self.doctype + " Product",
				"target_parent_dt": self.doctype,
				"target_parent_field": "per_billed",
				"target_ref_field": target_ref_field,
				"target_field": "billed_amt",
				"name": self.name,
			},
			update_modified,
		)

	def validate_inspection(self):
		"""Checks if quality inspection is set/ is valid for Products that require inspection."""
		inspection_fieldname_map = {
			"Purchase Receipt": "inspection_required_before_purchase",
			"Purchase Invoice": "inspection_required_before_purchase",
			"Sales Invoice": "inspection_required_before_delivery",
			"Delivery Note": "inspection_required_before_delivery",
		}
		inspection_required_fieldname = inspection_fieldname_map.get(self.doctype)

		# return if inspection is not required on document level
		if (
			(not inspection_required_fieldname and self.doctype != "Stock Entry")
			or (self.doctype == "Stock Entry" and not self.inspection_required)
			or (self.doctype in ["Sales Invoice", "Purchase Invoice"] and not self.update_stock)
		):
			return

		for row in self.get("products"):
			qi_required = False
			if inspection_required_fieldname and frappe.db.get_value(
				"Product", row.product_code, inspection_required_fieldname
			):
				qi_required = True
			elif self.doctype == "Stock Entry" and row.t_warehouse:
				qi_required = True  # inward stock needs inspection

			if qi_required:  # validate row only if inspection is required on product level
				self.validate_qi_presence(row)
				if self.docstatus == 1:
					self.validate_qi_submission(row)
					self.validate_qi_rejection(row)

	def validate_qi_presence(self, row):
		"""Check if QI is present on row level. Warn on save and stop on submit if missing."""
		if not row.quality_inspection:
			msg = f"Row #{row.idx}: Quality Inspection is required for Product {frappe.bold(row.product_code)}"
			if self.docstatus == 1:
				frappe.throw(_(msg), title=_("Inspection Required"), exc=QualityInspectionRequiredError)
			else:
				frappe.msgprint(_(msg), title=_("Inspection Required"), indicator="blue")

	def validate_qi_submission(self, row):
		"""Check if QI is submitted on row level, during submission"""
		action = frappe.db.get_single_value(
			"Stock Settings", "action_if_quality_inspection_is_not_submitted"
		)
		qa_docstatus = frappe.db.get_value("Quality Inspection", row.quality_inspection, "docstatus")

		if not qa_docstatus == 1:
			link = frappe.utils.get_link_to_form("Quality Inspection", row.quality_inspection)
			msg = (
				f"Row #{row.idx}: Quality Inspection {link} is not submitted for the product: {row.product_code}"
			)
			if action == "Stop":
				frappe.throw(_(msg), title=_("Inspection Submission"), exc=QualityInspectionNotSubmittedError)
			else:
				frappe.msgprint(_(msg), alert=True, indicator="orange")

	def validate_qi_rejection(self, row):
		"""Check if QI is rejected on row level, during submission"""
		action = frappe.db.get_single_value("Stock Settings", "action_if_quality_inspection_is_rejected")
		qa_status = frappe.db.get_value("Quality Inspection", row.quality_inspection, "status")

		if qa_status == "Rejected":
			link = frappe.utils.get_link_to_form("Quality Inspection", row.quality_inspection)
			msg = f"Row #{row.idx}: Quality Inspection {link} was rejected for product {row.product_code}"
			if action == "Stop":
				frappe.throw(_(msg), title=_("Inspection Rejected"), exc=QualityInspectionRejectedError)
			else:
				frappe.msgprint(_(msg), alert=True, indicator="orange")

	def update_blanket_order(self):
		blanket_orders = list(set([d.blanket_order for d in self.products if d.blanket_order]))
		for blanket_order in blanket_orders:
			frappe.get_doc("Blanket Order", blanket_order).update_ordered_qty()

	def validate_customer_provided_product(self):
		for d in self.get("products"):
			# Customer Provided parts will have zero valuation rate
			if frappe.get_cached_value("Product", d.product_code, "is_customer_provided_product"):
				d.allow_zero_valuation_rate = 1

	def set_rate_of_stock_uom(self):
		if self.doctype in [
			"Purchase Receipt",
			"Purchase Invoice",
			"Purchase Order",
			"Sales Invoice",
			"Sales Order",
			"Delivery Note",
			"Quotation",
		]:
			for d in self.get("products"):
				d.stock_uom_rate = d.rate / (d.conversion_factor or 1)

	def validate_internal_transfer(self):
		if (
			self.doctype in ("Sales Invoice", "Delivery Note", "Purchase Invoice", "Purchase Receipt")
			and self.is_internal_transfer()
		):
			self.validate_in_transit_warehouses()
			self.validate_multi_currency()
			self.validate_packed_products()

	def validate_in_transit_warehouses(self):
		if (
			self.doctype == "Sales Invoice" and self.get("update_stock")
		) or self.doctype == "Delivery Note":
			for product in self.get("products"):
				if not product.target_warehouse:
					frappe.throw(
						_("Row {0}: Target Warehouse is mandatory for internal transfers").format(product.idx)
					)

		if (
			self.doctype == "Purchase Invoice" and self.get("update_stock")
		) or self.doctype == "Purchase Receipt":
			for product in self.get("products"):
				if not product.from_warehouse:
					frappe.throw(
						_("Row {0}: From Warehouse is mandatory for internal transfers").format(product.idx)
					)

	def validate_multi_currency(self):
		if self.currency != self.company_currency:
			frappe.throw(_("Internal transfers can only be done in company's default currency"))

	def validate_packed_products(self):
		if self.doctype in ("Sales Invoice", "Delivery Note Product") and self.get("packed_products"):
			frappe.throw(_("Packed Products cannot be transferred internally"))

	def validate_putaway_capacity(self):
		# if over receipt is attempted while 'apply putaway rule' is disabled
		# and if rule was applied on the transaction, validate it.
		from erpnext.stock.doctype.putaway_rule.putaway_rule import get_available_putaway_capacity

		valid_doctype = self.doctype in (
			"Purchase Receipt",
			"Stock Entry",
			"Purchase Invoice",
			"Stock Reconciliation",
		)

		if self.doctype == "Purchase Invoice" and self.get("update_stock") == 0:
			valid_doctype = False

		if valid_doctype:
			rule_map = defaultdict(dict)
			for product in self.get("products"):
				warehouse_field = "t_warehouse" if self.doctype == "Stock Entry" else "warehouse"
				rule = frappe.db.get_value(
					"Putaway Rule",
					{"product_code": product.get("product_code"), "warehouse": product.get(warehouse_field)},
					["name", "disable"],
					as_dict=True,
				)
				if rule:
					if rule.get("disabled"):
						continue  # dont validate for disabled rule

					if self.doctype == "Stock Reconciliation":
						stock_qty = flt(product.qty)
					else:
						stock_qty = flt(product.transfer_qty) if self.doctype == "Stock Entry" else flt(product.stock_qty)

					rule_name = rule.get("name")
					if not rule_map[rule_name]:
						rule_map[rule_name]["warehouse"] = product.get(warehouse_field)
						rule_map[rule_name]["product"] = product.get("product_code")
						rule_map[rule_name]["qty_put"] = 0
						rule_map[rule_name]["capacity"] = get_available_putaway_capacity(rule_name)
					rule_map[rule_name]["qty_put"] += flt(stock_qty)

			for rule, values in rule_map.products():
				if flt(values["qty_put"]) > flt(values["capacity"]):
					message = self.prepare_over_receipt_message(rule, values)
					frappe.throw(msg=message, title=_("Over Receipt"))

	def prepare_over_receipt_message(self, rule, values):
		message = _(
			"{0} qty of Product {1} is being received into Warehouse {2} with capacity {3}."
		).format(
			frappe.bold(values["qty_put"]),
			frappe.bold(values["product"]),
			frappe.bold(values["warehouse"]),
			frappe.bold(values["capacity"]),
		)
		message += "<br><br>"
		rule_link = frappe.utils.get_link_to_form("Putaway Rule", rule)
		message += _("Please adjust the qty or edit {0} to proceed.").format(rule_link)
		return message

	def repost_future_sle_and_gle(self, force=False):
		args = frappe._dict(
			{
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"company": self.company,
			}
		)

		if self.docstatus == 2:
			force = True

		if force or future_sle_exists(args) or repost_required_for_queue(self):
			product_based_reposting = cint(
				frappe.db.get_single_value("Stock Reposting Settings", "product_based_reposting")
			)
			if product_based_reposting:
				create_product_wise_repost_entries(voucher_type=self.doctype, voucher_no=self.name)
			else:
				create_repost_product_valuation_entry(args)

	def add_gl_entry(
		self,
		gl_entries,
		account,
		cost_center,
		debit,
		credit,
		remarks,
		against_account,
		debit_in_account_currency=None,
		credit_in_account_currency=None,
		account_currency=None,
		project=None,
		voucher_detail_no=None,
		product=None,
		posting_date=None,
	):

		gl_entry = {
			"account": account,
			"cost_center": cost_center,
			"debit": debit,
			"credit": credit,
			"against": against_account,
			"remarks": remarks,
		}

		if voucher_detail_no:
			gl_entry.update({"voucher_detail_no": voucher_detail_no})

		if debit_in_account_currency:
			gl_entry.update({"debit_in_account_currency": debit_in_account_currency})

		if credit_in_account_currency:
			gl_entry.update({"credit_in_account_currency": credit_in_account_currency})

		if posting_date:
			gl_entry.update({"posting_date": posting_date})

		gl_entries.append(self.get_gl_dict(gl_entry, product=product))


def repost_required_for_queue(doc: StockController) -> bool:
	"""check if stock document contains repeated product-warehouse with queue based valuation.

	if queue exists for repeated products then SLEs need to reprocessed in background again.
	"""

	consuming_sles = frappe.db.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": doc.doctype,
			"voucher_no": doc.name,
			"actual_qty": ("<", 0),
			"is_cancelled": 0,
		},
		fields=["product_code", "warehouse", "stock_queue"],
	)
	product_warehouses = [(sle.product_code, sle.warehouse) for sle in consuming_sles]

	unique_product_warehouses = set(product_warehouses)

	if len(unique_product_warehouses) == len(product_warehouses):
		return False

	for sle in consuming_sles:
		if sle.stock_queue != "[]":  # using FIFO/LIFO valuation
			return True
	return False


@frappe.whitelist()
def make_quality_inspections(doctype, docname, products):
	if isinstance(products, str):
		products = json.loads(products)

	inspections = []
	for product in products:
		if flt(product.get("sample_size")) > flt(product.get("qty")):
			frappe.throw(
				_(
					"{product_name}'s Sample Size ({sample_size}) cannot be greater than the Accepted Quantity ({accepted_quantity})"
				).format(
					product_name=product.get("product_name"),
					sample_size=product.get("sample_size"),
					accepted_quantity=product.get("qty"),
				)
			)

		quality_inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"inspected_by": frappe.session.user,
				"reference_type": doctype,
				"reference_name": docname,
				"product_code": product.get("product_code"),
				"description": product.get("description"),
				"sample_size": flt(product.get("sample_size")),
				"product_serial_no": product.get("serial_no").split("\n")[0] if product.get("serial_no") else None,
				"batch_no": product.get("batch_no"),
			}
		).insert()
		quality_inspection.save()
		inspections.append(quality_inspection.name)

	return inspections


def is_reposting_pending():
	return frappe.db.exists(
		"Repost Product Valuation", {"docstatus": 1, "status": ["in", ["Queued", "In Progress"]]}
	)


def future_sle_exists(args, sl_entries=None):
	key = (args.voucher_type, args.voucher_no)
	if not hasattr(frappe.local, "future_sle"):
		frappe.local.future_sle = {}

	if validate_future_sle_not_exists(args, key, sl_entries):
		return False
	elif get_cached_data(args, key):
		return True

	if not sl_entries:
		sl_entries = get_sle_entries_against_voucher(args)
		if not sl_entries:
			return

	or_conditions = get_conditions_to_validate_future_sle(sl_entries)

	data = frappe.db.sql(
		"""
		select product_code, warehouse, count(name) as total_row
		from `tabStock Ledger Entry` force index (product_warehouse)
		where
			({})
			and timestamp(posting_date, posting_time)
				>= timestamp(%(posting_date)s, %(posting_time)s)
			and voucher_no != %(voucher_no)s
			and is_cancelled = 0
		GROUP BY
			product_code, warehouse
		""".format(
			" or ".join(or_conditions)
		),
		args,
		as_dict=1,
	)

	for d in data:
		frappe.local.future_sle[key][(d.product_code, d.warehouse)] = d.total_row

	return len(data)


def validate_future_sle_not_exists(args, key, sl_entries=None):
	product_key = ""
	if args.get("product_code"):
		product_key = (args.get("product_code"), args.get("warehouse"))

	if not sl_entries and hasattr(frappe.local, "future_sle"):
		if key not in frappe.local.future_sle:
			return False

		if not frappe.local.future_sle.get(key) or (
			product_key and product_key not in frappe.local.future_sle.get(key)
		):
			return True


def get_cached_data(args, key):
	if key not in frappe.local.future_sle:
		frappe.local.future_sle[key] = frappe._dict({})

	if args.get("product_code"):
		product_key = (args.get("product_code"), args.get("warehouse"))
		count = frappe.local.future_sle[key].get(product_key)

		return True if (count or count == 0) else False
	else:
		return frappe.local.future_sle[key]


def get_sle_entries_against_voucher(args):
	return frappe.get_all(
		"Stock Ledger Entry",
		filters={"voucher_type": args.voucher_type, "voucher_no": args.voucher_no},
		fields=["product_code", "warehouse"],
		order_by="creation asc",
	)


def get_conditions_to_validate_future_sle(sl_entries):
	warehouse_products_map = {}
	for entry in sl_entries:
		if entry.warehouse not in warehouse_products_map:
			warehouse_products_map[entry.warehouse] = set()

		warehouse_products_map[entry.warehouse].add(entry.product_code)

	or_conditions = []
	for warehouse, products in warehouse_products_map.products():
		or_conditions.append(
			f"""warehouse = {frappe.db.escape(warehouse)}
				and product_code in ({', '.join(frappe.db.escape(product) for product in products)})"""
		)

	return or_conditions


def create_repost_product_valuation_entry(args):
	args = frappe._dict(args)
	repost_entry = frappe.new_doc("Repost Product Valuation")
	repost_entry.based_on = args.based_on
	if not args.based_on:
		repost_entry.based_on = "Transaction" if args.voucher_no else "Product and Warehouse"
	repost_entry.voucher_type = args.voucher_type
	repost_entry.voucher_no = args.voucher_no
	repost_entry.product_code = args.product_code
	repost_entry.warehouse = args.warehouse
	repost_entry.posting_date = args.posting_date
	repost_entry.posting_time = args.posting_time
	repost_entry.company = args.company
	repost_entry.allow_zero_rate = args.allow_zero_rate
	repost_entry.flags.ignore_links = True
	repost_entry.flags.ignore_permissions = True
	repost_entry.save()
	repost_entry.submit()


def create_product_wise_repost_entries(voucher_type, voucher_no, allow_zero_rate=False):
	"""Using a voucher create repost product valuation records for all product-warehouse pairs."""

	stock_ledger_entries = get_products_to_be_repost(voucher_type, voucher_no)

	distinct_product_warehouses = set()
	repost_entries = []

	for sle in stock_ledger_entries:
		product_wh = (sle.product_code, sle.warehouse)
		if product_wh in distinct_product_warehouses:
			continue
		distinct_product_warehouses.add(product_wh)

		repost_entry = frappe.new_doc("Repost Product Valuation")
		repost_entry.based_on = "Product and Warehouse"
		repost_entry.voucher_type = voucher_type
		repost_entry.voucher_no = voucher_no

		repost_entry.product_code = sle.product_code
		repost_entry.warehouse = sle.warehouse
		repost_entry.posting_date = sle.posting_date
		repost_entry.posting_time = sle.posting_time
		repost_entry.allow_zero_rate = allow_zero_rate
		repost_entry.flags.ignore_links = True
		repost_entry.flags.ignore_permissions = True
		repost_entry.submit()
		repost_entries.append(repost_entry)

	return repost_entries
