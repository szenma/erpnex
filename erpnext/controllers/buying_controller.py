# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import ValidationError, _, msgprint
from frappe.contacts.doctype.address.address import get_address_display
from frappe.utils import cint, cstr, flt, getdate
from frappe.utils.data import nowtime

from erpnext.accounts.doctype.budget.budget import validate_expense_against_budget
from erpnext.accounts.party import get_party_details
from erpnext.buying.utils import update_last_purchase_rate, validate_for_products
from erpnext.controllers.sales_and_purchase_return import get_rate_for_return
from erpnext.controllers.subcontracting_controller import SubcontractingController
from erpnext.stock.get_product_details import get_conversion_factor
from erpnext.stock.utils import get_incoming_rate


class QtyMismatchError(ValidationError):
	pass


class BuyingController(SubcontractingController):
	def __setup__(self):
		self.flags.ignore_permlevel_for_fields = ["buying_price_list", "price_list_currency"]

	def get_feed(self):
		if self.get("supplier_name"):
			return _("From {0} | {1} {2}").format(self.supplier_name, self.currency, self.grand_total)

	def validate(self):
		self.set_rate_for_standalone_debit_note()

		super(BuyingController, self).validate()
		if getattr(self, "supplier", None) and not self.supplier_name:
			self.supplier_name = frappe.db.get_value("Supplier", self.supplier, "supplier_name")

		self.validate_products()
		self.set_qty_as_per_stock_uom()
		self.validate_stock_or_nonstock_products()
		self.validate_warehouse()
		self.validate_from_warehouse()
		self.set_supplier_address()
		self.validate_asset_return()
		self.validate_auto_repeat_subscription_dates()

		if self.doctype == "Purchase Invoice":
			self.validate_purchase_receipt_if_update_stock()

		if self.doctype == "Purchase Receipt" or (
			self.doctype == "Purchase Invoice" and self.update_stock
		):
			# self.validate_purchase_return()
			self.validate_rejected_warehouse()
			self.validate_accepted_rejected_qty()
			validate_for_products(self)

			# sub-contracting
			self.validate_for_subcontracting()
			if self.get("is_old_subcontracting_flow"):
				self.create_raw_materials_supplied()
			self.set_landed_cost_voucher_amount()

		if self.doctype in ("Purchase Receipt", "Purchase Invoice"):
			self.update_valuation_rate()

	def onload(self):
		super(BuyingController, self).onload()
		self.set_onload(
			"backflush_based_on",
			frappe.db.get_single_value(
				"Buying Settings", "backflush_raw_materials_of_subcontract_based_on"
			),
		)

	def set_rate_for_standalone_debit_note(self):
		if self.get("is_return") and self.get("update_stock") and not self.return_against:
			for row in self.products:

				# override the rate with valuation rate
				row.rate = get_incoming_rate(
					{
						"product_code": row.product_code,
						"warehouse": row.warehouse,
						"posting_date": self.get("posting_date"),
						"posting_time": self.get("posting_time"),
						"qty": row.qty,
						"serial_and_batch_bundle": row.get("serial_and_batch_bundle"),
						"company": self.company,
						"voucher_type": self.doctype,
						"voucher_no": self.name,
					},
					raise_error_if_no_rate=False,
				)

				row.discount_percentage = 0.0
				row.discount_amount = 0.0
				row.margin_rate_or_amount = 0.0

	def set_missing_values(self, for_validate=False):
		super(BuyingController, self).set_missing_values(for_validate)

		self.set_supplier_from_product_default()
		self.set_price_list_currency("Buying")

		# set contact and address details for supplier, if they are not mentioned
		if getattr(self, "supplier", None):
			self.update_if_missing(
				get_party_details(
					self.supplier,
					party_type="Supplier",
					doctype=self.doctype,
					company=self.company,
					party_address=self.get("supplier_address"),
					shipping_address=self.get("shipping_address"),
					company_address=self.get("billing_address"),
					fetch_payment_terms_template=not self.get("ignore_default_payment_terms_template"),
					ignore_permissions=self.flags.ignore_permissions,
				)
			)

		self.set_missing_product_details(for_validate)

	def set_supplier_from_product_default(self):
		if self.meta.get_field("supplier") and not self.supplier:
			for d in self.get("products"):
				supplier = frappe.db.get_value(
					"Product Default", {"parent": d.product_code, "company": self.company}, "default_supplier"
				)
				if supplier:
					self.supplier = supplier
				else:
					product_group = frappe.db.get_value("Product", d.product_code, "product_group")
					supplier = frappe.db.get_value(
						"Product Default", {"parent": product_group, "company": self.company}, "default_supplier"
					)
					if supplier:
						self.supplier = supplier
					break

	def validate_stock_or_nonstock_products(self):
		if self.meta.get_field("taxes") and not self.get_stock_products() and not self.get_asset_products():
			msg = _('Tax Category has been changed to "Total" because all the Products are non-stock products')
			self.update_tax_category(msg)

	def update_tax_category(self, msg):
		tax_for_valuation = [
			d for d in self.get("taxes") if d.category in ["Valuation", "Valuation and Total"]
		]

		if tax_for_valuation:
			for d in tax_for_valuation:
				d.category = "Total"

			msgprint(msg)

	def validate_asset_return(self):
		if self.doctype not in ["Purchase Receipt", "Purchase Invoice"] or not self.is_return:
			return

		purchase_doc_field = (
			"purchase_receipt" if self.doctype == "Purchase Receipt" else "purchase_invoice"
		)
		not_cancelled_asset = [
			d.name
			for d in frappe.db.get_all("Asset", {purchase_doc_field: self.return_against, "docstatus": 1})
		]
		if self.is_return and len(not_cancelled_asset):
			frappe.throw(
				_(
					"{} has submitted assets linked to it. You need to cancel the assets to create purchase return."
				).format(self.return_against),
				title=_("Not Allowed"),
			)

	def get_asset_products(self):
		if self.doctype not in ["Purchase Order", "Purchase Invoice", "Purchase Receipt"]:
			return []

		return [d.product_code for d in self.products if d.is_fixed_asset]

	def set_landed_cost_voucher_amount(self):
		for d in self.get("products"):
			lc_voucher_data = frappe.db.sql(
				"""select sum(applicable_charges), cost_center
				from `tabLanded Cost Product`
				where docstatus = 1 and purchase_receipt_product = %s""",
				d.name,
			)
			d.landed_cost_voucher_amount = lc_voucher_data[0][0] if lc_voucher_data else 0.0
			if not d.cost_center and lc_voucher_data and lc_voucher_data[0][1]:
				d.db_set("cost_center", lc_voucher_data[0][1])

	def validate_from_warehouse(self):
		for product in self.get("products"):
			if product.get("from_warehouse") and (product.get("from_warehouse") == product.get("warehouse")):
				frappe.throw(
					_("Row #{0}: Accepted Warehouse and Supplier Warehouse cannot be same").format(product.idx)
				)

			if product.get("from_warehouse") and self.get("is_subcontracted"):
				frappe.throw(
					_(
						"Row #{0}: Cannot select Supplier Warehouse while suppling raw materials to subcontractor"
					).format(product.idx)
				)

	def set_supplier_address(self):
		address_dict = {
			"supplier_address": "address_display",
			"shipping_address": "shipping_address_display",
			"billing_address": "billing_address_display",
		}

		for address_field, address_display_field in address_dict.products():
			if self.get(address_field):
				self.set(address_display_field, get_address_display(self.get(address_field)))

	def set_total_in_words(self):
		from frappe.utils import money_in_words

		if self.meta.get_field("base_in_words"):
			if self.meta.get_field("base_rounded_total") and not self.is_rounded_total_disabled():
				amount = abs(self.base_rounded_total)
			else:
				amount = abs(self.base_grand_total)
			self.base_in_words = money_in_words(amount, self.company_currency)

		if self.meta.get_field("in_words"):
			if self.meta.get_field("rounded_total") and not self.is_rounded_total_disabled():
				amount = abs(self.rounded_total)
			else:
				amount = abs(self.grand_total)

			self.in_words = money_in_words(amount, self.currency)

	# update valuation rate
	def update_valuation_rate(self, reset_outgoing_rate=True):
		"""
		product_tax_amount is the total tax amount applied on that product
		stored for valuation

		TODO: rename product_tax_amount to valuation_tax_amount
		"""
		stock_and_asset_products = []
		stock_and_asset_products = self.get_stock_products() + self.get_asset_products()

		stock_and_asset_products_qty, stock_and_asset_products_amount = 0, 0
		last_product_idx = 1
		for d in self.get("products"):
			if d.product_code and d.product_code in stock_and_asset_products:
				stock_and_asset_products_qty += flt(d.qty)
				stock_and_asset_products_amount += flt(d.base_net_amount)
				last_product_idx = d.idx

		total_valuation_amount = sum(
			flt(d.base_tax_amount_after_discount_amount)
			for d in self.get("taxes")
			if d.category in ["Valuation", "Valuation and Total"]
		)

		valuation_amount_adjustment = total_valuation_amount
		for i, product in enumerate(self.get("products")):
			if product.product_code and product.qty and product.product_code in stock_and_asset_products:
				product_proportion = (
					flt(product.base_net_amount) / stock_and_asset_products_amount
					if stock_and_asset_products_amount
					else flt(product.qty) / stock_and_asset_products_qty
				)

				if i == (last_product_idx - 1):
					product.product_tax_amount = flt(
						valuation_amount_adjustment, self.precision("product_tax_amount", product)
					)
				else:
					product.product_tax_amount = flt(
						product_proportion * total_valuation_amount, self.precision("product_tax_amount", product)
					)
					valuation_amount_adjustment -= product.product_tax_amount

				self.round_floats_in(product)
				if flt(product.conversion_factor) == 0.0:
					product.conversion_factor = (
						get_conversion_factor(product.product_code, product.uom).get("conversion_factor") or 1.0
					)

				qty_in_stock_uom = flt(product.qty * product.conversion_factor)
				if self.get("is_old_subcontracting_flow"):
					product.rm_supp_cost = self.get_supplied_products_cost(product.name, reset_outgoing_rate)
					product.valuation_rate = (
						product.base_net_amount
						+ product.product_tax_amount
						+ product.rm_supp_cost
						+ flt(product.landed_cost_voucher_amount)
					) / qty_in_stock_uom
				else:
					product.valuation_rate = (
						product.base_net_amount
						+ product.product_tax_amount
						+ flt(product.landed_cost_voucher_amount)
						+ flt(product.get("rate_difference_with_purchase_invoice"))
					) / qty_in_stock_uom
			else:
				product.valuation_rate = 0.0

	def set_incoming_rate(self):
		if self.doctype not in ("Purchase Receipt", "Purchase Invoice", "Purchase Order"):
			return

		if not self.is_internal_transfer():
			return

		ref_doctype_map = {
			"Purchase Order": "Sales Order Product",
			"Purchase Receipt": "Delivery Note Product",
			"Purchase Invoice": "Sales Invoice Product",
		}

		ref_doctype = ref_doctype_map.get(self.doctype)
		products = self.get("products")
		for d in products:
			if not cint(self.get("is_return")):
				# Get outgoing rate based on original product cost based on valuation method

				if not d.get(frappe.scrub(ref_doctype)):
					posting_time = self.get("posting_time")
					if not posting_time and self.doctype == "Purchase Order":
						posting_time = nowtime()

					outgoing_rate = get_incoming_rate(
						{
							"product_code": d.product_code,
							"warehouse": d.get("from_warehouse"),
							"posting_date": self.get("posting_date") or self.get("transation_date"),
							"posting_time": posting_time,
							"qty": -1 * flt(d.get("stock_qty")),
							"serial_no": d.get("serial_no"),
							"batch_no": d.get("batch_no"),
							"company": self.company,
							"voucher_type": self.doctype,
							"voucher_no": self.name,
							"allow_zero_valuation": d.get("allow_zero_valuation"),
						},
						raise_error_if_no_rate=False,
					)

					rate = flt(outgoing_rate * (d.conversion_factor or 1), d.precision("rate"))
				else:
					field = "incoming_rate" if self.get("is_internal_supplier") else "rate"
					rate = flt(
						frappe.db.get_value(ref_doctype, d.get(frappe.scrub(ref_doctype)), field)
						* (d.conversion_factor or 1),
						d.precision("rate"),
					)

				if self.is_internal_transfer():
					if self.doctype == "Purchase Receipt" or self.get("update_stock"):
						if rate != d.rate:
							d.rate = rate
							frappe.msgprint(
								_(
									"Row {0}: Product rate has been updated as per valuation rate since its an internal stock transfer"
								).format(d.idx),
								alert=1,
							)
						d.discount_percentage = 0.0
						d.discount_amount = 0.0
						d.margin_rate_or_amount = 0.0

	def validate_for_subcontracting(self):
		if self.is_subcontracted and self.get("is_old_subcontracting_flow"):
			if self.doctype in ["Purchase Receipt", "Purchase Invoice"] and not self.supplier_warehouse:
				frappe.throw(_("Supplier Warehouse mandatory for sub-contracted {0}").format(self.doctype))

			for product in self.get("products"):
				if product in self.sub_contracted_products and not product.bom:
					frappe.throw(_("Please select BOM in BOM field for Product {0}").format(product.product_code))
			if self.doctype != "Purchase Order":
				return
			for row in self.get("supplied_products"):
				if not row.reserve_warehouse:
					msg = f"Reserved Warehouse is mandatory for the Product {frappe.bold(row.rm_product_code)} in Raw Materials supplied"
					frappe.throw(_(msg))
		else:
			for product in self.get("products"):
				if product.get("bom"):
					product.bom = None

	def set_qty_as_per_stock_uom(self):
		for d in self.get("products"):
			if d.meta.get_field("stock_qty"):
				# Check if product code is present
				# Conversion factor should not be mandatory for non productized products
				if not d.conversion_factor and d.product_code:
					frappe.throw(_("Row {0}: Conversion Factor is mandatory").format(d.idx))
				d.stock_qty = flt(d.qty) * flt(d.conversion_factor)

				if self.doctype == "Purchase Receipt" and d.meta.get_field("received_stock_qty"):
					# Set Received Qty in Stock UOM
					d.received_stock_qty = flt(d.received_qty) * flt(
						d.conversion_factor, d.precision("conversion_factor")
					)

	def validate_purchase_return(self):
		for d in self.get("products"):
			if self.is_return and flt(d.rejected_qty) != 0:
				frappe.throw(_("Row #{0}: Rejected Qty can not be entered in Purchase Return").format(d.idx))

			# validate rate with ref PR

	def validate_rejected_warehouse(self):
		for product in self.get("products"):
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

	# validate accepted and rejected qty
	def validate_accepted_rejected_qty(self):
		for d in self.get("products"):
			self.validate_negative_quantity(d, ["received_qty", "qty", "rejected_qty"])

			if not flt(d.received_qty) and (flt(d.qty) or flt(d.rejected_qty)):
				d.received_qty = flt(d.qty) + flt(d.rejected_qty)

			# Check Received Qty = Accepted Qty + Rejected Qty
			val = flt(d.qty) + flt(d.rejected_qty)
			if flt(val, d.precision("received_qty")) != flt(d.received_qty, d.precision("received_qty")):
				message = _(
					"Row #{0}: Received Qty must be equal to Accepted + Rejected Qty for Product {1}"
				).format(d.idx, d.product_code)
				frappe.throw(msg=message, title=_("Mismatch"), exc=QtyMismatchError)

	def validate_negative_quantity(self, product_row, field_list):
		if self.is_return:
			return

		product_row = product_row.as_dict()
		for fieldname in field_list:
			if flt(product_row[fieldname]) < 0:
				frappe.throw(
					_("Row #{0}: {1} can not be negative for product {2}").format(
						product_row["idx"],
						frappe.get_meta(product_row.doctype).get_label(fieldname),
						product_row["product_code"],
					)
				)

	def check_for_on_hold_or_closed_status(self, ref_doctype, ref_fieldname):
		for d in self.get("products"):
			if d.get(ref_fieldname):
				status = frappe.db.get_value(ref_doctype, d.get(ref_fieldname), "status")
				if status in ("Closed", "On Hold"):
					frappe.throw(_("{0} {1} is {2}").format(ref_doctype, d.get(ref_fieldname), status))

	def update_stock_ledger(self, allow_negative_stock=False, via_landed_cost_voucher=False):
		self.update_ordered_and_reserved_qty()

		sl_entries = []
		stock_products = self.get_stock_products()

		for d in self.get("products"):
			if d.product_code not in stock_products:
				continue

			if d.warehouse:
				pr_qty = flt(flt(d.qty) * flt(d.conversion_factor), d.precision("stock_qty"))

				if pr_qty:

					if d.from_warehouse and (
						(not cint(self.is_return) and self.docstatus == 1)
						or (cint(self.is_return) and self.docstatus == 2)
					):
						from_warehouse_sle = self.get_sl_entries(
							d,
							{
								"actual_qty": -1 * pr_qty,
								"warehouse": d.from_warehouse,
								"outgoing_rate": d.rate,
								"recalculate_rate": 1,
								"dependant_sle_voucher_detail_no": d.name,
							},
						)

						sl_entries.append(from_warehouse_sle)

					sle = self.get_sl_entries(
						d, {"actual_qty": flt(pr_qty), "serial_no": cstr(d.serial_no).strip()}
					)

					if self.is_return:
						outgoing_rate = get_rate_for_return(
							self.doctype, self.name, d.product_code, self.return_against, product_row=d
						)

						sle.update({"outgoing_rate": outgoing_rate, "recalculate_rate": 1})
						if d.from_warehouse:
							sle.dependant_sle_voucher_detail_no = d.name
					else:
						val_rate_db_precision = 6 if cint(self.precision("valuation_rate", d)) <= 6 else 9
						incoming_rate = flt(d.valuation_rate, val_rate_db_precision)
						sle.update(
							{
								"incoming_rate": incoming_rate,
								"recalculate_rate": 1
								if (self.is_subcontracted and (d.bom or d.fg_product)) or d.from_warehouse
								else 0,
							}
						)
					sl_entries.append(sle)

					if d.from_warehouse and (
						(not cint(self.is_return) and self.docstatus == 2)
						or (cint(self.is_return) and self.docstatus == 1)
					):
						from_warehouse_sle = self.get_sl_entries(
							d, {"actual_qty": -1 * pr_qty, "warehouse": d.from_warehouse, "recalculate_rate": 1}
						)

						sl_entries.append(from_warehouse_sle)

			if flt(d.rejected_qty) != 0:
				sl_entries.append(
					self.get_sl_entries(
						d,
						{
							"warehouse": d.rejected_warehouse,
							"actual_qty": flt(flt(d.rejected_qty) * flt(d.conversion_factor), d.precision("stock_qty")),
							"serial_no": cstr(d.rejected_serial_no).strip(),
							"incoming_rate": 0.0,
						},
					)
				)

		if self.get("is_old_subcontracting_flow"):
			self.make_sl_entries_for_supplier_warehouse(sl_entries)
		self.make_sl_entries(
			sl_entries,
			allow_negative_stock=allow_negative_stock,
			via_landed_cost_voucher=via_landed_cost_voucher,
		)

	def update_ordered_and_reserved_qty(self):
		po_map = {}
		for d in self.get("products"):
			if self.doctype == "Purchase Receipt" and d.purchase_order:
				po_map.setdefault(d.purchase_order, []).append(d.purchase_order_product)

			elif self.doctype == "Purchase Invoice" and d.purchase_order and d.po_detail:
				po_map.setdefault(d.purchase_order, []).append(d.po_detail)

		for po, po_product_rows in po_map.products():
			if po and po_product_rows:
				po_obj = frappe.get_doc("Purchase Order", po)

				if po_obj.status in ["Closed", "Cancelled"]:
					frappe.throw(
						_("{0} {1} is cancelled or closed").format(_("Purchase Order"), po),
						frappe.InvalidStatusError,
					)

				po_obj.update_ordered_qty(po_product_rows)
				if self.get("is_old_subcontracting_flow"):
					po_obj.update_reserved_qty_for_subcontract()

	def on_submit(self):
		if self.get("is_return"):
			return

		if self.doctype in ["Purchase Receipt", "Purchase Invoice"]:
			field = "purchase_invoice" if self.doctype == "Purchase Invoice" else "purchase_receipt"

			self.process_fixed_asset()
			self.update_fixed_asset(field)

		if self.doctype in ["Purchase Order", "Purchase Receipt"] and not frappe.db.get_single_value(
			"Buying Settings", "disable_last_purchase_rate"
		):
			update_last_purchase_rate(self, is_submit=1)

	def on_cancel(self):
		super(BuyingController, self).on_cancel()

		if self.get("is_return"):
			return

		if self.doctype in ["Purchase Order", "Purchase Receipt"] and not frappe.db.get_single_value(
			"Buying Settings", "disable_last_purchase_rate"
		):
			update_last_purchase_rate(self, is_submit=0)

		if self.doctype in ["Purchase Receipt", "Purchase Invoice"]:
			field = "purchase_invoice" if self.doctype == "Purchase Invoice" else "purchase_receipt"

			self.delete_linked_asset()
			self.update_fixed_asset(field, delete_asset=True)

	def validate_budget(self):
		if self.docstatus == 1:
			for data in self.get("products"):
				args = data.as_dict()
				args.update(
					{
						"doctype": self.doctype,
						"company": self.company,
						"posting_date": (
							self.schedule_date if self.doctype == "Material Request" else self.transaction_date
						),
					}
				)

				validate_expense_against_budget(args)

	def process_fixed_asset(self):
		if self.doctype == "Purchase Invoice" and not self.update_stock:
			return

		asset_products = self.get_asset_products()
		if asset_products:
			self.auto_make_assets(asset_products)

	def auto_make_assets(self, asset_products):
		products_data = get_asset_product_details(asset_products)
		messages = []

		for d in self.products:
			if d.is_fixed_asset:
				product_data = products_data.get(d.product_code)

				if product_data.get("auto_create_assets"):
					# If asset has to be auto created
					# Check for asset naming series
					if product_data.get("asset_naming_series"):
						created_assets = []
						if product_data.get("is_grouped_asset"):
							asset = self.make_asset(d, is_grouped_asset=True)
							created_assets.append(asset)
						else:
							for qty in range(cint(d.qty)):
								asset = self.make_asset(d)
								created_assets.append(asset)

						if len(created_assets) > 5:
							# dont show asset form links if more than 5 assets are created
							messages.append(
								_("{} Assets created for {}").format(len(created_assets), frappe.bold(d.product_code))
							)
						else:
							assets_link = list(map(lambda d: frappe.utils.get_link_to_form("Asset", d), created_assets))
							assets_link = frappe.bold(",".join(assets_link))

							is_plural = "s" if len(created_assets) != 1 else ""
							messages.append(
								_("Asset{} {assets_link} created for {}").format(
									is_plural, frappe.bold(d.product_code), assets_link=assets_link
								)
							)
					else:
						frappe.throw(
							_("Row {}: Asset Naming Series is mandatory for the auto creation for product {}").format(
								d.idx, frappe.bold(d.product_code)
							)
						)
				else:
					messages.append(
						_("Assets not created for {0}. You will have to create asset manually.").format(
							frappe.bold(d.product_code)
						)
					)

		for message in messages:
			frappe.msgprint(message, title="Success", indicator="green")

	def make_asset(self, row, is_grouped_asset=False):
		if not row.asset_location:
			frappe.throw(_("Row {0}: Enter location for the asset product {1}").format(row.idx, row.product_code))

		product_data = frappe.db.get_value(
			"Product", row.product_code, ["asset_naming_series", "asset_category"], as_dict=1
		)

		if is_grouped_asset:
			purchase_amount = flt(row.base_amount + row.product_tax_amount)
		else:
			purchase_amount = flt(row.base_rate + row.product_tax_amount)

		asset = frappe.get_doc(
			{
				"doctype": "Asset",
				"product_code": row.product_code,
				"asset_name": row.product_name,
				"naming_series": product_data.get("asset_naming_series") or "AST",
				"asset_category": product_data.get("asset_category"),
				"location": row.asset_location,
				"company": self.company,
				"supplier": self.supplier,
				"purchase_date": self.posting_date,
				"calculate_depreciation": 1,
				"purchase_receipt_amount": purchase_amount,
				"gross_purchase_amount": purchase_amount,
				"asset_quantity": row.qty if is_grouped_asset else 0,
				"purchase_receipt": self.name if self.doctype == "Purchase Receipt" else None,
				"purchase_invoice": self.name if self.doctype == "Purchase Invoice" else None,
				"cost_center": row.cost_center,
			}
		)

		asset.flags.ignore_validate = True
		asset.flags.ignore_mandatory = True
		asset.set_missing_values()
		asset.insert()

		return asset.name

	def update_fixed_asset(self, field, delete_asset=False):
		for d in self.get("products"):
			if d.is_fixed_asset:
				is_auto_create_enabled = frappe.db.get_value("Product", d.product_code, "auto_create_assets")
				assets = frappe.db.get_all("Asset", filters={field: self.name, "product_code": d.product_code})

				for asset in assets:
					asset = frappe.get_doc("Asset", asset.name)
					if delete_asset and is_auto_create_enabled:
						# need to delete movements to delete assets otherwise throws link exists error
						movements = frappe.db.sql(
							"""SELECT asm.name
							FROM `tabAsset Movement` asm, `tabAsset Movement Product` asm_product
							WHERE asm_product.parent=asm.name and asm_product.asset=%s""",
							asset.name,
							as_dict=1,
						)
						for movement in movements:
							frappe.delete_doc("Asset Movement", movement.name, force=1)
						frappe.delete_doc("Asset", asset.name, force=1)
						continue

					if self.docstatus in [0, 1] and not asset.get(field):
						asset.set(field, self.name)
						asset.purchase_date = self.posting_date
						asset.supplier = self.supplier
					elif self.docstatus == 2:
						if asset.docstatus == 2:
							continue
						if asset.docstatus == 0:
							asset.set(field, None)
							asset.supplier = None
						if asset.docstatus == 1 and delete_asset:
							frappe.throw(
								_(
									"Cannot cancel this document as it is linked with submitted asset {0}. Please cancel it to continue."
								).format(frappe.utils.get_link_to_form("Asset", asset.name))
							)

					asset.flags.ignore_validate_update_after_submit = True
					asset.flags.ignore_mandatory = True
					if asset.docstatus == 0:
						asset.flags.ignore_validate = True

					asset.save()

	def delete_linked_asset(self):
		if self.doctype == "Purchase Invoice" and not self.get("update_stock"):
			return

		frappe.db.sql("delete from `tabAsset Movement` where reference_name=%s", self.name)

	def validate_schedule_date(self):
		if not self.get("products"):
			return

		if any(d.schedule_date for d in self.get("products")):
			# Select earliest schedule_date.
			self.schedule_date = min(
				d.schedule_date for d in self.get("products") if d.schedule_date is not None
			)

		if self.schedule_date:
			for d in self.get("products"):
				if not d.schedule_date:
					d.schedule_date = self.schedule_date

				if (
					d.schedule_date
					and self.transaction_date
					and getdate(d.schedule_date) < getdate(self.transaction_date)
				):
					frappe.throw(_("Row #{0}: Reqd by Date cannot be before Transaction Date").format(d.idx))
		else:
			frappe.throw(_("Please enter Reqd by Date"))

	def validate_products(self):
		# validate products to see if they have is_purchase_product or is_subcontracted_product enabled
		if self.doctype == "Material Request":
			return

		if self.get("is_old_subcontracting_flow"):
			validate_product_type(self, "is_sub_contracted_product", "subcontracted")
		else:
			validate_product_type(self, "is_purchase_product", "purchase")


