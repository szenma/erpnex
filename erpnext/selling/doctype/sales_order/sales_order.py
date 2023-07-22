# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json

import frappe
import frappe.utils
from frappe import _, qb
from frappe.contacts.doctype.address.address import get_company_address
from frappe.desk.notifications import clear_doctype_notifications
from frappe.model.mapper import get_mapped_doc
from frappe.model.utils import get_fetch_values
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, cint, cstr, flt, get_link_to_form, getdate, nowdate, strip_html

from erpnext.accounts.doctype.sales_invoice.sales_invoice import (
	unlink_inter_company_doc,
	update_linked_doc,
	validate_inter_company_party,
)
from erpnext.accounts.party import get_party_account
from erpnext.controllers.selling_controller import SellingController
from erpnext.manufacturing.doctype.blanket_order.blanket_order import (
	validate_against_blanket_order,
)
from erpnext.manufacturing.doctype.production_plan.production_plan import (
	get_products_for_material_requests,
)
from erpnext.selling.doctype.customer.customer import check_credit_limit
from erpnext.setup.doctype.product_group.product_group import get_product_group_defaults
from erpnext.stock.doctype.product.product import get_product_defaults
from erpnext.stock.get_product_details import get_default_bom, get_price_list_rate
from erpnext.stock.stock_balance import get_reserved_qty, update_bin_qty

form_grid_templates = {"products": "templates/form_grid/product_grid.html"}


class WarehouseRequired(frappe.ValidationError):
	pass


