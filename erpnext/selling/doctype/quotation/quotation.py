# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt, getdate, nowdate

from erpnext.controllers.selling_controller import SellingController

form_grid_templates = {"products": "templates/form_grid/product_grid.html"}


class Quotation(SellingController):
	def set_indicator(self):
		if self.docstatus == 1:
			self.indicator_color = "blue"
			self.indicator_title = "Submitted"
		if self.valid_till and getdate(self.valid_till) < getdate(nowdate()):
			self.indicator_color = "gray"
			self.indicator_title = "Expired"

	def validate(self):
		super(Quotation, self).validate()
		self.set_status()
		self.validate_uom_is_integer("stock_uom", "qty")
		self.validate_valid_till()
		self.validate_shopping_cart_products()
		self.set_customer_name()
		if self.products:
			self.with_products = 1

		from erpnext.stock.doctype.packed_product.packed_product import make_packing_list

		make_packing_list(self)

	def before_submit(self):
		self.set_has_alternative_product()

	def validate_valid_till(self):
		if self.valid_till and getdate(self.valid_till) < getdate(self.transaction_date):
			frappe.throw(_("Valid till date cannot be before transaction date"))

	def validate_shopping_cart_products(self):
		if self.order_type != "Shopping Cart":
			return

		for product in self.products:
			has_web_product = frappe.db.exists("Website Product", {"product_code": product.product_code})

			# If variant is unpublished but template is published: valid
			template = frappe.get_cached_value("Product", product.product_code, "variant_of")
			if template and not has_web_product:
				has_web_product = frappe.db.exists("Website Product", {"product_code": template})

			if not has_web_product:
				frappe.throw(
					_("Row #{0}: Product {1} must have a Website Product for Shopping Cart Quotations").format(
						product.idx, frappe.bold(product.product_code)
					),
					title=_("Unpublished Product"),
				)

	def set_has_alternative_product(self):
		"""Mark 'Has Alternative Product' for rows."""
		if not any(row.is_alternative for row in self.get("products")):
			return

		products_with_alternatives = self.get_rows_with_alternatives()
		for row in self.get("products"):
			if not row.is_alternative and row.name in products_with_alternatives:
				row.has_alternative_product = 1

	def get_ordered_status(self):
		status = "Open"
		ordered_products = frappe._dict(
			frappe.db.get_all(
				"Sales Order Product",
				{"prevdoc_docname": self.name, "docstatus": 1},
				["product_code", "sum(qty)"],
				group_by="product_code",
				as_list=1,
			)
		)

		if not ordered_products:
			return status

		has_alternatives = any(row.is_alternative for row in self.get("products"))
		self._products = self.get_valid_products() if has_alternatives else self.get("products")

		if any(row.qty > ordered_products.get(row.product_code, 0.0) for row in self._products):
			status = "Partially Ordered"
		else:
			status = "Ordered"

		return status

	def get_valid_products(self):
		"""
		Filters out products in an alternatives set that were not ordered.
		"""

		def is_in_sales_order(row):
			in_sales_order = bool(
				frappe.db.exists(
					"Sales Order Product", {"quotation_product": row.name, "product_code": row.product_code, "docstatus": 1}
				)
			)
			return in_sales_order

		def can_map(row) -> bool:
			if row.is_alternative or row.has_alternative_product:
				return is_in_sales_order(row)

			return True

		return list(filter(can_map, self.get("products")))

	def is_fully_ordered(self):
		return self.get_ordered_status() == "Ordered"

	def is_partially_ordered(self):
		return self.get_ordered_status() == "Partially Ordered"

	def update_lead(self):
		if self.quotation_to == "Lead" and self.party_name:
			frappe.get_doc("Lead", self.party_name).set_status(update=True)

	def set_customer_name(self):
		if self.party_name and self.quotation_to == "Customer":
			self.customer_name = frappe.db.get_value("Customer", self.party_name, "customer_name")
		elif self.party_name and self.quotation_to == "Lead":
			lead_name, company_name = frappe.db.get_value(
				"Lead", self.party_name, ["lead_name", "company_name"]
			)
			self.customer_name = company_name or lead_name

	def update_opportunity(self, status):
		for opportunity in set(d.prevdoc_docname for d in self.get("products")):
			if opportunity:
				self.update_opportunity_status(status, opportunity)

		if self.opportunity:
			self.update_opportunity_status(status)

	def update_opportunity_status(self, status, opportunity=None):
		if not opportunity:
			opportunity = self.opportunity

		opp = frappe.get_doc("Opportunity", opportunity)
		opp.set_status(status=status, update=True)

	@frappe.whitelist()
	def declare_enquiry_lost(self, lost_reasons_list, competitors, detailed_reason=None):
		if not (self.is_fully_ordered() or self.is_partially_ordered()):
			get_lost_reasons = frappe.get_list("Quotation Lost Reason", fields=["name"])
			lost_reasons_lst = [reason.get("name") for reason in get_lost_reasons]
			self.db_set("status", "Lost")

			if detailed_reason:
				self.db_set("order_lost_reason", detailed_reason)

			for reason in lost_reasons_list:
				if reason.get("lost_reason") in lost_reasons_lst:
					self.append("lost_reasons", reason)
				else:
					frappe.throw(
						_("Invalid lost reason {0}, please create a new lost reason").format(
							frappe.bold(reason.get("lost_reason"))
						)
					)

			for competitor in competitors:
				self.append("competitors", competitor)

			self.update_opportunity("Lost")
			self.update_lead()
			self.save()

		else:
			frappe.throw(_("Cannot set as Lost as Sales Order is made."))

	def on_submit(self):
		# Check for Approving Authority
		frappe.get_doc("Authorization Control").validate_approving_authority(
			self.doctype, self.company, self.base_grand_total, self
		)

		# update enquiry status
		self.update_opportunity("Quotation")
		self.update_lead()

	def on_cancel(self):
		if self.lost_reasons:
			self.lost_reasons = []
		super(Quotation, self).on_cancel()

		# update enquiry status
		self.set_status(update=True)
		self.update_opportunity("Open")
		self.update_lead()

	def print_other_charges(self, docname):
		print_lst = []
		for d in self.get("taxes"):
			lst1 = []
			lst1.append(d.description)
			lst1.append(d.total)
			print_lst.append(lst1)
		return print_lst

	def on_recurring(self, reference_doc, auto_repeat_doc):
		self.valid_till = None

	def get_rows_with_alternatives(self):
		rows_with_alternatives = []
		table_length = len(self.get("products"))

		for idx, row in enumerate(self.get("products")):
			if row.is_alternative:
				continue

			if idx == (table_length - 1):
				break

			if self.get("products")[idx + 1].is_alternative:
				rows_with_alternatives.append(row.name)

		return rows_with_alternatives


