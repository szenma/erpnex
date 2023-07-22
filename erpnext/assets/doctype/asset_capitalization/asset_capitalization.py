# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe

# import erpnext
from frappe import _
from frappe.utils import cint, flt, get_link_to_form
from six import string_types

import erpnext
from erpnext.assets.doctype.asset.asset import get_asset_value_after_depreciation
from erpnext.assets.doctype.asset.depreciation import (
	depreciate_asset,
	get_gl_entries_on_asset_disposal,
	get_value_after_depreciation_on_disposal_date,
	reset_depreciation_schedule,
	reverse_depreciation_entry_made_after_disposal,
)
from erpnext.assets.doctype.asset_category.asset_category import get_asset_category_account
from erpnext.controllers.stock_controller import StockController
from erpnext.setup.doctype.brand.brand import get_brand_defaults
from erpnext.setup.doctype.product_group.product_group import get_product_group_defaults
from erpnext.stock import get_warehouse_account_map
from erpnext.stock.doctype.product.product import get_product_defaults
from erpnext.stock.get_product_details import (
	get_default_cost_center,
	get_default_expense_account,
	get_product_warehouse,
)
from erpnext.stock.stock_ledger import get_previous_sle
from erpnext.stock.utils import get_incoming_rate

force_fields = [
	"target_product_name",
	"target_asset_name",
	"product_name",
	"asset_name",
	"target_is_fixed_asset",
	"target_has_serial_no",
	"target_has_batch_no",
	"target_stock_uom",
	"stock_uom",
	"fixed_asset_account",
	"valuation_rate",
]


