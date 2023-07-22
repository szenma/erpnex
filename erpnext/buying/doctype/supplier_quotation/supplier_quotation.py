# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt, getdate, nowdate

from erpnext.buying.utils import validate_for_products
from erpnext.controllers.buying_controller import BuyingController

form_grid_templates = {"products": "templates/form_grid/product_grid.html"}


class SupplierQuotation(BuyingController):
	def validate(self):
		super(SupplierQuotation, self).validate()

		if not self.status:
			self.status = "Draft"

		from erpnext.controllers.status_updater import validate_status

		validate_status(self.status, ["Draft", "Submitted", "Stopped", "Cancelled"])

		validate_for_products(self)
		self.validate_with_previous_doc()
		self.validate_uom_is_integer("uom", "qty")
		self.validate_valid_till()

	def on_submit(self):
		self.db_set("status", "Submitted")
		self.update_rfq_supplier_status(1)

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		self.update_rfq_supplier_status(0)

	def on_trash(self):
		pass

	def validate_with_previous_doc(self):
		super(SupplierQuotation, self).validate_with_previous_doc(
			{
				"Material Request": {
					"ref_dn_field": "prevdoc_docname",
					"compare_fields": [["company", "="]],
				},
				"Material Request Product": {
					"ref_dn_field": "prevdoc_detail_docname",
					"compare_fields": [["product_code", "="], ["uom", "="]],
					"is_child_table": True,
				},
			}
		)

	def validate_valid_till(self):
		if self.valid_till and getdate(self.valid_till) < getdate(self.transaction_date):
			frappe.throw(_("Valid till Date cannot be before Transaction Date"))

	def update_rfq_supplier_status(self, include_me):
		rfq_list = set([])
		for product in self.products:
			if product.request_for_quotation:
				rfq_list.add(product.request_for_quotation)
		for rfq in rfq_list:
			doc = frappe.get_doc("Request for Quotation", rfq)
			doc_sup = frappe.get_all(
				"Request for Quotation Supplier",
				filters={"parent": doc.name, "supplier": self.supplier},
				fields=["name", "quote_status"],
			)

			doc_sup = doc_sup[0] if doc_sup else None
			if not doc_sup:
				frappe.throw(
					_("Supplier {0} not found in {1}").format(
						self.supplier,
						"<a href='desk/app/Form/Request for Quotation/{0}'> Request for Quotation {0} </a>".format(
							doc.name
						),
					)
				)

			quote_status = _("Received")
			for product in doc.products:
				sqi_count = frappe.db.sql(
					"""
					SELECT
						COUNT(sqi.name) as count
					FROM
						`tabSupplier Quotation Product` as sqi,
						`tabSupplier Quotation` as sq
					WHERE sq.supplier = %(supplier)s
						AND sqi.docstatus = 1
						AND sq.name != %(me)s
						AND sqi.request_for_quotation_product = %(rqi)s
						AND sqi.parent = sq.name""",
					{"supplier": self.supplier, "rqi": product.name, "me": self.name},
					as_dict=1,
				)[0]
				self_count = (
					sum(my_product.request_for_quotation_product == product.name for my_product in self.products)
					if include_me
					else 0
				)
				if (sqi_count.count + self_count) == 0:
					quote_status = _("Pending")

				frappe.db.set_value(
					"Request for Quotation Supplier", doc_sup.name, "quote_status", quote_status
				)


def get_list_context(context=None):
	from erpnext.controllers.website_list_for_contact import get_list_context

	list_context = get_list_context(context)
	list_context.update(
		{
			"show_sidebar": True,
			"show_search": True,
			"no_breadcrumbs": True,
			"title": _("Supplier Quotation"),
		}
	)

	return list_context


@frappe.whitelist()
def make_purchase_order(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.run_method("set_missing_values")
		target.run_method("get_schedule_dates")
		target.run_method("calculate_taxes_and_totals")

	def update_product(obj, target, source_parent):
		target.stock_qty = flt(obj.qty) * flt(obj.conversion_factor)

	doclist = get_mapped_doc(
		"Supplier Quotation",
		source_name,
		{
			"Supplier Quotation": {
				"doctype": "Purchase Order",
				"validation": {
					"docstatus": ["=", 1],
				},
			},
			"Supplier Quotation Product": {
				"doctype": "Purchase Order Product",
				"field_map": [
					["name", "supplier_quotation_product"],
					["parent", "supplier_quotation"],
					["material_request", "material_request"],
					["material_request_product", "material_request_product"],
					["sales_order", "sales_order"],
				],
				"postprocess": update_product,
			},
			"Purchase Taxes and Charges": {
				"doctype": "Purchase Taxes and Charges",
			},
		},
		target_doc,
		set_missing_values,
	)

	doclist.set_onload("ignore_price_list", True)
	return doclist


@frappe.whitelist()
def make_purchase_invoice(source_name, target_doc=None):
	doc = get_mapped_doc(
		"Supplier Quotation",
		source_name,
		{
			"Supplier Quotation": {
				"doctype": "Purchase Invoice",
				"validation": {
					"docstatus": ["=", 1],
				},
			},
			"Supplier Quotation Product": {"doctype": "Purchase Invoice Product"},
			"Purchase Taxes and Charges": {"doctype": "Purchase Taxes and Charges"},
		},
		target_doc,
	)

	return doc


@frappe.whitelist()
def make_quotation(source_name, target_doc=None):
	doclist = get_mapped_doc(
		"Supplier Quotation",
		source_name,
		{
			"Supplier Quotation": {
				"doctype": "Quotation",
				"field_map": {
					"name": "supplier_quotation",
				},
			},
			"Supplier Quotation Product": {
				"doctype": "Quotation Product",
				"condition": lambda doc: frappe.db.get_value("Product", doc.product_code, "is_sales_product") == 1,
				"add_if_empty": True,
			},
		},
		target_doc,
	)

	return doclist


def set_expired_status():
	frappe.db.sql(
		"""
		UPDATE
			`tabSupplier Quotation` SET `status` = 'Expired'
		WHERE
			`status` not in ('Cancelled', 'Stopped') AND `valid_till` < %s
		""",
		(nowdate()),
	)