def get_list_context(context=None):
	from erpnext.controllers.website_list_for_contact import get_list_context

	list_context = get_list_context(context)
	list_context.update(
		{
			"show_sidebar": True,
			"show_search": True,
			"no_breadcrumbs": True,
			"title": _("Quotations"),
		}
	)

	return list_context


@frappe.whitelist()
def make_sales_order(source_name: str, target_doc=None):
	if not frappe.db.get_singles_value(
		"Selling Settings", "allow_sales_order_creation_for_expired_quotation"
	):
		quotation = frappe.db.get_value(
			"Quotation", source_name, ["transaction_date", "valid_till"], as_dict=1
		)
		if quotation.valid_till and (
			quotation.valid_till < quotation.transaction_date or quotation.valid_till < getdate(nowdate())
		):
			frappe.throw(_("Validity period of this quotation has ended."))

	return _make_sales_order(source_name, target_doc)


def _make_sales_order(source_name, target_doc=None, ignore_permissions=False):
	customer = _make_customer(source_name, ignore_permissions)
	ordered_products = frappe._dict(
		frappe.db.get_all(
			"Sales Order Product",
			{"prevdoc_docname": source_name, "docstatus": 1},
			["product_code", "sum(qty)"],
			group_by="product_code",
			as_list=1,
		)
	)

	selected_rows = [x.get("name") for x in frappe.flags.get("args", {}).get("selected_products", [])]

	def set_missing_values(source, target):
		if customer:
			target.customer = customer.name
			target.customer_name = customer.customer_name
		if source.referral_sales_partner:
			target.sales_partner = source.referral_sales_partner
			target.commission_rate = frappe.get_value(
				"Sales Partner", source.referral_sales_partner, "commission_rate"
			)

		# sales team
		for d in customer.get("sales_team") or []:
			target.append(
				"sales_team",
				{
					"sales_person": d.sales_person,
					"allocated_percentage": d.allocated_percentage or None,
					"commission_rate": d.commission_rate,
				},
			)

		target.flags.ignore_permissions = ignore_permissions
		target.delivery_date = nowdate()
		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	def update_product(obj, target, source_parent):
		balance_qty = obj.qty - ordered_products.get(obj.product_code, 0.0)
		target.qty = balance_qty if balance_qty > 0 else 0
		target.stock_qty = flt(target.qty) * flt(obj.conversion_factor)
		target.delivery_date = nowdate()

		if obj.against_blanket_order:
			target.against_blanket_order = obj.against_blanket_order
			target.blanket_order = obj.blanket_order
			target.blanket_order_rate = obj.blanket_order_rate

	def can_map_row(product) -> bool:
		"""
		Row mapping from Quotation to Sales order:
		1. If no selections, map all non-alternative rows (that sum up to the grand total)
		2. If selections: Is Alternative Product/Has Alternative Product: Map if selected and adequate qty
		3. If selections: Simple row: Map if adequate qty
		"""
		has_qty = product.qty > 0

		if not selected_rows:
			return not product.is_alternative

		if selected_rows and (product.is_alternative or product.has_alternative_product):
			return (product.name in selected_rows) and has_qty

		# Simple row
		return has_qty

	doclist = get_mapped_doc(
		"Quotation",
		source_name,
		{
			"Quotation": {"doctype": "Sales Order", "validation": {"docstatus": ["=", 1]}},
			"Quotation Product": {
				"doctype": "Sales Order Product",
				"field_map": {"parent": "prevdoc_docname", "name": "quotation_product"},
				"postprocess": update_product,
				"condition": can_map_row,
			},
			"Sales Taxes and Charges": {"doctype": "Sales Taxes and Charges", "add_if_empty": True},
			"Sales Team": {"doctype": "Sales Team", "add_if_empty": True},
			"Payment Schedule": {"doctype": "Payment Schedule", "add_if_empty": True},
		},
		target_doc,
		set_missing_values,
		ignore_permissions=ignore_permissions,
	)

	# postprocess: fetch shipping address, set missing values
	doclist.set_onload("ignore_price_list", True)

	return doclist