class AssetCapitalization(StockController):
	def validate(self):
		self.validate_posting_time()
		self.set_missing_values(for_validate=True)
		self.validate_target_product()
		self.validate_consumed_stock_product()
		self.validate_consumed_asset_product()
		self.validate_service_product()
		self.set_warehouse_details()
		self.set_asset_values()
		self.calculate_totals()
		self.set_title()

	def before_submit(self):
		self.validate_source_mandatory()
		if self.entry_type == "Capitalization":
			self.create_target_asset()

	def on_submit(self):
		self.update_stock_ledger()
		self.make_gl_entries()

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Repost Product Valuation")
		self.update_stock_ledger()
		self.make_gl_entries()
		self.restore_consumed_asset_products()

	def set_title(self):
		self.title = self.target_asset_name or self.target_product_name or self.target_product_code

	def set_missing_values(self, for_validate=False):
		target_product_details = get_target_product_details(self.target_product_code, self.company)
		for k, v in target_product_details.products():
			if self.meta.has_field(k) and (not self.get(k) or k in force_fields):
				self.set(k, v)

		for d in self.stock_products:
			args = self.as_dict()
			args.update(d.as_dict())
			args.doctype = self.doctype
			args.name = self.name
			consumed_stock_product_details = get_consumed_stock_product_details(args)
			for k, v in consumed_stock_product_details.products():
				if d.meta.has_field(k) and (not d.get(k) or k in force_fields):
					d.set(k, v)

		for d in self.asset_products:
			args = self.as_dict()
			args.update(d.as_dict())
			args.doctype = self.doctype
			args.name = self.name
			args.finance_book = d.get("finance_book") or self.get("finance_book")
			consumed_asset_details = get_consumed_asset_details(args)
			for k, v in consumed_asset_details.products():
				if d.meta.has_field(k) and (not d.get(k) or k in force_fields):
					d.set(k, v)

		for d in self.service_products:
			args = self.as_dict()
			args.update(d.as_dict())
			args.doctype = self.doctype
			args.name = self.name
			service_product_details = get_service_product_details(args)
			for k, v in service_product_details.products():
				if d.meta.has_field(k) and (not d.get(k) or k in force_fields):
					d.set(k, v)

	def validate_target_product(self):
		target_product = frappe.get_cached_doc("Product", self.target_product_code)

		if not target_product.is_fixed_asset and not target_product.is_stock_product:
			frappe.throw(
				_("Target Product {0} is neither a Fixed Asset nor a Stock Product").format(target_product.name)
			)

		if self.entry_type == "Capitalization" and not target_product.is_fixed_asset:
			frappe.throw(_("Target Product {0} must be a Fixed Asset product").format(target_product.name))
		elif self.entry_type == "Decapitalization" and not target_product.is_stock_product:
			frappe.throw(_("Target Product {0} must be a Stock Product").format(target_product.name))

		if target_product.is_fixed_asset:
			self.target_qty = 1
		if flt(self.target_qty) <= 0:
			frappe.throw(_("Target Qty must be a positive number"))

		if not target_product.is_stock_product:
			self.target_warehouse = None
		if not target_product.has_batch_no:
			self.target_batch_no = None
		if not target_product.has_serial_no:
			self.target_serial_no = ""

		if target_product.is_stock_product and not self.target_warehouse:
			frappe.throw(_("Target Warehouse is mandatory for Decapitalization"))

		self.validate_product(target_product)

	def validate_consumed_stock_product(self):
		for d in self.stock_products:
			if d.product_code:
				product = frappe.get_cached_doc("Product", d.product_code)

				if not product.is_stock_product:
					frappe.throw(_("Row #{0}: Product {1} is not a stock product").format(d.idx, d.product_code))

				if flt(d.stock_qty) <= 0:
					frappe.throw(_("Row #{0}: Qty must be a positive number").format(d.idx))

				self.validate_product(product)

	def validate_consumed_asset_product(self):
		for d in self.asset_products:
			if d.asset:
				if d.asset == self.target_asset:
					frappe.throw(
						_("Row #{0}: Consumed Asset {1} cannot be the same as the Target Asset").format(
							d.idx, d.asset
						)
					)

				asset = self.get_asset_for_validation(d.asset)
				self.validate_asset(asset)

	def validate_service_product(self):
		for d in self.service_products:
			if d.product_code:
				product = frappe.get_cached_doc("Product", d.product_code)

				if product.is_stock_product or product.is_fixed_asset:
					frappe.throw(_("Row #{0}: Product {1} is not a service product").format(d.idx, d.product_code))

				if flt(d.qty) <= 0:
					frappe.throw(_("Row #{0}: Qty must be a positive number").format(d.idx))

				if flt(d.rate) <= 0:
					frappe.throw(_("Row #{0}: Amount must be a positive number").format(d.idx))

				self.validate_product(product)

			if not d.cost_center:
				d.cost_center = frappe.get_cached_value("Company", self.company, "cost_center")

	def validate_source_mandatory(self):
		if not self.target_is_fixed_asset and not self.get("asset_products"):
			frappe.throw(_("Consumed Asset Products is mandatory for Decapitalization"))

		if not self.get("stock_products") and not self.get("asset_products"):
			frappe.throw(_("Consumed Stock Products or Consumed Asset Products is mandatory for Capitalization"))

	def validate_product(self, product):
		from erpnext.stock.doctype.product.product import validate_end_of_life

		validate_end_of_life(product.name, product.end_of_life, product.disabled)

	def get_asset_for_validation(self, asset):
		return frappe.db.get_value(
			"Asset", asset, ["name", "product_code", "company", "status", "docstatus"], as_dict=1
		)

	def validate_asset(self, asset):
		if asset.status in ("Draft", "Scrapped", "Sold", "Capitalized", "Decapitalized"):
			frappe.throw(_("Asset {0} is {1}").format(asset.name, asset.status))

		if asset.docstatus == 0:
			frappe.throw(_("Asset {0} is Draft").format(asset.name))
		if asset.docstatus == 2:
			frappe.throw(_("Asset {0} is cancelled").format(asset.name))

		if asset.company != self.company:
			frappe.throw(_("Asset {0} does not belong to company {1}").format(asset.name, self.company))

	@frappe.whitelist()
	def set_warehouse_details(self):
		for d in self.get("stock_products"):
			if d.product_code and d.warehouse:
				args = self.get_args_for_incoming_rate(d)
				warehouse_details = get_warehouse_details(args)
				d.update(warehouse_details)

	@frappe.whitelist()
	def set_asset_values(self):
		for d in self.get("asset_products"):
			if d.asset:
				finance_book = d.get("finance_book") or self.get("finance_book")
				d.current_asset_value = flt(
					get_asset_value_after_depreciation(d.asset, finance_book=finance_book)
				)
				d.asset_value = get_value_after_depreciation_on_disposal_date(
					d.asset, self.posting_date, finance_book=finance_book
				)

	def get_args_for_incoming_rate(self, product):
		return frappe._dict(
			{
				"product_code": product.product_code,
				"warehouse": product.warehouse,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": -1 * flt(product.stock_qty),
				"serial_no": product.serial_no,
				"batch_no": product.batch_no,
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"company": self.company,
				"allow_zero_valuation": cint(product.get("allow_zero_valuation_rate")),
			}
		)

	def calculate_totals(self):
		self.stock_products_total = 0
		self.asset_products_total = 0
		self.service_products_total = 0

		for d in self.stock_products:
			d.amount = flt(flt(d.stock_qty) * flt(d.valuation_rate), d.precision("amount"))
			self.stock_products_total += d.amount

		for d in self.asset_products:
			d.asset_value = flt(flt(d.asset_value), d.precision("asset_value"))
			self.asset_products_total += d.asset_value

		for d in self.service_products:
			d.amount = flt(flt(d.qty) * flt(d.rate), d.precision("amount"))
			self.service_products_total += d.amount

		self.stock_products_total = flt(self.stock_products_total, self.precision("stock_products_total"))
		self.asset_products_total = flt(self.asset_products_total, self.precision("asset_products_total"))
		self.service_products_total = flt(self.service_products_total, self.precision("service_products_total"))

		self.total_value = self.stock_products_total + self.asset_products_total + self.service_products_total
		self.total_value = flt(self.total_value, self.precision("total_value"))

		self.target_qty = flt(self.target_qty, self.precision("target_qty"))
		self.target_incoming_rate = self.total_value / self.target_qty

	def update_stock_ledger(self):
		sl_entries = []

		for d in self.stock_products:
			sle = self.get_sl_entries(
				d,
				{
					"actual_qty": -flt(d.stock_qty),
				},
			)
			sl_entries.append(sle)

		if self.entry_type == "Decapitalization" and not self.target_is_fixed_asset:
			sle = self.get_sl_entries(
				self,
				{
					"product_code": self.target_product_code,
					"warehouse": self.target_warehouse,
					"batch_no": self.target_batch_no,
					"serial_no": self.target_serial_no,
					"actual_qty": flt(self.target_qty),
					"incoming_rate": flt(self.target_incoming_rate),
				},
			)
			sl_entries.append(sle)

		# reverse sl entries if cancel
		if self.docstatus == 2:
			sl_entries.reverse()

		if sl_entries:
			self.make_sl_entries(sl_entries)

	def make_gl_entries(self, gl_entries=None, from_repost=False):
		from erpnext.accounts.general_ledger import make_gl_entries, make_reverse_gl_entries

		if self.docstatus == 1:
			if not gl_entries:
				gl_entries = self.get_gl_entries()

			if gl_entries:
				make_gl_entries(gl_entries, from_repost=from_repost)
		elif self.docstatus == 2:
			make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)

	def get_gl_entries(
		self, warehouse_account=None, default_expense_account=None, default_cost_center=None
	):
		# Stock GL Entries
		gl_entries = []

		self.warehouse_account = warehouse_account
		if not self.warehouse_account:
			self.warehouse_account = get_warehouse_account_map(self.company)

		precision = self.get_debit_field_precision()
		self.sle_map = self.get_stock_ledger_details()

		target_account = self.get_target_account()
		target_against = set()

		self.get_gl_entries_for_consumed_stock_products(
			gl_entries, target_account, target_against, precision
		)
		self.get_gl_entries_for_consumed_asset_products(
			gl_entries, target_account, target_against, precision
		)
		self.get_gl_entries_for_consumed_service_products(
			gl_entries, target_account, target_against, precision
		)

		if not self.stock_products and not self.service_products and self.are_all_asset_products_non_depreciable:
			return []

		self.get_gl_entries_for_target_product(gl_entries, target_against, precision)

		return gl_entries

	def get_target_account(self):
		if self.target_is_fixed_asset:
			return self.target_fixed_asset_account
		else:
			return self.warehouse_account[self.target_warehouse]["account"]

	def get_gl_entries_for_consumed_stock_products(
		self, gl_entries, target_account, target_against, precision
	):
		# Consumed Stock Products
		for product_row in self.stock_products:
			sle_list = self.sle_map.get(product_row.name)
			if sle_list:
				for sle in sle_list:
					stock_value_difference = flt(sle.stock_value_difference, precision)

					if erpnext.is_perpetual_inventory_enabled(self.company):
						account = self.warehouse_account[sle.warehouse]["account"]
					else:
						account = self.get_company_default("default_expense_account")

					target_against.add(account)
					gl_entries.append(
						self.get_gl_dict(
							{
								"account": account,
								"against": target_account,
								"cost_center": product_row.cost_center,
								"project": product_row.get("project") or self.get("project"),
								"remarks": self.get("remarks") or "Accounting Entry for Stock",
								"credit": -1 * stock_value_difference,
							},
							self.warehouse_account[sle.warehouse]["account_currency"],
							product=product_row,
						)
					)

	def get_gl_entries_for_consumed_asset_products(
		self, gl_entries, target_account, target_against, precision
	):
		self.are_all_asset_products_non_depreciable = True

		# Consumed Assets
		for product in self.asset_products:
			asset = frappe.get_doc("Asset", product.asset)

			if asset.calculate_depreciation:
				self.are_all_asset_products_non_depreciable = False
				depreciate_asset(asset, self.posting_date)
				asset.reload()

			fixed_asset_gl_entries = get_gl_entries_on_asset_disposal(
				asset,
				product.asset_value,
				product.get("finance_book") or self.get("finance_book"),
				self.get("doctype"),
				self.get("name"),
				self.get("posting_date"),
			)

			asset.db_set("disposal_date", self.posting_date)

			self.set_consumed_asset_status(asset)

			for gle in fixed_asset_gl_entries:
				gle["against"] = target_account
				gl_entries.append(self.get_gl_dict(gle, product=product))
				target_against.add(gle["account"])

	def get_gl_entries_for_consumed_service_products(
		self, gl_entries, target_account, target_against, precision
	):
		# Service Expenses
		for product_row in self.service_products:
			expense_amount = flt(product_row.amount, precision)
			target_against.add(product_row.expense_account)

			gl_entries.append(
				self.get_gl_dict(
					{
						"account": product_row.expense_account,
						"against": target_account,
						"cost_center": product_row.cost_center,
						"project": product_row.get("project") or self.get("project"),
						"remarks": self.get("remarks") or "Accounting Entry for Stock",
						"credit": expense_amount,
					},
					product=product_row,
				)
			)

	def get_gl_entries_for_target_product(self, gl_entries, target_against, precision):
		if self.target_is_fixed_asset:
			# Capitalization
			gl_entries.append(
				self.get_gl_dict(
					{
						"account": self.target_fixed_asset_account,
						"against": ", ".join(target_against),
						"remarks": self.get("remarks") or _("Accounting Entry for Asset"),
						"debit": flt(self.total_value, precision),
						"cost_center": self.get("cost_center"),
					},
					product=self,
				)
			)
		else:
			# Target Stock Product
			sle_list = self.sle_map.get(self.name)
			for sle in sle_list:
				stock_value_difference = flt(sle.stock_value_difference, precision)
				account = self.warehouse_account[sle.warehouse]["account"]

				gl_entries.append(
					self.get_gl_dict(
						{
							"account": account,
							"against": ", ".join(target_against),
							"cost_center": self.cost_center,
							"project": self.get("project"),
							"remarks": self.get("remarks") or "Accounting Entry for Stock",
							"debit": stock_value_difference,
						},
						self.warehouse_account[sle.warehouse]["account_currency"],
						product=self,
					)
				)

	def create_target_asset(self):
		total_target_asset_value = flt(self.total_value, self.precision("total_value"))
		asset_doc = frappe.new_doc("Asset")
		asset_doc.company = self.company
		asset_doc.product_code = self.target_product_code
		asset_doc.is_existing_asset = 1
		asset_doc.location = self.target_asset_location
		asset_doc.available_for_use_date = self.posting_date
		asset_doc.purchase_date = self.posting_date
		asset_doc.gross_purchase_amount = total_target_asset_value
		asset_doc.purchase_receipt_amount = total_target_asset_value
		asset_doc.flags.ignore_validate = True
		asset_doc.insert()

		self.target_asset = asset_doc.name

		self.target_fixed_asset_account = get_asset_category_account(
			"fixed_asset_account", product=self.target_product_code, company=asset_doc.company
		)

		frappe.msgprint(
			_(
				"Asset {0} has been created. Please set the depreciation details if any and submit it."
			).format(get_link_to_form("Asset", asset_doc.name))
		)

	def restore_consumed_asset_products(self):
		for product in self.asset_products:
			asset = frappe.get_doc("Asset", product.asset)
			asset.db_set("disposal_date", None)
			self.set_consumed_asset_status(asset)

			if asset.calculate_depreciation:
				reverse_depreciation_entry_made_after_disposal(asset, self.posting_date)
				reset_depreciation_schedule(asset, self.posting_date)

	def set_consumed_asset_status(self, asset):
		if self.docstatus == 1:
			asset.set_status("Capitalized" if self.target_is_fixed_asset else "Decapitalized")
		else:
			asset.set_status()


