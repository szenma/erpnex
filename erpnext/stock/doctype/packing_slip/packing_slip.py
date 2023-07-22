# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.controllers.status_updater import StatusUpdater


class PackingSlip(StatusUpdater):
	def __init__(self, *args, **kwargs) -> None:
		super(PackingSlip, self).__init__(*args, **kwargs)
		self.status_updater = [
			{
				"target_dt": "Delivery Note Product",
				"join_field": "dn_detail",
				"target_field": "packed_qty",
				"target_parent_dt": "Delivery Note",
				"target_ref_field": "qty",
				"source_dt": "Packing Slip Product",
				"source_field": "qty",
			},
			{
				"target_dt": "Packed Product",
				"join_field": "pi_detail",
				"target_field": "packed_qty",
				"target_parent_dt": "Delivery Note",
				"target_ref_field": "qty",
				"source_dt": "Packing Slip Product",
				"source_field": "qty",
			},
		]

	def validate(self) -> None:
		from erpnext.utilities.transaction_base import validate_uom_is_integer

		self.validate_delivery_note()
		self.validate_case_nos()
		self.validate_products()

		validate_uom_is_integer(self, "stock_uom", "qty")
		validate_uom_is_integer(self, "weight_uom", "net_weight")

		self.set_missing_values()
		self.calculate_net_total_pkg()

	def on_submit(self):
		self.update_prevdoc_status()

	def on_cancel(self):
		self.update_prevdoc_status()

	def validate_delivery_note(self):
		"""Raises an exception if the `Delivery Note` status is not Draft"""

		if cint(frappe.db.get_value("Delivery Note", self.delivery_note, "docstatus")) != 0:
			frappe.throw(
				_("A Packing Slip can only be created for Draft Delivery Note.").format(self.delivery_note)
			)

	def validate_case_nos(self):
		"""Validate if case nos overlap. If they do, recommend next case no."""

		if cint(self.from_case_no) <= 0:
			frappe.throw(
				_("The 'From Package No.' field must neither be empty nor it's value less than 1.")
			)
		elif not self.to_case_no:
			self.to_case_no = self.from_case_no
		elif cint(self.to_case_no) < cint(self.from_case_no):
			frappe.throw(_("'To Package No.' cannot be less than 'From Package No.'"))
		else:
			ps = frappe.qb.DocType("Packing Slip")
			res = (
				frappe.qb.from_(ps)
				.select(
					ps.name,
				)
				.where(
					(ps.delivery_note == self.delivery_note)
					& (ps.docstatus == 1)
					& (
						(ps.from_case_no.between(self.from_case_no, self.to_case_no))
						| (ps.to_case_no.between(self.from_case_no, self.to_case_no))
						| ((ps.from_case_no <= self.from_case_no) & (ps.to_case_no >= self.from_case_no))
					)
				)
			).run()

			if res:
				frappe.throw(
					_("""Package No(s) already in use. Try from Package No {0}""").format(
						self.get_recommended_case_no()
					)
				)

	def validate_products(self):
		for product in self.products:
			if product.qty <= 0:
				frappe.throw(_("Row {0}: Qty must be greater than 0.").format(product.idx))

			if not product.dn_detail and not product.pi_detail:
				frappe.throw(
					_("Row {0}: Either Delivery Note Product or Packed Product reference is mandatory.").format(
						product.idx
					)
				)

			remaining_qty = frappe.db.get_value(
				"Delivery Note Product" if product.dn_detail else "Packed Product",
				{"name": product.dn_detail or product.pi_detail, "docstatus": 0},
				["sum(qty - packed_qty)"],
			)

			if remaining_qty is None:
				frappe.throw(
					_("Row {0}: Please provide a valid Delivery Note Product or Packed Product reference.").format(
						product.idx
					)
				)
			elif remaining_qty <= 0:
				frappe.throw(
					_("Row {0}: Packing Slip is already created for Product {1}.").format(
						product.idx, frappe.bold(product.product_code)
					)
				)
			elif product.qty > remaining_qty:
				frappe.throw(
					_("Row {0}: Qty cannot be greater than {1} for the Product {2}.").format(
						product.idx, frappe.bold(remaining_qty), frappe.bold(product.product_code)
					)
				)

	def set_missing_values(self):
		if not self.from_case_no:
			self.from_case_no = self.get_recommended_case_no()

		for product in self.products:
			stock_uom, weight_per_unit, weight_uom = frappe.db.get_value(
				"Product", product.product_code, ["stock_uom", "weight_per_unit", "weight_uom"]
			)

			product.stock_uom = stock_uom
			if weight_per_unit and not product.net_weight:
				product.net_weight = weight_per_unit
			if weight_uom and not product.weight_uom:
				product.weight_uom = weight_uom

	def get_recommended_case_no(self):
		"""Returns the next case no. for a new packing slip for a delivery note"""

		return (
			cint(
				frappe.db.get_value(
					"Packing Slip", {"delivery_note": self.delivery_note, "docstatus": 1}, ["max(to_case_no)"]
				)
			)
			+ 1
		)

	def calculate_net_total_pkg(self):
		self.net_weight_uom = self.products[0].weight_uom if self.products else None
		self.gross_weight_uom = self.net_weight_uom

		net_weight_pkg = 0
		for product in self.products:
			if product.weight_uom != self.net_weight_uom:
				frappe.throw(
					_(
						"Different UOM for products will lead to incorrect (Total) Net Weight value. Make sure that Net Weight of each product is in the same UOM."
					)
				)

			net_weight_pkg += flt(product.net_weight) * flt(product.qty)

		self.net_weight_pkg = round(net_weight_pkg, 2)

		if not flt(self.gross_weight_pkg):
			self.gross_weight_pkg = self.net_weight_pkg


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def product_details(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_match_cond

	return frappe.db.sql(
		"""select name, product_name, description from `tabProduct`
				where name in ( select product_code FROM `tabDelivery Note Product`
	 						where parent= %s)
	 			and %s like "%s" %s
	 			limit  %s offset %s """
		% ("%s", searchfield, "%s", get_match_cond(doctype), "%s", "%s"),
		((filters or {}).get("delivery_note"), "%%%s%%" % txt, page_len, start),
	)
