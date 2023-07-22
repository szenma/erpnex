# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json

import frappe
from frappe import _, scrub
from frappe.model.document import Document
from frappe.utils import cint, flt, round_based_on_smallest_currency_fraction

import erpnext
from erpnext.accounts.doctype.journal_entry.journal_entry import get_exchange_rate
from erpnext.accounts.doctype.pricing_rule.utils import get_applied_pricing_rules
from erpnext.controllers.accounts_controller import (
	validate_conversion_rate,
	validate_inclusive_tax,
	validate_taxes_and_charges,
)
from erpnext.stock.get_product_details import _get_product_tax_template


class calculate_taxes_and_totals(object):
	def __init__(self, doc: Document):
		self.doc = doc
		frappe.flags.round_off_applicable_accounts = []

		self._products = self.filter_rows() if self.doc.doctype == "Quotation" else self.doc.get("products")

		get_round_off_applicable_accounts(self.doc.company, frappe.flags.round_off_applicable_accounts)
		self.calculate()

	def filter_rows(self):
		"""Exclude rows, that do not fulfill the filter criteria, from totals computation."""
		products = list(filter(lambda product: not product.get("is_alternative"), self.doc.get("products")))
		return products

	def calculate(self):
		if not len(self._products):
			return

		self.discount_amount_applied = False
		self._calculate()

		if self.doc.meta.get_field("discount_amount"):
			self.set_discount_amount()
			self.apply_discount_amount()

		# Update grand total as per cash and non trade discount
		if self.doc.apply_discount_on == "Grand Total" and self.doc.get("is_cash_or_non_trade_discount"):
			self.doc.grand_total -= self.doc.discount_amount
			self.doc.base_grand_total -= self.doc.base_discount_amount
			self.set_rounded_total()

		self.calculate_shipping_charges()

		if self.doc.doctype in ["Sales Invoice", "Purchase Invoice"]:
			self.calculate_total_advance()

		if self.doc.meta.get_field("other_charges_calculation"):
			self.set_product_wise_tax_breakup()

	def _calculate(self):
		self.validate_conversion_rate()
		self.calculate_product_values()
		self.validate_product_tax_template()
		self.initialize_taxes()
		self.determine_exclusive_rate()
		self.calculate_net_total()
		self.calculate_tax_withholding_net_total()
		self.calculate_taxes()
		self.manipulate_grand_total_for_inclusive_tax()
		self.calculate_totals()
		self._cleanup()
		self.calculate_total_net_weight()

	def calculate_tax_withholding_net_total(self):
		if hasattr(self.doc, "tax_withholding_net_total"):
			sum_net_amount = 0
			sum_base_net_amount = 0
			for product in self._products:
				if hasattr(product, "apply_tds") and product.apply_tds:
					sum_net_amount += product.net_amount
					sum_base_net_amount += product.base_net_amount

			self.doc.tax_withholding_net_total = sum_net_amount
			self.doc.base_tax_withholding_net_total = sum_base_net_amount

	def validate_product_tax_template(self):
		for product in self._products:
			if product.product_code and product.get("product_tax_template"):
				product_doc = frappe.get_cached_doc("Product", product.product_code)
				args = {
					"net_rate": product.net_rate or product.rate,
					"tax_category": self.doc.get("tax_category"),
					"posting_date": self.doc.get("posting_date"),
					"bill_date": self.doc.get("bill_date"),
					"transaction_date": self.doc.get("transaction_date"),
					"company": self.doc.get("company"),
				}

				product_group = product_doc.product_group
				product_group_taxes = []

				while product_group:
					product_group_doc = frappe.get_cached_doc("Product Group", product_group)
					product_group_taxes += product_group_doc.taxes or []
					product_group = product_group_doc.parent_product_group

				product_taxes = product_doc.taxes or []

				if not product_group_taxes and (not product_taxes):
					# No validation if no taxes in product or product group
					continue

				taxes = _get_product_tax_template(args, product_taxes + product_group_taxes, for_validate=True)

				if taxes:
					if product.product_tax_template not in taxes:
						product.product_tax_template = taxes[0]
						frappe.msgprint(
							_("Row {0}: Product Tax template updated as per validity and rate applied").format(
								product.idx, frappe.bold(product.product_code)
							)
						)

	def validate_conversion_rate(self):
		# validate conversion rate
		company_currency = erpnext.get_company_currency(self.doc.company)
		if not self.doc.currency or self.doc.currency == company_currency:
			self.doc.currency = company_currency
			self.doc.conversion_rate = 1.0
		else:
			validate_conversion_rate(
				self.doc.currency,
				self.doc.conversion_rate,
				self.doc.meta.get_label("conversion_rate"),
				self.doc.company,
			)

		self.doc.conversion_rate = flt(self.doc.conversion_rate)

	def calculate_product_values(self):
		if self.doc.get("is_consolidated"):
			return

		if not self.discount_amount_applied:
			for product in self._products:
				self.doc.round_floats_in(product)

				if product.discount_percentage == 100:
					product.rate = 0.0
				elif product.price_list_rate:
					if not product.rate or (product.pricing_rules and product.discount_percentage > 0):
						product.rate = flt(
							product.price_list_rate * (1.0 - (product.discount_percentage / 100.0)), product.precision("rate")
						)

						product.discount_amount = product.price_list_rate * (product.discount_percentage / 100.0)

					elif product.discount_amount and product.pricing_rules:
						product.rate = product.price_list_rate - product.discount_amount

				if product.doctype in [
					"Quotation Product",
					"Sales Order Product",
					"Delivery Note Product",
					"Sales Invoice Product",
					"POS Invoice Product",
					"Purchase Invoice Product",
					"Purchase Order Product",
					"Purchase Receipt Product",
				]:
					product.rate_with_margin, product.base_rate_with_margin = self.calculate_margin(product)
					if flt(product.rate_with_margin) > 0:
						product.rate = flt(
							product.rate_with_margin * (1.0 - (product.discount_percentage / 100.0)), product.precision("rate")
						)

						if product.discount_amount and not product.discount_percentage:
							product.rate = product.rate_with_margin - product.discount_amount
						else:
							product.discount_amount = product.rate_with_margin - product.rate

					elif flt(product.price_list_rate) > 0:
						product.discount_amount = product.price_list_rate - product.rate
				elif flt(product.price_list_rate) > 0 and not product.discount_amount:
					product.discount_amount = product.price_list_rate - product.rate

				product.net_rate = product.rate

				if not product.qty and self.doc.get("is_return"):
					product.amount = flt(-1 * product.rate, product.precision("amount"))
				elif not product.qty and self.doc.get("is_debit_note"):
					product.amount = flt(product.rate, product.precision("amount"))
				else:
					product.amount = flt(product.rate * product.qty, product.precision("amount"))

				product.net_amount = product.amount

				self._set_in_company_currency(
					product, ["price_list_rate", "rate", "net_rate", "amount", "net_amount"]
				)

				product.product_tax_amount = 0.0

	def _set_in_company_currency(self, doc, fields):
		"""set values in base currency"""
		for f in fields:
			val = flt(
				flt(doc.get(f), doc.precision(f)) * self.doc.conversion_rate, doc.precision("base_" + f)
			)
			doc.set("base_" + f, val)

	def initialize_taxes(self):
		for tax in self.doc.get("taxes"):
			if not self.discount_amount_applied:
				validate_taxes_and_charges(tax)
				validate_inclusive_tax(tax, self.doc)

			if not (self.doc.get("is_consolidated") or tax.get("dont_recompute_tax")):
				tax.product_wise_tax_detail = {}

			tax_fields = [
				"total",
				"tax_amount_after_discount_amount",
				"tax_amount_for_current_product",
				"grand_total_for_current_product",
				"tax_fraction_for_current_product",
				"grand_total_fraction_for_current_product",
			]

			if tax.charge_type != "Actual" and not (
				self.discount_amount_applied and self.doc.apply_discount_on == "Grand Total"
			):
				tax_fields.append("tax_amount")

			for fieldname in tax_fields:
				tax.set(fieldname, 0.0)

			self.doc.round_floats_in(tax)

	def determine_exclusive_rate(self):
		if not any(cint(tax.included_in_print_rate) for tax in self.doc.get("taxes")):
			return

		for product in self._products:
			product_tax_map = self._load_product_tax_rate(product.product_tax_rate)
			cumulated_tax_fraction = 0
			total_inclusive_tax_amount_per_qty = 0
			for i, tax in enumerate(self.doc.get("taxes")):
				(
					tax.tax_fraction_for_current_product,
					inclusive_tax_amount_per_qty,
				) = self.get_current_tax_fraction(tax, product_tax_map)

				if i == 0:
					tax.grand_total_fraction_for_current_product = 1 + tax.tax_fraction_for_current_product
				else:
					tax.grand_total_fraction_for_current_product = (
						self.doc.get("taxes")[i - 1].grand_total_fraction_for_current_product
						+ tax.tax_fraction_for_current_product
					)

				cumulated_tax_fraction += tax.tax_fraction_for_current_product
				total_inclusive_tax_amount_per_qty += inclusive_tax_amount_per_qty * flt(product.qty)

			if (
				not self.discount_amount_applied
				and product.qty
				and (cumulated_tax_fraction or total_inclusive_tax_amount_per_qty)
			):
				amount = flt(product.amount) - total_inclusive_tax_amount_per_qty

				product.net_amount = flt(amount / (1 + cumulated_tax_fraction))
				product.net_rate = flt(product.net_amount / product.qty, product.precision("net_rate"))
				product.discount_percentage = flt(product.discount_percentage, product.precision("discount_percentage"))

				self._set_in_company_currency(product, ["net_rate", "net_amount"])

	def _load_product_tax_rate(self, product_tax_rate):
		return json.loads(product_tax_rate) if product_tax_rate else {}

	def get_current_tax_fraction(self, tax, product_tax_map):
		"""
		Get tax fraction for calculating tax exclusive amount
		from tax inclusive amount
		"""
		current_tax_fraction = 0
		inclusive_tax_amount_per_qty = 0

		if cint(tax.included_in_print_rate):
			tax_rate = self._get_tax_rate(tax, product_tax_map)

			if tax.charge_type == "On Net Total":
				current_tax_fraction = tax_rate / 100.0

			elif tax.charge_type == "On Previous Row Amount":
				current_tax_fraction = (tax_rate / 100.0) * self.doc.get("taxes")[
					cint(tax.row_id) - 1
				].tax_fraction_for_current_product

			elif tax.charge_type == "On Previous Row Total":
				current_tax_fraction = (tax_rate / 100.0) * self.doc.get("taxes")[
					cint(tax.row_id) - 1
				].grand_total_fraction_for_current_product

			elif tax.charge_type == "On Product Quantity":
				inclusive_tax_amount_per_qty = flt(tax_rate)

		if getattr(tax, "add_deduct_tax", None) and tax.add_deduct_tax == "Deduct":
			current_tax_fraction *= -1.0
			inclusive_tax_amount_per_qty *= -1.0

		return current_tax_fraction, inclusive_tax_amount_per_qty

	def _get_tax_rate(self, tax, product_tax_map):
		if tax.account_head in product_tax_map:
			return flt(product_tax_map.get(tax.account_head), self.doc.precision("rate", tax))
		else:
			return tax.rate

	def calculate_net_total(self):
		self.doc.total_qty = (
			self.doc.total
		) = self.doc.base_total = self.doc.net_total = self.doc.base_net_total = 0.0

		for product in self._products:
			self.doc.total += product.amount
			self.doc.total_qty += product.qty
			self.doc.base_total += product.base_amount
			self.doc.net_total += product.net_amount
			self.doc.base_net_total += product.base_net_amount

		self.doc.round_floats_in(self.doc, ["total", "base_total", "net_total", "base_net_total"])

	def calculate_shipping_charges(self):

		# Do not apply shipping rule for POS
		if self.doc.get("is_pos"):
			return

		if hasattr(self.doc, "shipping_rule") and self.doc.shipping_rule:
			shipping_rule = frappe.get_doc("Shipping Rule", self.doc.shipping_rule)
			shipping_rule.apply(self.doc)

			self._calculate()

	def calculate_taxes(self):
		rounding_adjustment_computed = self.doc.get("is_consolidated") and self.doc.get(
			"rounding_adjustment"
		)
		if not rounding_adjustment_computed:
			self.doc.rounding_adjustment = 0

		# maintain actual tax rate based on idx
		actual_tax_dict = dict(
			[
				[tax.idx, flt(tax.tax_amount, tax.precision("tax_amount"))]
				for tax in self.doc.get("taxes")
				if tax.charge_type == "Actual"
			]
		)

		for n, product in enumerate(self._products):
			product_tax_map = self._load_product_tax_rate(product.product_tax_rate)
			for i, tax in enumerate(self.doc.get("taxes")):
				# tax_amount represents the amount of tax for the current step
				current_tax_amount = self.get_current_tax_amount(product, tax, product_tax_map)

				# Adjust divisional loss to the last product
				if tax.charge_type == "Actual":
					actual_tax_dict[tax.idx] -= current_tax_amount
					if n == len(self._products) - 1:
						current_tax_amount += actual_tax_dict[tax.idx]

				# accumulate tax amount into tax.tax_amount
				if tax.charge_type != "Actual" and not (
					self.discount_amount_applied and self.doc.apply_discount_on == "Grand Total"
				):
					tax.tax_amount += current_tax_amount

				# store tax_amount for current product as it will be used for
				# charge type = 'On Previous Row Amount'
				tax.tax_amount_for_current_product = current_tax_amount

				# set tax after discount
				tax.tax_amount_after_discount_amount += current_tax_amount

				current_tax_amount = self.get_tax_amount_if_for_valuation_or_deduction(current_tax_amount, tax)

				# note: grand_total_for_current_product contains the contribution of
				# product's amount, previously applied tax and the current tax on that product
				if i == 0:
					tax.grand_total_for_current_product = flt(product.net_amount + current_tax_amount)
				else:
					tax.grand_total_for_current_product = flt(
						self.doc.get("taxes")[i - 1].grand_total_for_current_product + current_tax_amount
					)

				# set precision in the last product iteration
				if n == len(self._products) - 1:
					self.round_off_totals(tax)
					self._set_in_company_currency(tax, ["tax_amount", "tax_amount_after_discount_amount"])

					self.round_off_base_values(tax)
					self.set_cumulative_total(i, tax)

					self._set_in_company_currency(tax, ["total"])

					# adjust Discount Amount loss in last tax iteration
					if (
						i == (len(self.doc.get("taxes")) - 1)
						and self.discount_amount_applied
						and self.doc.discount_amount
						and self.doc.apply_discount_on == "Grand Total"
						and not rounding_adjustment_computed
					):
						self.doc.rounding_adjustment = flt(
							self.doc.grand_total - flt(self.doc.discount_amount) - tax.total,
							self.doc.precision("rounding_adjustment"),
						)

	def get_tax_amount_if_for_valuation_or_deduction(self, tax_amount, tax):
		# if just for valuation, do not add the tax amount in total
		# if tax/charges is for deduction, multiply by -1
		if getattr(tax, "category", None):
			tax_amount = 0.0 if (tax.category == "Valuation") else tax_amount
			if self.doc.doctype in [
				"Purchase Order",
				"Purchase Invoice",
				"Purchase Receipt",
				"Supplier Quotation",
			]:
				tax_amount *= -1.0 if (tax.add_deduct_tax == "Deduct") else 1.0
		return tax_amount

	def set_cumulative_total(self, row_idx, tax):
		tax_amount = tax.tax_amount_after_discount_amount
		tax_amount = self.get_tax_amount_if_for_valuation_or_deduction(tax_amount, tax)

		if row_idx == 0:
			tax.total = flt(self.doc.net_total + tax_amount, tax.precision("total"))
		else:
			tax.total = flt(self.doc.get("taxes")[row_idx - 1].total + tax_amount, tax.precision("total"))

	def get_current_tax_amount(self, product, tax, product_tax_map):
		tax_rate = self._get_tax_rate(tax, product_tax_map)
		current_tax_amount = 0.0

		if tax.charge_type == "Actual":
			# distribute the tax amount proportionally to each product row
			actual = flt(tax.tax_amount, tax.precision("tax_amount"))
			current_tax_amount = (
				product.net_amount * actual / self.doc.net_total if self.doc.net_total else 0.0
			)

		elif tax.charge_type == "On Net Total":
			current_tax_amount = (tax_rate / 100.0) * product.net_amount
		elif tax.charge_type == "On Previous Row Amount":
			current_tax_amount = (tax_rate / 100.0) * self.doc.get("taxes")[
				cint(tax.row_id) - 1
			].tax_amount_for_current_product
		elif tax.charge_type == "On Previous Row Total":
			current_tax_amount = (tax_rate / 100.0) * self.doc.get("taxes")[
				cint(tax.row_id) - 1
			].grand_total_for_current_product
		elif tax.charge_type == "On Product Quantity":
			current_tax_amount = tax_rate * product.qty

		if not (self.doc.get("is_consolidated") or tax.get("dont_recompute_tax")):
			self.set_product_wise_tax(product, tax, tax_rate, current_tax_amount)

		return current_tax_amount

	def set_product_wise_tax(self, product, tax, tax_rate, current_tax_amount):
		# store tax breakup for each product
		key = product.product_code or product.product_name
		product_wise_tax_amount = current_tax_amount * self.doc.conversion_rate
		if tax.product_wise_tax_detail.get(key):
			product_wise_tax_amount += tax.product_wise_tax_detail[key][1]

		tax.product_wise_tax_detail[key] = [tax_rate, flt(product_wise_tax_amount)]

	def round_off_totals(self, tax):
		if tax.account_head in frappe.flags.round_off_applicable_accounts:
			tax.tax_amount = round(tax.tax_amount, 0)
			tax.tax_amount_after_discount_amount = round(tax.tax_amount_after_discount_amount, 0)

		tax.tax_amount = flt(tax.tax_amount, tax.precision("tax_amount"))
		tax.tax_amount_after_discount_amount = flt(
			tax.tax_amount_after_discount_amount, tax.precision("tax_amount")
		)

	def round_off_base_values(self, tax):
		# Round off to nearest integer based on regional settings
		if tax.account_head in frappe.flags.round_off_applicable_accounts:
			tax.base_tax_amount = round(tax.base_tax_amount, 0)
			tax.base_tax_amount_after_discount_amount = round(tax.base_tax_amount_after_discount_amount, 0)

	def manipulate_grand_total_for_inclusive_tax(self):
		# if fully inclusive taxes and diff
		if self.doc.get("taxes") and any(cint(t.included_in_print_rate) for t in self.doc.get("taxes")):
			last_tax = self.doc.get("taxes")[-1]
			non_inclusive_tax_amount = sum(
				flt(d.tax_amount_after_discount_amount)
				for d in self.doc.get("taxes")
				if not d.included_in_print_rate
			)

			diff = (
				self.doc.total + non_inclusive_tax_amount - flt(last_tax.total, last_tax.precision("total"))
			)

			# If discount amount applied, deduct the discount amount
			# because self.doc.total is always without discount, but last_tax.total is after discount
			if self.discount_amount_applied and self.doc.discount_amount:
				diff -= flt(self.doc.discount_amount)

			diff = flt(diff, self.doc.precision("rounding_adjustment"))

			if diff and abs(diff) <= (5.0 / 10 ** last_tax.precision("tax_amount")):
				self.doc.rounding_adjustment = diff

	def calculate_totals(self):
		if self.doc.get("taxes"):
			self.doc.grand_total = flt(self.doc.get("taxes")[-1].total) + flt(self.doc.rounding_adjustment)
		else:
			self.doc.grand_total = flt(self.doc.net_total)

		if self.doc.get("taxes"):
			self.doc.total_taxes_and_charges = flt(
				self.doc.grand_total - self.doc.net_total - flt(self.doc.rounding_adjustment),
				self.doc.precision("total_taxes_and_charges"),
			)
		else:
			self.doc.total_taxes_and_charges = 0.0

		self._set_in_company_currency(self.doc, ["total_taxes_and_charges", "rounding_adjustment"])

		if self.doc.doctype in [
			"Quotation",
			"Sales Order",
			"Delivery Note",
			"Sales Invoice",
			"POS Invoice",
		]:
			self.doc.base_grand_total = (
				flt(self.doc.grand_total * self.doc.conversion_rate, self.doc.precision("base_grand_total"))
				if self.doc.total_taxes_and_charges
				else self.doc.base_net_total
			)
		else:
			self.doc.taxes_and_charges_added = self.doc.taxes_and_charges_deducted = 0.0
			for tax in self.doc.get("taxes"):
				if tax.category in ["Valuation and Total", "Total"]:
					if tax.add_deduct_tax == "Add":
						self.doc.taxes_and_charges_added += flt(tax.tax_amount_after_discount_amount)
					else:
						self.doc.taxes_and_charges_deducted += flt(tax.tax_amount_after_discount_amount)

			self.doc.round_floats_in(self.doc, ["taxes_and_charges_added", "taxes_and_charges_deducted"])

			self.doc.base_grand_total = (
				flt(self.doc.grand_total * self.doc.conversion_rate)
				if (self.doc.taxes_and_charges_added or self.doc.taxes_and_charges_deducted)
				else self.doc.base_net_total
			)

			self._set_in_company_currency(
				self.doc, ["taxes_and_charges_added", "taxes_and_charges_deducted"]
			)

		self.doc.round_floats_in(self.doc, ["grand_total", "base_grand_total"])

		self.set_rounded_total()

	def calculate_total_net_weight(self):
		if self.doc.meta.get_field("total_net_weight"):
			self.doc.total_net_weight = 0.0
			for d in self._products:
				if d.total_weight:
					self.doc.total_net_weight += d.total_weight

	def set_rounded_total(self):
		if self.doc.get("is_consolidated") and self.doc.get("rounding_adjustment"):
			return

		if self.doc.meta.get_field("rounded_total"):
			if self.doc.is_rounded_total_disabled():
				self.doc.rounded_total = self.doc.base_rounded_total = 0
				return

			self.doc.rounded_total = round_based_on_smallest_currency_fraction(
				self.doc.grand_total, self.doc.currency, self.doc.precision("rounded_total")
			)

			# if print_in_rate is set, we would have already calculated rounding adjustment
			self.doc.rounding_adjustment += flt(
				self.doc.rounded_total - self.doc.grand_total, self.doc.precision("rounding_adjustment")
			)

			self._set_in_company_currency(self.doc, ["rounding_adjustment", "rounded_total"])

	def _cleanup(self):
		if not self.doc.get("is_consolidated"):
			for tax in self.doc.get("taxes"):
				if not tax.get("dont_recompute_tax"):
					tax.product_wise_tax_detail = json.dumps(tax.product_wise_tax_detail, separators=(",", ":"))

	def set_discount_amount(self):
		if self.doc.additional_discount_percentage:
			self.doc.discount_amount = flt(
				flt(self.doc.get(scrub(self.doc.apply_discount_on)))
				* self.doc.additional_discount_percentage
				/ 100,
				self.doc.precision("discount_amount"),
			)

	def apply_discount_amount(self):
		if self.doc.discount_amount:
			if not self.doc.apply_discount_on:
				frappe.throw(_("Please select Apply Discount On"))

			self.doc.base_discount_amount = flt(
				self.doc.discount_amount * self.doc.conversion_rate, self.doc.precision("base_discount_amount")
			)

			if self.doc.apply_discount_on == "Grand Total" and self.doc.get(
				"is_cash_or_non_trade_discount"
			):
				self.discount_amount_applied = True
				return

			total_for_discount_amount = self.get_total_for_discount_amount()
			taxes = self.doc.get("taxes")
			net_total = 0

			if total_for_discount_amount:
				# calculate product amount after Discount Amount
				for i, product in enumerate(self._products):
					distributed_amount = (
						flt(self.doc.discount_amount) * product.net_amount / total_for_discount_amount
					)

					product.net_amount = flt(product.net_amount - distributed_amount, product.precision("net_amount"))
					net_total += product.net_amount

					# discount amount rounding loss adjustment if no taxes
					if (
						self.doc.apply_discount_on == "Net Total"
						or not taxes
						or total_for_discount_amount == self.doc.net_total
					) and i == len(self._products) - 1:
						discount_amount_loss = flt(
							self.doc.net_total - net_total - self.doc.discount_amount, self.doc.precision("net_total")
						)

						product.net_amount = flt(product.net_amount + discount_amount_loss, product.precision("net_amount"))

					product.net_rate = flt(product.net_amount / product.qty, product.precision("net_rate")) if product.qty else 0

					self._set_in_company_currency(product, ["net_rate", "net_amount"])

				self.discount_amount_applied = True
				self._calculate()
		else:
			self.doc.base_discount_amount = 0

	def get_total_for_discount_amount(self):
		if self.doc.apply_discount_on == "Net Total":
			return self.doc.net_total
		else:
			actual_taxes_dict = {}

			for tax in self.doc.get("taxes"):
				if tax.charge_type in ["Actual", "On Product Quantity"]:
					tax_amount = self.get_tax_amount_if_for_valuation_or_deduction(tax.tax_amount, tax)
					actual_taxes_dict.setdefault(tax.idx, tax_amount)
				elif tax.row_id in actual_taxes_dict:
					actual_tax_amount = flt(actual_taxes_dict.get(tax.row_id, 0)) * flt(tax.rate) / 100
					actual_taxes_dict.setdefault(tax.idx, actual_tax_amount)

			return flt(
				self.doc.grand_total - sum(actual_taxes_dict.values()), self.doc.precision("grand_total")
			)

	def calculate_total_advance(self):
		if not self.doc.docstatus.is_cancelled():
			total_allocated_amount = sum(
				flt(adv.allocated_amount, adv.precision("allocated_amount"))
				for adv in self.doc.get("advances")
			)

			self.doc.total_advance = flt(total_allocated_amount, self.doc.precision("total_advance"))

			grand_total = self.doc.rounded_total or self.doc.grand_total

			if self.doc.party_account_currency == self.doc.currency:
				invoice_total = flt(
					grand_total - flt(self.doc.write_off_amount), self.doc.precision("grand_total")
				)
			else:
				base_write_off_amount = flt(
					flt(self.doc.write_off_amount) * self.doc.conversion_rate,
					self.doc.precision("base_write_off_amount"),
				)
				invoice_total = (
					flt(grand_total * self.doc.conversion_rate, self.doc.precision("grand_total"))
					- base_write_off_amount
				)

			if invoice_total > 0 and self.doc.total_advance > invoice_total:
				frappe.throw(
					_("Advance amount cannot be greater than {0} {1}").format(
						self.doc.party_account_currency, invoice_total
					)
				)

			if self.doc.docstatus.is_draft():
				if self.doc.get("write_off_outstanding_amount_automatically"):
					self.doc.write_off_amount = 0

				self.calculate_outstanding_amount()
				self.calculate_write_off_amount()

	def is_internal_invoice(self):
		"""
		Checks if its an internal transfer invoice
		and decides if to calculate any out standing amount or not
		"""

		if self.doc.doctype in ("Sales Invoice", "Purchase Invoice") and self.doc.is_internal_transfer():
			return True

		return False

	def calculate_outstanding_amount(self):
		# NOTE:
		# write_off_amount is only for POS Invoice
		# total_advance is only for non POS Invoice
		if self.doc.doctype == "Sales Invoice":
			self.calculate_paid_amount()

		if (
			self.doc.is_return
			and self.doc.return_against
			and not self.doc.get("is_pos")
			or self.is_internal_invoice()
		):
			return

		self.doc.round_floats_in(self.doc, ["grand_total", "total_advance", "write_off_amount"])
		self._set_in_company_currency(self.doc, ["write_off_amount"])

		if self.doc.doctype in ["Sales Invoice", "Purchase Invoice"]:
			grand_total = self.doc.rounded_total or self.doc.grand_total
			base_grand_total = self.doc.base_rounded_total or self.doc.base_grand_total

			if self.doc.party_account_currency == self.doc.currency:
				total_amount_to_pay = flt(
					grand_total - self.doc.total_advance - flt(self.doc.write_off_amount),
					self.doc.precision("grand_total"),
				)
			else:
				total_amount_to_pay = flt(
					flt(base_grand_total, self.doc.precision("base_grand_total"))
					- self.doc.total_advance
					- flt(self.doc.base_write_off_amount),
					self.doc.precision("base_grand_total"),
				)

			self.doc.round_floats_in(self.doc, ["paid_amount"])
			change_amount = 0

			if self.doc.doctype == "Sales Invoice" and not self.doc.get("is_return"):
				self.calculate_change_amount()
				change_amount = (
					self.doc.change_amount
					if self.doc.party_account_currency == self.doc.currency
					else self.doc.base_change_amount
				)

			paid_amount = (
				self.doc.paid_amount
				if self.doc.party_account_currency == self.doc.currency
				else self.doc.base_paid_amount
			)

			self.doc.outstanding_amount = flt(
				total_amount_to_pay - flt(paid_amount) + flt(change_amount),
				self.doc.precision("outstanding_amount"),
			)

			if (
				self.doc.doctype == "Sales Invoice"
				and self.doc.get("is_pos")
				and self.doc.get("pos_profile")
				and self.doc.get("is_consolidated")
			):
				write_off_limit = flt(
					frappe.db.get_value("POS Profile", self.doc.pos_profile, "write_off_limit")
				)
				if write_off_limit and abs(self.doc.outstanding_amount) <= write_off_limit:
					self.doc.write_off_outstanding_amount_automatically = 1

			if (
				self.doc.doctype == "Sales Invoice"
				and self.doc.get("is_pos")
				and self.doc.get("is_return")
				and not self.doc.get("is_consolidated")
			):
				self.set_total_amount_to_default_mop(total_amount_to_pay)
				self.calculate_paid_amount()

	def calculate_paid_amount(self):

		paid_amount = base_paid_amount = 0.0

		if self.doc.is_pos:
			for payment in self.doc.get("payments"):
				payment.amount = flt(payment.amount)
				payment.base_amount = payment.amount * flt(self.doc.conversion_rate)
				paid_amount += payment.amount
				base_paid_amount += payment.base_amount
		elif not self.doc.is_return:
			self.doc.set("payments", [])

		if self.doc.redeem_loyalty_points and self.doc.loyalty_amount:
			base_paid_amount += self.doc.loyalty_amount
			paid_amount += self.doc.loyalty_amount / flt(self.doc.conversion_rate)

		self.doc.paid_amount = flt(paid_amount, self.doc.precision("paid_amount"))
		self.doc.base_paid_amount = flt(base_paid_amount, self.doc.precision("base_paid_amount"))

	def calculate_change_amount(self):
		self.doc.change_amount = 0.0
		self.doc.base_change_amount = 0.0
		grand_total = self.doc.rounded_total or self.doc.grand_total
		base_grand_total = self.doc.base_rounded_total or self.doc.base_grand_total

		if (
			self.doc.doctype == "Sales Invoice"
			and self.doc.paid_amount > grand_total
			and not self.doc.is_return
			and any(d.type == "Cash" for d in self.doc.payments)
		):
			self.doc.change_amount = flt(
				self.doc.paid_amount - grand_total, self.doc.precision("change_amount")
			)

			self.doc.base_change_amount = flt(
				self.doc.base_paid_amount - base_grand_total, self.doc.precision("base_change_amount")
			)

	def calculate_write_off_amount(self):
		if self.doc.get("write_off_outstanding_amount_automatically"):
			self.doc.write_off_amount = flt(
				self.doc.outstanding_amount, self.doc.precision("write_off_amount")
			)
			self.doc.base_write_off_amount = flt(
				self.doc.write_off_amount * self.doc.conversion_rate,
				self.doc.precision("base_write_off_amount"),
			)

			self.calculate_outstanding_amount()

	def calculate_margin(self, product):
		rate_with_margin = 0.0
		base_rate_with_margin = 0.0
		if product.price_list_rate:
			if product.pricing_rules and not self.doc.ignore_pricing_rule:
				has_margin = False
				for d in get_applied_pricing_rules(product.pricing_rules):
					pricing_rule = frappe.get_cached_doc("Pricing Rule", d)

					if pricing_rule.margin_rate_or_amount and (
						(
							pricing_rule.currency == self.doc.currency
							and pricing_rule.margin_type in ["Amount", "Percentage"]
						)
						or pricing_rule.margin_type == "Percentage"
					):
						product.margin_type = pricing_rule.margin_type
						product.margin_rate_or_amount = pricing_rule.margin_rate_or_amount
						has_margin = True

				if not has_margin:
					product.margin_type = None
					product.margin_rate_or_amount = 0.0

			if not product.pricing_rules and flt(product.rate) > flt(product.price_list_rate):
				product.margin_type = "Amount"
				product.margin_rate_or_amount = flt(
					product.rate - product.price_list_rate, product.precision("margin_rate_or_amount")
				)
				product.rate_with_margin = product.rate

			elif product.margin_type and product.margin_rate_or_amount:
				margin_value = (
					product.margin_rate_or_amount
					if product.margin_type == "Amount"
					else flt(product.price_list_rate) * flt(product.margin_rate_or_amount) / 100
				)
				rate_with_margin = flt(product.price_list_rate) + flt(margin_value)
				base_rate_with_margin = flt(rate_with_margin) * flt(self.doc.conversion_rate)

		return rate_with_margin, base_rate_with_margin

	def set_product_wise_tax_breakup(self):
		self.doc.other_charges_calculation = get_productised_tax_breakup_html(self.doc)

	def set_total_amount_to_default_mop(self, total_amount_to_pay):
		total_paid_amount = 0
		for payment in self.doc.get("payments"):
			total_paid_amount += (
				payment.amount if self.doc.party_account_currency == self.doc.currency else payment.base_amount
			)

		pending_amount = total_amount_to_pay - total_paid_amount

		if pending_amount > 0:
			default_mode_of_payment = frappe.db.get_value(
				"POS Payment Method",
				{"parent": self.doc.pos_profile, "default": 1},
				["mode_of_payment"],
				as_dict=1,
			)

			if default_mode_of_payment:
				self.doc.payments = []
				self.doc.append(
					"payments",
					{
						"mode_of_payment": default_mode_of_payment.mode_of_payment,
						"amount": pending_amount,
						"default": 1,
					},
				)