@frappe.whitelist()
def get_target_product_details(product_code=None, company=None):
	out = frappe._dict()

	# Get Product Details
	product = frappe._dict()
	if product_code:
		product = frappe.get_cached_doc("Product", product_code)

	# Set Product Details
	out.target_product_name = product.product_name
	out.target_stock_uom = product.stock_uom
	out.target_is_fixed_asset = cint(product.is_fixed_asset)
	out.target_has_batch_no = cint(product.has_batch_no)
	out.target_has_serial_no = cint(product.has_serial_no)

	if out.target_is_fixed_asset:
		out.target_qty = 1
		out.target_warehouse = None
	else:
		out.target_asset = None

	if not out.target_has_batch_no:
		out.target_batch_no = None
	if not out.target_has_serial_no:
		out.target_serial_no = ""

	# Cost Center
	product_defaults = get_product_defaults(product.name, company)
	product_group_defaults = get_product_group_defaults(product.name, company)
	brand_defaults = get_brand_defaults(product.name, company)
	out.cost_center = get_default_cost_center(
		frappe._dict({"product_code": product.name, "company": company}),
		product_defaults,
		product_group_defaults,
		brand_defaults,
	)

	return out


@frappe.whitelist()
def get_consumed_stock_product_details(args):
	if isinstance(args, string_types):
		args = json.loads(args)

	args = frappe._dict(args)
	out = frappe._dict()

	product = frappe._dict()
	if args.product_code:
		product = frappe.get_cached_doc("Product", args.product_code)

	out.product_name = product.product_name
	out.batch_no = None
	out.serial_no = ""

	out.stock_qty = flt(args.stock_qty) or 1
	out.stock_uom = product.stock_uom

	out.warehouse = get_product_warehouse(product, args, overwrite_warehouse=True) if product else None

	# Cost Center
	product_defaults = get_product_defaults(product.name, args.company)
	product_group_defaults = get_product_group_defaults(product.name, args.company)
	brand_defaults = get_brand_defaults(product.name, args.company)
	out.cost_center = get_default_cost_center(
		args, product_defaults, product_group_defaults, brand_defaults
	)

	if args.product_code and out.warehouse:
		incoming_rate_args = frappe._dict(
			{
				"product_code": args.product_code,
				"warehouse": out.warehouse,
				"posting_date": args.posting_date,
				"posting_time": args.posting_time,
				"qty": -1 * flt(out.stock_qty),
				"voucher_type": args.doctype,
				"voucher_no": args.name,
				"company": args.company,
				"serial_no": args.serial_no,
				"batch_no": args.batch_no,
			}
		)
		out.update(get_warehouse_details(incoming_rate_args))
	else:
		out.valuation_rate = 0
		out.actual_qty = 0

	return out