def set_expired_status():
	# filter out submitted non expired quotations whose validity has been ended
	cond = "`tabQuotation`.docstatus = 1 and `tabQuotation`.status NOT IN ('Expired', 'Lost') and `tabQuotation`.valid_till < %s"
	# check if those QUO have SO against it
	so_against_quo = """
		SELECT
			so.name FROM `tabSales Order` so, `tabSales Order Product` so_product
		WHERE
			so_product.docstatus = 1 and so.docstatus = 1
			and so_product.parent = so.name
			and so_product.prevdoc_docname = `tabQuotation`.name"""

	# if not exists any SO, set status as Expired
	frappe.db.multisql(
		{
			"mariadb": """UPDATE `tabQuotation`  SET `tabQuotation`.status = 'Expired' WHERE {cond} and not exists({so_against_quo})""".format(
				cond=cond, so_against_quo=so_against_quo
			),
			"postgres": """UPDATE `tabQuotation` SET status = 'Expired' FROM `tabSales Order`, `tabSales Order Product` WHERE {cond} and not exists({so_against_quo})""".format(
				cond=cond, so_against_quo=so_against_quo
			),
		},
		(nowdate()),
	)


@frappe.whitelist()
def make_sales_invoice(source_name, target_doc=None):
	return _make_sales_invoice(source_name, target_doc)


def _make_sales_invoice(source_name, target_doc=None, ignore_permissions=False):
	customer = _make_customer(source_name, ignore_permissions)

	def set_missing_values(source, target):
		if customer:
			target.customer = customer.name
			target.customer_name = customer.customer_name

		target.flags.ignore_permissions = ignore_permissions
		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	def update_product(obj, target, source_parent):
		target.cost_center = None
		target.stock_qty = flt(obj.qty) * flt(obj.conversion_factor)

	doclist = get_mapped_doc(
		"Quotation",
		source_name,
		{
			"Quotation": {"doctype": "Sales Invoice", "validation": {"docstatus": ["=", 1]}},
			"Quotation Product": {
				"doctype": "Sales Invoice Product",
				"postprocess": update_product,
				"condition": lambda row: not row.is_alternative,
			},
			"Sales Taxes and Charges": {"doctype": "Sales Taxes and Charges", "add_if_empty": True},
			"Sales Team": {"doctype": "Sales Team", "add_if_empty": True},
		},
		target_doc,
		set_missing_values,
		ignore_permissions=ignore_permissions,
	)

	doclist.set_onload("ignore_price_list", True)

	return doclist


def _make_customer(source_name, ignore_permissions=False):
	quotation = frappe.db.get_value(
		"Quotation", source_name, ["order_type", "party_name", "customer_name"], as_dict=1
	)

	if quotation and quotation.get("party_name"):
		if not frappe.db.exists("Customer", quotation.get("party_name")):
			lead_name = quotation.get("party_name")
			customer_name = frappe.db.get_value(
				"Customer", {"lead_name": lead_name}, ["name", "customer_name"], as_dict=True
			)
			if not customer_name:
				from erpnext.crm.doctype.lead.lead import _make_customer

				customer_doclist = _make_customer(lead_name, ignore_permissions=ignore_permissions)
				customer = frappe.get_doc(customer_doclist)
				customer.flags.ignore_permissions = ignore_permissions
				if quotation.get("party_name") == "Shopping Cart":
					customer.customer_group = frappe.db.get_value(
						"E Commerce Settings", None, "default_customer_group"
					)

				try:
					customer.insert()
					return customer
				except frappe.NameError:
					if frappe.defaults.get_global_default("cust_master_name") == "Customer Name":
						customer.run_method("autoname")
						customer.name += "-" + lead_name
						customer.insert()
						return customer
					else:
						raise
				except frappe.MandatoryError as e:
					mandatory_fields = e.args[0].split(":")[1].split(",")
					mandatory_fields = [customer.meta.get_label(field.strip()) for field in mandatory_fields]

					frappe.local.message_log = []
					lead_link = frappe.utils.get_link_to_form("Lead", lead_name)
					message = (
						_("Could not auto create Customer due to the following missing mandatory field(s):") + "<br>"
					)
					message += "<br><ul><li>" + "</li><li>".join(mandatory_fields) + "</li></ul>"
					message += _("Please create Customer from Lead {0}.").format(lead_link)

					frappe.throw(message, title=_("Mandatory Missing"))
			else:
				return customer_name
		else:
			return frappe.get_doc("Customer", quotation.get("party_name"))