def get_productised_tax_breakup_html(doc):
	if not doc.taxes:
		return
	frappe.flags.company = doc.company

	# get headers
	tax_accounts = []
	for tax in doc.taxes:
		if getattr(tax, "category", None) and tax.category == "Valuation":
			continue
		if tax.description not in tax_accounts:
			tax_accounts.append(tax.description)

	headers = get_productised_tax_breakup_header(doc.doctype + " Product", tax_accounts)

	# get tax breakup data
	productised_tax, productised_taxable_amount = get_productised_tax_breakup_data(doc)

	get_rounded_tax_amount(productised_tax, doc.precision("tax_amount", "taxes"))

	update_productised_tax_data(doc)
	frappe.flags.company = None

	return frappe.render_template(
		"templates/includes/productised_tax_breakup.html",
		dict(
			headers=headers,
			productised_tax=productised_tax,
			productised_taxable_amount=productised_taxable_amount,
			tax_accounts=tax_accounts,
			doc=doc,
		),
	)


@frappe.whitelist()
def get_round_off_applicable_accounts(company, account_list):
	# required to set correct region
	frappe.flags.company = company
	account_list = get_regional_round_off_accounts(company, account_list)

	return account_list


@erpnext.allow_regional
def get_regional_round_off_accounts(company, account_list):
	pass