class SalesOrder(SellingController):
	def __init__(self, *args, **kwargs):
		super(SalesOrder, self).__init__(*args, **kwargs)

	def validate(self):
		super(SalesOrder, self).validate()
		self.validate_delivery_date()
		self.validate_proj_cust()
		self.validate_po()
		self.validate_uom_is_integer("stock_uom", "stock_qty")
		self.validate_uom_is_integer("uom", "qty")
		self.validate_for_products()
		self.validate_warehouse()
		self.validate_drop_ship()
		self.validate_serial_no_based_delivery()
		validate_against_blanket_order(self)
		validate_inter_company_party(
			self.doctype, self.customer, self.company, self.inter_company_order_reference
		)

		if self.coupon_code:
			from erpnext.accounts.doctype.pricing_rule.utils import validate_coupon_code

			validate_coupon_code(self.coupon_code)

		from erpnext.stock.doctype.packed_product.packed_product import make_packing_list

		make_packing_list(self)

		self.validate_with_previous_doc()
		self.set_status()

		if not self.billing_status:
			self.billing_status = "Not Billed"
		if not self.delivery_status:
			self.delivery_status = "Not Delivered"

		self.reset_default_field_value("set_warehouse", "products", "warehouse")

	def validate_po(self):
		# validate p.o date v/s delivery date
		if self.po_date and not self.skip_delivery_note:
			for d in self.get("products"):
				if d.delivery_date and getdate(self.po_date) > getdate(d.delivery_date):
					frappe.throw(
						_("Row #{0}: Expected Delivery Date cannot be before Purchase Order Date").format(d.idx)
					)

		if self.po_no and self.customer and not self.skip_delivery_note:
			so = frappe.db.sql(
				"select name from `tabSales Order` \
				where ifnull(po_no, '') = %s and name != %s and docstatus < 2\
				and customer = %s",
				(self.po_no, self.name, self.customer),
			)
			if (
				so
				and so[0][0]
				and not cint(
					frappe.db.get_single_value("Selling Settings", "allow_against_multiple_purchase_orders")
				)
			):
				frappe.msgprint(
					_("Warning: Sales Order {0} already exists against Customer's Purchase Order {1}").format(
						so[0][0], self.po_no
					)
				)

	def validate_for_products(self):
		for d in self.get("products"):

			# used for production plan
			d.transaction_date = self.transaction_date

			tot_avail_qty = frappe.db.sql(
				"select projected_qty from `tabBin` \
				where product_code = %s and warehouse = %s",
				(d.product_code, d.warehouse),
			)
			d.projected_qty = tot_avail_qty and flt(tot_avail_qty[0][0]) or 0

	def product_bundle_has_stock_product(self, product_bundle):
		"""Returns true if product bundle has stock product"""
		ret = len(
			frappe.db.sql(
				"""select i.name from tabProduct i, `tabProduct Bundle Product` pbi
			where pbi.parent = %s and pbi.product_code = i.name and i.is_stock_product = 1""",
				product_bundle,
			)
		)
		return ret

	def validate_sales_mntc_quotation(self):
		for d in self.get("products"):
			if d.prevdoc_docname:
				res = frappe.db.sql(
					"select name from `tabQuotation` where name=%s and order_type = %s",
					(d.prevdoc_docname, self.order_type),
				)
				if not res:
					frappe.msgprint(_("Quotation {0} not of type {1}").format(d.prevdoc_docname, self.order_type))

	def validate_delivery_date(self):
		if self.order_type == "Sales" and not self.skip_delivery_note:
			delivery_date_list = [d.delivery_date for d in self.get("products") if d.delivery_date]
			max_delivery_date = max(delivery_date_list) if delivery_date_list else None
			if (max_delivery_date and not self.delivery_date) or (
				max_delivery_date and getdate(self.delivery_date) != getdate(max_delivery_date)
			):
				self.delivery_date = max_delivery_date
			if self.delivery_date:
				for d in self.get("products"):
					if not d.delivery_date:
						d.delivery_date = self.delivery_date
					if getdate(self.transaction_date) > getdate(d.delivery_date):
						frappe.msgprint(
							_("Expected Delivery Date should be after Sales Order Date"),
							indicator="orange",
							title=_("Invalid Delivery Date"),
							raise_exception=True,
						)
			else:
				frappe.throw(_("Please enter Delivery Date"))

		self.validate_sales_mntc_quotation()

	def validate_proj_cust(self):
		if self.project and self.customer_name:
			res = frappe.db.sql(
				"""select name from `tabProject` where name = %s
				and (customer = %s or ifnull(customer,'')='')""",
				(self.project, self.customer),
			)
			if not res:
				frappe.throw(
					_("Customer {0} does not belong to project {1}").format(self.customer, self.project)
				)

	def validate_warehouse(self):
		super(SalesOrder, self).validate_warehouse()

		for d in self.get("products"):
			if (
				(
					frappe.get_cached_value("Product", d.product_code, "is_stock_product") == 1
					or (self.has_product_bundle(d.product_code) and self.product_bundle_has_stock_product(d.product_code))
				)
				and not d.warehouse
				and not cint(d.delivered_by_supplier)
			):
				frappe.throw(
					_("Delivery warehouse required for stock product {0}").format(d.product_code), WarehouseRequired
				)

	def validate_with_previous_doc(self):
		super(SalesOrder, self).validate_with_previous_doc(
			{"Quotation": {"ref_dn_field": "prevdoc_docname", "compare_fields": [["company", "="]]}}
		)

		if cint(frappe.db.get_single_value("Selling Settings", "maintain_same_sales_rate")):
			self.validate_rate_with_reference_doc([["Quotation", "prevdoc_docname", "quotation_product"]])

	def update_enquiry_status(self, prevdoc, flag):
		enq = frappe.db.sql(
			"select t2.prevdoc_docname from `tabQuotation` t1, `tabQuotation Product` t2 where t2.parent = t1.name and t1.name=%s",
			prevdoc,
		)
		if enq:
			frappe.db.sql("update `tabOpportunity` set status = %s where name=%s", (flag, enq[0][0]))

	def update_prevdoc_status(self, flag=None):
		for quotation in set(d.prevdoc_docname for d in self.get("products")):
			if quotation:
				doc = frappe.get_doc("Quotation", quotation)
				if doc.docstatus.is_cancelled():
					frappe.throw(_("Quotation {0} is cancelled").format(quotation))

				doc.set_status(update=True)
				doc.update_opportunity("Converted" if flag == "submit" else "Quotation")

	def validate_drop_ship(self):
		for d in self.get("products"):
			if d.delivered_by_supplier and not d.supplier:
				frappe.throw(_("Row #{0}: Set Supplier for product {1}").format(d.idx, d.product_code))

	def on_submit(self):
		self.check_credit_limit()
		self.update_reserved_qty()

		frappe.get_doc("Authorization Control").validate_approving_authority(
			self.doctype, self.company, self.base_grand_total, self
		)
		self.update_project()
		self.update_prevdoc_status("submit")

		self.update_blanket_order()

		update_linked_doc(self.doctype, self.name, self.inter_company_order_reference)
		if self.coupon_code:
			from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count

			update_coupon_code_count(self.coupon_code, "used")

	def on_cancel(self):
		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry", "Payment Ledger Entry")
		super(SalesOrder, self).on_cancel()

		# Cannot cancel closed SO
		if self.status == "Closed":
			frappe.throw(_("Closed order cannot be cancelled. Unclose to cancel."))

		self.check_nextdoc_docstatus()
		self.update_reserved_qty()
		self.update_project()
		self.update_prevdoc_status("cancel")

		self.db_set("status", "Cancelled")

		self.update_blanket_order()

		unlink_inter_company_doc(self.doctype, self.name, self.inter_company_order_reference)
		if self.coupon_code:
			from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count

			update_coupon_code_count(self.coupon_code, "cancelled")

	def update_project(self):
		if (
			frappe.db.get_single_value("Selling Settings", "sales_update_frequency") != "Each Transaction"
		):
			return

		if self.project:
			project = frappe.get_doc("Project", self.project)
			project.update_sales_amount()
			project.db_update()

	def check_credit_limit(self):
		# if bypass credit limit check is set to true (1) at sales order level,
		# then we need not to check credit limit and vise versa
		if not cint(
			frappe.db.get_value(
				"Customer Credit Limit",
				{"parent": self.customer, "parenttype": "Customer", "company": self.company},
				"bypass_credit_limit_check",
			)
		):
			check_credit_limit(self.customer, self.company)

	def check_nextdoc_docstatus(self):
		linked_invoices = frappe.db.sql_list(
			"""select distinct t1.name
			from `tabSales Invoice` t1,`tabSales Invoice Product` t2
			where t1.name = t2.parent and t2.sales_order = %s and t1.docstatus = 0""",
			self.name,
		)

		if linked_invoices:
			linked_invoices = [get_link_to_form("Sales Invoice", si) for si in linked_invoices]
			frappe.throw(
				_("Sales Invoice {0} must be deleted before cancelling this Sales Order").format(
					", ".join(linked_invoices)
				)
			)

	def check_modified_date(self):
		mod_db = frappe.db.get_value("Sales Order", self.name, "modified")
		date_diff = frappe.db.sql("select TIMEDIFF('%s', '%s')" % (mod_db, cstr(self.modified)))
		if date_diff and date_diff[0][0]:
			frappe.throw(_("{0} {1} has been modified. Please refresh.").format(self.doctype, self.name))

	def update_status(self, status):
		self.check_modified_date()
		self.set_status(update=True, status=status)
		self.update_reserved_qty()
		self.notify_update()
		clear_doctype_notifications(self)

	def update_reserved_qty(self, so_product_rows=None):
		"""update requested qty (before ordered_qty is updated)"""
		product_wh_list = []

		def _valid_for_reserve(product_code, warehouse):
			if (
				product_code
				and warehouse
				and [product_code, warehouse] not in product_wh_list
				and frappe.get_cached_value("Product", product_code, "is_stock_product")
			):
				product_wh_list.append([product_code, warehouse])

		for d in self.get("products"):
			if (not so_product_rows or d.name in so_product_rows) and not d.delivered_by_supplier:
				if self.has_product_bundle(d.product_code):
					for p in self.get("packed_products"):
						if p.parent_detail_docname == d.name and p.parent_product == d.product_code:
							_valid_for_reserve(p.product_code, p.warehouse)
				else:
					_valid_for_reserve(d.product_code, d.warehouse)

		for product_code, warehouse in product_wh_list:
			update_bin_qty(product_code, warehouse, {"reserved_qty": get_reserved_qty(product_code, warehouse)})

	def on_update(self):
		pass

	def before_update_after_submit(self):
		self.validate_po()
		self.validate_drop_ship()
		self.validate_supplier_after_submit()
		self.validate_delivery_date()

	def validate_supplier_after_submit(self):
		"""Check that supplier is the same after submit if PO is already made"""
		exc_list = []

		for product in self.products:
			if product.supplier:
				supplier = frappe.db.get_value(
					"Sales Order Product", {"parent": self.name, "product_code": product.product_code}, "supplier"
				)
				if product.ordered_qty > 0.0 and product.supplier != supplier:
					exc_list.append(
						_("Row #{0}: Not allowed to change Supplier as Purchase Order already exists").format(
							product.idx
						)
					)

		if exc_list:
			frappe.throw("\n".join(exc_list))

	def update_delivery_status(self):
		"""Update delivery status from Purchase Order for drop shipping"""
		tot_qty, delivered_qty = 0.0, 0.0

		for product in self.products:
			if product.delivered_by_supplier:
				product_delivered_qty = frappe.db.sql(
					"""select sum(qty)
					from `tabPurchase Order Product` poi, `tabPurchase Order` po
					where poi.sales_order_product = %s
						and poi.product_code = %s
						and poi.parent = po.name
						and po.docstatus = 1
						and po.status = 'Delivered'""",
					(product.name, product.product_code),
				)

				product_delivered_qty = product_delivered_qty[0][0] if product_delivered_qty else 0
				product.db_set("delivered_qty", flt(product_delivered_qty), update_modified=False)

			delivered_qty += product.delivered_qty
			tot_qty += product.qty

		if tot_qty != 0:
			self.db_set("per_delivered", flt(delivered_qty / tot_qty) * 100, update_modified=False)

	def update_picking_status(self):
		total_picked_qty = 0.0
		total_qty = 0.0
		per_picked = 0.0

		for so_product in self.products:
			if cint(
				frappe.get_cached_value("Product", so_product.product_code, "is_stock_product")
			) or self.has_product_bundle(so_product.product_code):
				total_picked_qty += flt(so_product.picked_qty)
				total_qty += flt(so_product.stock_qty)

		if total_picked_qty and total_qty:
			per_picked = total_picked_qty / total_qty * 100

		self.db_set("per_picked", flt(per_picked), update_modified=False)

	def set_indicator(self):
		"""Set indicator for portal"""
		if self.per_billed < 100 and self.per_delivered < 100:
			self.indicator_color = "orange"
			self.indicator_title = _("Not Paid and Not Delivered")

		elif self.per_billed == 100 and self.per_delivered < 100:
			self.indicator_color = "orange"
			self.indicator_title = _("Paid and Not Delivered")

		else:
			self.indicator_color = "green"
			self.indicator_title = _("Paid")

	def on_recurring(self, reference_doc, auto_repeat_doc):
		def _get_delivery_date(ref_doc_delivery_date, red_doc_transaction_date, transaction_date):
			delivery_date = auto_repeat_doc.get_next_schedule_date(schedule_date=ref_doc_delivery_date)

			if delivery_date <= transaction_date:
				delivery_date_diff = frappe.utils.date_diff(ref_doc_delivery_date, red_doc_transaction_date)
				delivery_date = frappe.utils.add_days(transaction_date, delivery_date_diff)

			return delivery_date

		self.set(
			"delivery_date",
			_get_delivery_date(
				reference_doc.delivery_date, reference_doc.transaction_date, self.transaction_date
			),
		)

		for d in self.get("products"):
			reference_delivery_date = frappe.db.get_value(
				"Sales Order Product",
				{"parent": reference_doc.name, "product_code": d.product_code, "idx": d.idx},
				"delivery_date",
			)

			d.set(
				"delivery_date",
				_get_delivery_date(
					reference_delivery_date, reference_doc.transaction_date, self.transaction_date
				),
			)

	def validate_serial_no_based_delivery(self):
		reserved_products = []
		normal_products = []
		for product in self.products:
			if product.ensure_delivery_based_on_produced_serial_no:
				if product.product_code in normal_products:
					frappe.throw(
						_(
							"Cannot ensure delivery by Serial No as Product {0} is added with and without Ensure Delivery by Serial No."
						).format(product.product_code)
					)
				if product.product_code not in reserved_products:
					if not frappe.get_cached_value("Product", product.product_code, "has_serial_no"):
						frappe.throw(
							_(
								"Product {0} has no Serial No. Only serilialized products can have delivery based on Serial No"
							).format(product.product_code)
						)
					if not frappe.db.exists("BOM", {"product": product.product_code, "is_active": 1}):
						frappe.throw(
							_("No active BOM found for product {0}. Delivery by Serial No cannot be ensured").format(
								product.product_code
							)
						)
				reserved_products.append(product.product_code)
			else:
				normal_products.append(product.product_code)

			if not product.ensure_delivery_based_on_produced_serial_no and product.product_code in reserved_products:
				frappe.throw(
					_(
						"Cannot ensure delivery by Serial No as Product {0} is added with and without Ensure Delivery by Serial No."
					).format(product.product_code)
				)