def get_asset_product_details(asset_products):
	asset_products_data = {}
	for d in frappe.get_all(
		"Product",
		fields=["name", "auto_create_assets", "asset_naming_series", "is_grouped_asset"],
		filters={"name": ("in", asset_products)},
	):
		asset_products_data.setdefault(d.name, d)

	return asset_products_data


def validate_product_type(doc, fieldname, message):
	# iterate through products and check if they are valid sales or purchase products
	products = [d.product_code for d in doc.products if d.product_code]

	# No validation check inase of creating transaction using 'Opening Invoice Creation Tool'
	if not products:
		return

	product_list = ", ".join(["%s" % frappe.db.escape(d) for d in products])

	invalid_products = [
		d[0]
		for d in frappe.db.sql(
			"""
		select product_code from tabProduct where name in ({0}) and {1}=0
		""".format(
				product_list, fieldname
			),
			as_list=True,
		)
	]

	if invalid_products:
		products = ", ".join([d for d in invalid_products])

		if len(invalid_products) > 1:
			error_message = _(
				"Following products {0} are not marked as {1} product. You can enable them as {1} product from its Product master"
			).format(products, message)
		else:
			error_message = _(
				"Following product {0} is not marked as {1} product. You can enable them as {1} product from its Product master"
			).format(products, message)

		frappe.throw(error_message)