@erpnext.allow_regional
def update_productised_tax_data(doc):
	# Don't delete this method, used for localization
	pass


@erpnext.allow_regional
def get_productised_tax_breakup_header(product_doctype, tax_accounts):
	return [_("Product"), _("Taxable Amount")] + tax_accounts


@erpnext.allow_regional
def get_productised_tax_breakup_data(doc):
	productised_tax = get_productised_tax(doc.taxes)

	productised_taxable_amount = get_productised_taxable_amount(doc.products)

	return productised_tax, productised_taxable_amount


def get_productised_tax(taxes, with_tax_account=False):
	productised_tax = {}
	for tax in taxes:
		if getattr(tax, "category", None) and tax.category == "Valuation":
			continue

		product_tax_map = json.loads(tax.product_wise_tax_detail) if tax.product_wise_tax_detail else {}
		if product_tax_map:
			for product_code, tax_data in product_tax_map.products():
				productised_tax.setdefault(product_code, frappe._dict())

				tax_rate = 0.0
				tax_amount = 0.0

				if isinstance(tax_data, list):
					tax_rate = flt(tax_data[0])
					tax_amount = flt(tax_data[1])
				else:
					tax_rate = flt(tax_data)

				productised_tax[product_code][tax.description] = frappe._dict(
					dict(tax_rate=tax_rate, tax_amount=tax_amount)
				)

				if with_tax_account:
					productised_tax[product_code][tax.description].tax_account = tax.account_head

	return productised_tax