def get_list_context(context=None):
	from erpnext.controllers.website_list_for_contact import get_list_context

	list_context = get_list_context(context)
	list_context.update(
		{
			"show_sidebar": True,
			"show_search": True,
			"no_breadcrumbs": True,
			"title": _("Orders"),
		}
	)

	return list_context


@frappe.whitelist()
def close_or_unclose_sales_orders(names, status):
	if not frappe.has_permission("Sales Order", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	names = json.loads(names)
	for name in names:
		so = frappe.get_doc("Sales Order", name)
		if so.docstatus == 1:
			if status == "Closed":
				if so.status not in ("Cancelled", "Closed") and (
					so.per_delivered < 100 or so.per_billed < 100
				):
					so.update_status(status)
			else:
				if so.status == "Closed":
					so.update_status("Draft")
			so.update_blanket_order()

	frappe.local.message_log = []


def get_requested_product_qty(sales_order):
	return frappe._dict(
		frappe.db.sql(
			"""
		select sales_order_product, sum(qty)
		from `tabMaterial Request Product`
		where docstatus = 1
			and sales_order = %s
		group by sales_order_product
	""",
			sales_order,
		)
	)


@frappe.whitelist()
def make_material_request(source_name, target_doc=None):
	requested_product_qty = get_requested_product_qty(source_name)

	def update_product(source, target, source_parent):
		# qty is for packed products, because packed products don't have stock_qty field
		qty = source.get("qty")
		target.project = source_parent.project
		target.qty = qty - requested_product_qty.get(source.name, 0) - source.delivered_qty
		target.stock_qty = flt(target.qty) * flt(target.conversion_factor)

		args = target.as_dict().copy()
		args.update(
			{
				"company": source_parent.get("company"),
				"price_list": frappe.db.get_single_value("Buying Settings", "buying_price_list"),
				"currency": source_parent.get("currency"),
				"conversion_rate": source_parent.get("conversion_rate"),
			}
		)

		target.rate = flt(
			get_price_list_rate(args=args, product_doc=frappe.get_cached_doc("Product", target.product_code)).get(
				"price_list_rate"
			)
		)
		target.amount = target.qty * target.rate

	doc = get_mapped_doc(
		"Sales Order",
		source_name,
		{
			"Sales Order": {"doctype": "Material Request", "validation": {"docstatus": ["=", 1]}},
			"Packed Product": {
				"doctype": "Material Request Product",
				"field_map": {"parent": "sales_order", "uom": "stock_uom"},
				"postprocess": update_product,
			},
			"Sales Order Product": {
				"doctype": "Material Request Product",
				"field_map": {"name": "sales_order_product", "parent": "sales_order"},
				"condition": lambda doc: not frappe.db.exists("Product Bundle", doc.product_code)
				and (doc.stock_qty - doc.delivered_qty) > requested_product_qty.get(doc.name, 0),
				"postprocess": update_product,
			},
		},
		target_doc,
	)

	return doc


@frappe.whitelist()
def make_project(source_name, target_doc=None):
	def postprocess(source, doc):
		doc.project_type = "External"
		doc.project_name = source.name

	doc = get_mapped_doc(
		"Sales Order",
		source_name,
		{
			"Sales Order": {
				"doctype": "Project",
				"validation": {"docstatus": ["=", 1]},
				"field_map": {
					"name": "sales_order",
					"base_grand_total": "estimated_costing",
					"net_total": "total_sales_amount",
				},
			},
		},
		target_doc,
		postprocess,
	)

	return doc


@frappe.whitelist()
def make_delivery_note(source_name, target_doc=None, skip_product_mapping=False):
	from erpnext.stock.doctype.packed_product.packed_product import make_packing_list

	def set_missing_values(source, target):
		target.run_method("set_missing_values")
		target.run_method("set_po_nos")
		target.run_method("calculate_taxes_and_totals")

		if source.company_address:
			target.update({"company_address": source.company_address})
		else:
			# set company address
			target.update(get_company_address(target.company))

		if target.company_address:
			target.update(get_fetch_values("Delivery Note", "company_address", target.company_address))

		make_packing_list(target)

	def update_product(source, target, source_parent):
		target.base_amount = (flt(source.qty) - flt(source.delivered_qty)) * flt(source.base_rate)
		target.amount = (flt(source.qty) - flt(source.delivered_qty)) * flt(source.rate)
		target.qty = flt(source.qty) - flt(source.delivered_qty)

		product = get_product_defaults(target.product_code, source_parent.company)
		product_group = get_product_group_defaults(target.product_code, source_parent.company)

		if product:
			target.cost_center = (
				frappe.db.get_value("Project", source_parent.project, "cost_center")
				or product.get("buying_cost_center")
				or product_group.get("buying_cost_center")
			)

	mapper = {
		"Sales Order": {"doctype": "Delivery Note", "validation": {"docstatus": ["=", 1]}},
		"Sales Taxes and Charges": {"doctype": "Sales Taxes and Charges", "add_if_empty": True},
		"Sales Team": {"doctype": "Sales Team", "add_if_empty": True},
	}

	if not skip_product_mapping:

		def condition(doc):
			# make_mapped_doc sets js `args` into `frappe.flags.args`
			if frappe.flags.args and frappe.flags.args.delivery_dates:
				if cstr(doc.delivery_date) not in frappe.flags.args.delivery_dates:
					return False
			return abs(doc.delivered_qty) < abs(doc.qty) and doc.delivered_by_supplier != 1

		mapper["Sales Order Product"] = {
			"doctype": "Delivery Note Product",
			"field_map": {
				"rate": "rate",
				"name": "so_detail",
				"parent": "against_sales_order",
			},
			"postprocess": update_product,
			"condition": condition,
		}

	target_doc = get_mapped_doc("Sales Order", source_name, mapper, target_doc, set_missing_values)

	target_doc.set_onload("ignore_price_list", True)

	return target_doc


@frappe.whitelist()
def make_sales_invoice(source_name, target_doc=None, ignore_permissions=False):
	def postprocess(source, target):
		set_missing_values(source, target)
		# Get the advance paid Journal Entries in Sales Invoice Advance
		if target.get("allocate_advances_automatically"):
			target.set_advances()

	def set_missing_values(source, target):
		target.flags.ignore_permissions = True
		target.run_method("set_missing_values")
		target.run_method("set_po_nos")
		target.run_method("calculate_taxes_and_totals")

		if source.company_address:
			target.update({"company_address": source.company_address})
		else:
			# set company address
			target.update(get_company_address(target.company))

		if target.company_address:
			target.update(get_fetch_values("Sales Invoice", "company_address", target.company_address))

		# set the redeem loyalty points if provided via shopping cart
		if source.loyalty_points and source.order_type == "Shopping Cart":
			target.redeem_loyalty_points = 1

		target.debit_to = get_party_account("Customer", source.customer, source.company)

	def update_product(source, target, source_parent):
		target.amount = flt(source.amount) - flt(source.billed_amt)
		target.base_amount = target.amount * flt(source_parent.conversion_rate)
		target.qty = (
			target.amount / flt(source.rate)
			if (source.rate and source.billed_amt)
			else source.qty - source.returned_qty
		)

		if source_parent.project:
			target.cost_center = frappe.db.get_value("Project", source_parent.project, "cost_center")
		if target.product_code:
			product = get_product_defaults(target.product_code, source_parent.company)
			product_group = get_product_group_defaults(target.product_code, source_parent.company)
			cost_center = product.get("selling_cost_center") or product_group.get("selling_cost_center")

			if cost_center:
				target.cost_center = cost_center

	doclist = get_mapped_doc(
		"Sales Order",
		source_name,
		{
			"Sales Order": {
				"doctype": "Sales Invoice",
				"field_map": {
					"party_account_currency": "party_account_currency",
					"payment_terms_template": "payment_terms_template",
				},
				"field_no_map": ["payment_terms_template"],
				"validation": {"docstatus": ["=", 1]},
			},
			"Sales Order Product": {
				"doctype": "Sales Invoice Product",
				"field_map": {
					"name": "so_detail",
					"parent": "sales_order",
				},
				"postprocess": update_product,
				"condition": lambda doc: doc.qty
				and (doc.base_amount == 0 or abs(doc.billed_amt) < abs(doc.amount)),
			},
			"Sales Taxes and Charges": {"doctype": "Sales Taxes and Charges", "add_if_empty": True},
			"Sales Team": {"doctype": "Sales Team", "add_if_empty": True},
		},
		target_doc,
		postprocess,
		ignore_permissions=ignore_permissions,
	)

	automatically_fetch_payment_terms = cint(
		frappe.db.get_single_value("Accounts Settings", "automatically_fetch_payment_terms")
	)
	if automatically_fetch_payment_terms:
		doclist.set_payment_schedule()

	doclist.set_onload("ignore_price_list", True)

	return doclist


@frappe.whitelist()
def make_maintenance_schedule(source_name, target_doc=None):
	maint_schedule = frappe.db.sql(
		"""select t1.name
		from `tabMaintenance Schedule` t1, `tabMaintenance Schedule Product` t2
		where t2.parent=t1.name and t2.sales_order=%s and t1.docstatus=1""",
		source_name,
	)

	if not maint_schedule:
		doclist = get_mapped_doc(
			"Sales Order",
			source_name,
			{
				"Sales Order": {"doctype": "Maintenance Schedule", "validation": {"docstatus": ["=", 1]}},
				"Sales Order Product": {
					"doctype": "Maintenance Schedule Product",
					"field_map": {"parent": "sales_order"},
				},
			},
			target_doc,
		)

		return doclist


@frappe.whitelist()
def make_maintenance_visit(source_name, target_doc=None):
	visit = frappe.db.sql(
		"""select t1.name
		from `tabMaintenance Visit` t1, `tabMaintenance Visit Purpose` t2
		where t2.parent=t1.name and t2.prevdoc_docname=%s
		and t1.docstatus=1 and t1.completion_status='Fully Completed'""",
		source_name,
	)

	if not visit:
		doclist = get_mapped_doc(
			"Sales Order",
			source_name,
			{
				"Sales Order": {"doctype": "Maintenance Visit", "validation": {"docstatus": ["=", 1]}},
				"Sales Order Product": {
					"doctype": "Maintenance Visit Purpose",
					"field_map": {"parent": "prevdoc_docname", "parenttype": "prevdoc_doctype"},
				},
			},
			target_doc,
		)

		return doclist


@frappe.whitelist()
def get_events(start, end, filters=None):
	"""Returns events for Gantt / Calendar view rendering.

	:param start: Start date-time.
	:param end: End date-time.
	:param filters: Filters (JSON).
	"""
	from frappe.desk.calendar import get_event_conditions

	conditions = get_event_conditions("Sales Order", filters)

	data = frappe.db.sql(
		"""
		select
			distinct `tabSales Order`.name, `tabSales Order`.customer_name, `tabSales Order`.status,
			`tabSales Order`.delivery_status, `tabSales Order`.billing_status,
			`tabSales Order Product`.delivery_date
		from
			`tabSales Order`, `tabSales Order Product`
		where `tabSales Order`.name = `tabSales Order Product`.parent
			and `tabSales Order`.skip_delivery_note = 0
			and (ifnull(`tabSales Order Product`.delivery_date, '0000-00-00')!= '0000-00-00') \
			and (`tabSales Order Product`.delivery_date between %(start)s and %(end)s)
			and `tabSales Order`.docstatus < 2
			{conditions}
		""".format(
			conditions=conditions
		),
		{"start": start, "end": end},
		as_dict=True,
		update={"allDay": 0},
	)
	return data


@frappe.whitelist()
def make_purchase_order_for_default_supplier(source_name, selected_products=None, target_doc=None):
	"""Creates Purchase Order for each Supplier. Returns a list of doc objects."""

	from erpnext.setup.utils import get_exchange_rate

	if not selected_products:
		return

	if isinstance(selected_products, str):
		selected_products = json.loads(selected_products)

	def set_missing_values(source, target):
		target.supplier = supplier
		target.currency = frappe.db.get_value(
			"Supplier", filters={"name": supplier}, fieldname=["default_currency"]
		)
		company_currency = frappe.db.get_value(
			"Company", filters={"name": target.company}, fieldname=["default_currency"]
		)

		target.conversion_rate = get_exchange_rate(target.currency, company_currency, args="for_buying")

		target.apply_discount_on = ""
		target.additional_discount_percentage = 0.0
		target.discount_amount = 0.0
		target.inter_company_order_reference = ""
		target.shipping_rule = ""

		default_price_list = frappe.get_value("Supplier", supplier, "default_price_list")
		if default_price_list:
			target.buying_price_list = default_price_list

		if any(product.delivered_by_supplier == 1 for product in source.products):
			if source.shipping_address_name:
				target.shipping_address = source.shipping_address_name
				target.shipping_address_display = source.shipping_address
			else:
				target.shipping_address = source.customer_address
				target.shipping_address_display = source.address_display

			target.customer_contact_person = source.contact_person
			target.customer_contact_display = source.contact_display
			target.customer_contact_mobile = source.contact_mobile
			target.customer_contact_email = source.contact_email

		else:
			target.customer = ""
			target.customer_name = ""

		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	def update_product(source, target, source_parent):
		target.schedule_date = source.delivery_date
		target.qty = flt(source.qty) - (flt(source.ordered_qty) / flt(source.conversion_factor))
		target.stock_qty = flt(source.stock_qty) - flt(source.ordered_qty)
		target.project = source_parent.project

	suppliers = [product.get("supplier") for product in selected_products if product.get("supplier")]
	suppliers = list(dict.fromkeys(suppliers))  # remove duplicates while preserving order

	products_to_map = [product.get("product_code") for product in selected_products if product.get("product_code")]
	products_to_map = list(set(products_to_map))

	if not suppliers:
		frappe.throw(
			_("Please set a Supplier against the Products to be considered in the Purchase Order.")
		)

	purchase_orders = []
	for supplier in suppliers:
		doc = get_mapped_doc(
			"Sales Order",
			source_name,
			{
				"Sales Order": {
					"doctype": "Purchase Order",
					"field_no_map": [
						"address_display",
						"contact_display",
						"contact_mobile",
						"contact_email",
						"contact_person",
						"taxes_and_charges",
						"shipping_address",
						"terms",
					],
					"validation": {"docstatus": ["=", 1]},
				},
				"Sales Order Product": {
					"doctype": "Purchase Order Product",
					"field_map": [
						["name", "sales_order_product"],
						["parent", "sales_order"],
						["stock_uom", "stock_uom"],
						["uom", "uom"],
						["conversion_factor", "conversion_factor"],
						["delivery_date", "schedule_date"],
					],
					"field_no_map": [
						"rate",
						"price_list_rate",
						"product_tax_template",
						"discount_percentage",
						"discount_amount",
						"pricing_rules",
					],
					"postprocess": update_product,
					"condition": lambda doc: doc.ordered_qty < doc.stock_qty
					and doc.supplier == supplier
					and doc.product_code in products_to_map,
				},
			},
			target_doc,
			set_missing_values,
		)

		doc.insert()
		frappe.db.commit()
		purchase_orders.append(doc)

	return purchase_orders


@frappe.whitelist()
def make_purchase_order(source_name, selected_products=None, target_doc=None):
	if not selected_products:
		return

	if isinstance(selected_products, str):
		selected_products = json.loads(selected_products)

	products_to_map = [
		product.get("product_code")
		for product in selected_products
		if product.get("product_code") and product.get("product_code")
	]
	products_to_map = list(set(products_to_map))

	def is_drop_ship_order(target):
		drop_ship = True
		for product in target.products:
			if not product.delivered_by_supplier:
				drop_ship = False
				break

		return drop_ship

	def set_missing_values(source, target):
		target.supplier = ""
		target.apply_discount_on = ""
		target.additional_discount_percentage = 0.0
		target.discount_amount = 0.0
		target.inter_company_order_reference = ""
		target.shipping_rule = ""

		if is_drop_ship_order(target):
			target.customer = source.customer
			target.customer_name = source.customer_name
			target.shipping_address = source.shipping_address_name
		else:
			target.customer = target.customer_name = target.shipping_address = None

		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	def update_product(source, target, source_parent):
		target.schedule_date = source.delivery_date
		target.qty = flt(source.qty) - (flt(source.ordered_qty) / flt(source.conversion_factor))
		target.stock_qty = flt(source.stock_qty) - flt(source.ordered_qty)
		target.project = source_parent.project

	def update_product_for_packed_product(source, target, source_parent):
		target.qty = flt(source.qty) - flt(source.ordered_qty)

	# po = frappe.get_list("Purchase Order", filters={"sales_order":source_name, "supplier":supplier, "docstatus": ("<", "2")})
	doc = get_mapped_doc(
		"Sales Order",
		source_name,
		{
			"Sales Order": {
				"doctype": "Purchase Order",
				"field_no_map": [
					"address_display",
					"contact_display",
					"contact_mobile",
					"contact_email",
					"contact_person",
					"taxes_and_charges",
					"shipping_address",
					"terms",
				],
				"validation": {"docstatus": ["=", 1]},
			},
			"Sales Order Product": {
				"doctype": "Purchase Order Product",
				"field_map": [
					["name", "sales_order_product"],
					["parent", "sales_order"],
					["stock_uom", "stock_uom"],
					["uom", "uom"],
					["conversion_factor", "conversion_factor"],
					["delivery_date", "schedule_date"],
				],
				"field_no_map": [
					"rate",
					"price_list_rate",
					"product_tax_template",
					"discount_percentage",
					"discount_amount",
					"supplier",
					"pricing_rules",
				],
				"postprocess": update_product,
				"condition": lambda doc: doc.ordered_qty < doc.stock_qty
				and doc.product_code in products_to_map
				and not is_product_bundle(doc.product_code),
			},
			"Packed Product": {
				"doctype": "Purchase Order Product",
				"field_map": [
					["name", "sales_order_packed_product"],
					["parent", "sales_order"],
					["uom", "uom"],
					["conversion_factor", "conversion_factor"],
					["parent_product", "product_bundle"],
					["rate", "rate"],
				],
				"field_no_map": [
					"price_list_rate",
					"product_tax_template",
					"discount_percentage",
					"discount_amount",
					"supplier",
					"pricing_rules",
				],
				"postprocess": update_product_for_packed_product,
				"condition": lambda doc: doc.parent_product in products_to_map,
			},
		},
		target_doc,
		set_missing_values,
	)

	set_delivery_date(doc.products, source_name)

	return doc


def set_delivery_date(products, sales_order):
	delivery_dates = frappe.get_all(
		"Sales Order Product", filters={"parent": sales_order}, fields=["delivery_date", "product_code"]
	)

	delivery_by_product = frappe._dict()
	for date in delivery_dates:
		delivery_by_product[date.product_code] = date.delivery_date

	for product in products:
		if product.product_bundle:
			product.schedule_date = delivery_by_product[product.product_bundle]


def is_product_bundle(product_code):
	return frappe.db.exists("Product Bundle", product_code)


@frappe.whitelist()
def make_work_orders(products, sales_order, company, project=None):
	"""Make Work Orders against the given Sales Order for the given `products`"""
	products = json.loads(products).get("products")
	out = []

	for i in products:
		if not i.get("bom"):
			frappe.throw(_("Please select BOM against product {0}").format(i.get("product_code")))
		if not i.get("pending_qty"):
			frappe.throw(_("Please select Qty against product {0}").format(i.get("product_code")))

		work_order = frappe.get_doc(
			dict(
				doctype="Work Order",
				production_product=i["product_code"],
				bom_no=i.get("bom"),
				qty=i["pending_qty"],
				company=company,
				sales_order=sales_order,
				sales_order_product=i["sales_order_product"],
				project=project,
				fg_warehouse=i["warehouse"],
				description=i["description"],
			)
		).insert()
		work_order.set_work_order_operations()
		work_order.flags.ignore_mandatory = True
		work_order.save()
		out.append(work_order)

	return [p.name for p in out]


@frappe.whitelist()
def update_status(status, name):
	so = frappe.get_doc("Sales Order", name)
	so.update_status(status)


@frappe.whitelist()
def make_raw_material_request(products, company, sales_order, project=None):
	if not frappe.has_permission("Sales Order", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if isinstance(products, str):
		products = frappe._dict(json.loads(products))

	for product in products.get("products"):
		product["include_exploded_products"] = products.get("include_exploded_products")
		product["ignore_existing_ordered_qty"] = products.get("ignore_existing_ordered_qty")
		product["include_raw_materials_from_sales_order"] = products.get(
			"include_raw_materials_from_sales_order"
		)

	products.update({"company": company, "sales_order": sales_order})

	raw_materials = get_products_for_material_requests(products)
	if not raw_materials:
		frappe.msgprint(
			_("Material Request not created, as quantity for Raw Materials already available.")
		)
		return

	material_request = frappe.new_doc("Material Request")
	material_request.update(
		dict(
			doctype="Material Request",
			transaction_date=nowdate(),
			company=company,
			material_request_type="Purchase",
		)
	)
	for product in raw_materials:
		product_doc = frappe.get_cached_doc("Product", product.get("product_code"))

		schedule_date = add_days(nowdate(), cint(product_doc.lead_time_days))
		row = material_request.append(
			"products",
			{
				"product_code": product.get("product_code"),
				"qty": product.get("quantity"),
				"schedule_date": schedule_date,
				"warehouse": product.get("warehouse"),
				"sales_order": sales_order,
				"project": project,
			},
		)

		if not (strip_html(product.get("description")) and strip_html(product_doc.description)):
			row.description = product_doc.product_name or product.get("product_code")

	material_request.insert()
	material_request.flags.ignore_permissions = 1
	material_request.run_method("set_missing_values")
	material_request.submit()
	return material_request


@frappe.whitelist()
def make_inter_company_purchase_order(source_name, target_doc=None):
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_inter_company_transaction

	return make_inter_company_transaction("Sales Order", source_name, target_doc)


@frappe.whitelist()
def create_pick_list(source_name, target_doc=None):
	from erpnext.stock.doctype.packed_product.packed_product import is_product_bundle

	def update_product_quantity(source, target, source_parent) -> None:
		picked_qty = flt(source.picked_qty) / (flt(source.conversion_factor) or 1)
		qty_to_be_picked = flt(source.qty) - max(picked_qty, flt(source.delivered_qty))

		target.qty = qty_to_be_picked
		target.stock_qty = qty_to_be_picked * flt(source.conversion_factor)

	def update_packed_product_qty(source, target, source_parent) -> None:
		qty = flt(source.qty)
		for product in source_parent.products:
			if source.parent_detail_docname == product.name:
				picked_qty = flt(product.picked_qty) / (flt(product.conversion_factor) or 1)
				pending_percent = (product.qty - max(picked_qty, product.delivered_qty)) / product.qty
				target.qty = target.stock_qty = qty * pending_percent
				return

	def should_pick_order_product(product) -> bool:
		return (
			abs(product.delivered_qty) < abs(product.qty)
			and product.delivered_by_supplier != 1
			and not is_product_bundle(product.product_code)
		)

	doc = get_mapped_doc(
		"Sales Order",
		source_name,
		{
			"Sales Order": {"doctype": "Pick List", "validation": {"docstatus": ["=", 1]}},
			"Sales Order Product": {
				"doctype": "Pick List Product",
				"field_map": {"parent": "sales_order", "name": "sales_order_product"},
				"postprocess": update_product_quantity,
				"condition": should_pick_order_product,
			},
			"Packed Product": {
				"doctype": "Pick List Product",
				"field_map": {
					"parent": "sales_order",
					"name": "sales_order_product",
					"parent_detail_docname": "product_bundle_product",
				},
				"field_no_map": ["picked_qty"],
				"postprocess": update_packed_product_qty,
			},
		},
		target_doc,
	)

	doc.purpose = "Delivery"

	doc.set_product_locations()

	return doc


def update_produced_qty_in_so_product(sales_order, sales_order_product):
	# for multiple work orders against same sales order product
	linked_wo_with_so_product = frappe.db.get_all(
		"Work Order",
		["produced_qty"],
		{"sales_order_product": sales_order_product, "sales_order": sales_order, "docstatus": 1},
	)

	total_produced_qty = 0
	for wo in linked_wo_with_so_product:
		total_produced_qty += flt(wo.get("produced_qty"))

	if not total_produced_qty and frappe.flags.in_patch:
		return

	frappe.db.set_value("Sales Order Product", sales_order_product, "produced_qty", total_produced_qty)


@frappe.whitelist()
def get_work_order_products(sales_order, for_raw_material_request=0):
	"""Returns products with BOM that already do not have a linked work order"""
	if sales_order:
		so = frappe.get_doc("Sales Order", sales_order)

		wo = qb.DocType("Work Order")

		products = []
		product_codes = [i.product_code for i in so.products]
		product_bundle_parents = [
			pb.new_product_code
			for pb in frappe.get_all(
				"Product Bundle", {"new_product_code": ["in", product_codes]}, ["new_product_code"]
			)
		]

		for table in [so.products, so.packed_products]:
			for i in table:
				bom = get_default_bom(i.product_code)
				stock_qty = i.qty if i.doctype == "Packed Product" else i.stock_qty

				if not for_raw_material_request:
					total_work_order_qty = flt(
						qb.from_(wo)
						.select(Sum(wo.qty))
						.where(
							(wo.production_product == i.product_code)
							& (wo.sales_order == so.name)
							& (wo.sales_order_product == i.name)
							& (wo.docstatus.lt(2))
						)
						.run()[0][0]
					)
					pending_qty = stock_qty - total_work_order_qty
				else:
					pending_qty = stock_qty

				if pending_qty and i.product_code not in product_bundle_parents:
					products.append(
						dict(
							name=i.name,
							product_code=i.product_code,
							description=i.description,
							bom=bom or "",
							warehouse=i.warehouse,
							pending_qty=pending_qty,
							required_qty=pending_qty if for_raw_material_request else 0,
							sales_order_product=i.name,
						)
					)

		return products