@frappe.whitelist()
def get_warehouse_details(args):
	if isinstance(args, string_types):
		args = json.loads(args)

	args = frappe._dict(args)

	out = {}
	if args.warehouse and args.product_code:
		out = {
			"actual_qty": get_previous_sle(args).get("qty_after_transaction") or 0,
			"valuation_rate": get_incoming_rate(args, raise_error_if_no_rate=False),
		}
	return out


@frappe.whitelist()
def get_consumed_asset_details(args):
	if isinstance(args, string_types):
		args = json.loads(args)

	args = frappe._dict(args)
	out = frappe._dict()

	asset_details = frappe._dict()
	if args.asset:
		asset_details = frappe.db.get_value(
			"Asset", args.asset, ["asset_name", "product_code", "product_name"], as_dict=1
		)
		if not asset_details:
			frappe.throw(_("Asset {0} does not exist").format(args.asset))

	out.product_code = asset_details.product_code
	out.asset_name = asset_details.asset_name
	out.product_name = asset_details.product_name

	if args.asset:
		out.current_asset_value = flt(
			get_asset_value_after_depreciation(args.asset, finance_book=args.finance_book)
		)
		out.asset_value = get_value_after_depreciation_on_disposal_date(
			args.asset, args.posting_date, finance_book=args.finance_book
		)
	else:
		out.current_asset_value = 0
		out.asset_value = 0

	# Account
	if asset_details.product_code:
		out.fixed_asset_account = get_asset_category_account(
			"fixed_asset_account", product=asset_details.product_code, company=args.company
		)
	else:
		out.fixed_asset_account = None

	# Cost Center
	if asset_details.product_code:
		product = frappe.get_cached_doc("Product", asset_details.product_code)
		product_defaults = get_product_defaults(product.name, args.company)
		product_group_defaults = get_product_group_defaults(product.name, args.company)
		brand_defaults = get_brand_defaults(product.name, args.company)
		out.cost_center = get_default_cost_center(
			args, product_defaults, product_group_defaults, brand_defaults
		)

	return out


@frappe.whitelist()
def get_service_product_details(args):
	if isinstance(args, string_types):
		args = json.loads(args)

	args = frappe._dict(args)
	out = frappe._dict()

	product = frappe._dict()
	if args.product_code:
		product = frappe.get_cached_doc("Product", args.product_code)

	out.product_name = product.product_name
	out.qty = flt(args.qty) or 1
	out.uom = product.purchase_uom or product.stock_uom

	product_defaults = get_product_defaults(product.name, args.company)
	product_group_defaults = get_product_group_defaults(product.name, args.company)
	brand_defaults = get_brand_defaults(product.name, args.company)

	out.expense_account = get_default_expense_account(
		args, product_defaults, product_group_defaults, brand_defaults
	)
	out.cost_center = get_default_cost_center(
		args, product_defaults, product_group_defaults, brand_defaults
	)

	return out