def get_productised_taxable_amount(products):
	productised_taxable_amount = frappe._dict()
	for product in products:
		product_code = product.product_code or product.product_name
		productised_taxable_amount.setdefault(product_code, 0)
		productised_taxable_amount[product_code] += product.net_amount

	return productised_taxable_amount


def get_rounded_tax_amount(productised_tax, precision):
	# Rounding based on tax_amount precision
	for taxes in productised_tax.values():
		for tax_account in taxes:
			taxes[tax_account]["tax_amount"] = flt(taxes[tax_account]["tax_amount"], precision)


class init_landed_taxes_and_totals(object):
	def __init__(self, doc):
		self.doc = doc
		self.tax_field = "taxes" if self.doc.doctype == "Landed Cost Voucher" else "additional_costs"
		self.set_account_currency()
		self.set_exchange_rate()
		self.set_amounts_in_company_currency()

	def set_account_currency(self):
		company_currency = erpnext.get_company_currency(self.doc.company)
		for d in self.doc.get(self.tax_field):
			if not d.account_currency:
				account_currency = frappe.get_cached_value("Account", d.expense_account, "account_currency")
				d.account_currency = account_currency or company_currency

	def set_exchange_rate(self):
		company_currency = erpnext.get_company_currency(self.doc.company)
		for d in self.doc.get(self.tax_field):
			if d.account_currency == company_currency:
				d.exchange_rate = 1
			elif not d.exchange_rate:
				d.exchange_rate = get_exchange_rate(
					self.doc.posting_date,
					account=d.expense_account,
					account_currency=d.account_currency,
					company=self.doc.company,
				)

			if not d.exchange_rate:
				frappe.throw(_("Row {0}: Exchange Rate is mandatory").format(d.idx))

	def set_amounts_in_company_currency(self):
		for d in self.doc.get(self.tax_field):
			d.amount = flt(d.amount, d.precision("amount"))
			d.base_amount = flt(d.amount * flt(d.exchange_rate), d.precision("base_amount"))
