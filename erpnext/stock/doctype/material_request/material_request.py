# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

# ERPNext - web based ERP (http://erpnext.com)
# For license information, please see license.txt


import json

import frappe
from frappe import _, msgprint
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder.functions import Sum
from frappe.utils import cint, cstr, flt, get_link_to_form, getdate, new_line_sep, nowdate

from erpnext.buying.utils import check_on_hold_or_closed_status, validate_for_products
from erpnext.controllers.buying_controller import BuyingController
from erpnext.manufacturing.doctype.work_order.work_order import get_product_details
from erpnext.stock.doctype.product.product import get_product_defaults
from erpnext.stock.stock_balance import get_indented_qty, update_bin_qty

form_grid_templates = {"products": "templates/form_grid/material_request_grid.html"}


class MaterialRequest(BuyingController):
	def get_feed(self):
		return

	def check_if_already_pulled(self):
		pass

	def validate_qty_against_so(self):
		so_products = {}  # Format --> {'SO/00001': {'Product/001': 120, 'Product/002': 24}}
		for d in self.get("products"):
			if d.sales_order:
				if not d.sales_order in so_products:
					so_products[d.sales_order] = {d.product_code: flt(d.qty)}
				else:
					if not d.product_code in so_products[d.sales_order]:
						so_products[d.sales_order][d.product_code] = flt(d.qty)
					else:
						so_products[d.sales_order][d.product_code] += flt(d.qty)

		for so_no in so_products.keys():
			for product in so_products[so_no].keys():
				already_indented = frappe.db.sql(
					"""select sum(qty)
					from `tabMaterial Request Product`
					where product_code = %s and sales_order = %s and
					docstatus = 1 and parent != %s""",
					(product, so_no, self.name),
				)
				already_indented = already_indented and flt(already_indented[0][0]) or 0

				actual_so_qty = frappe.db.sql(
					"""select sum(stock_qty) from `tabSales Order Product`
					where parent = %s and product_code = %s and docstatus = 1""",
					(so_no, product),
				)
				actual_so_qty = actual_so_qty and flt(actual_so_qty[0][0]) or 0

				if actual_so_qty and (flt(so_products[so_no][product]) + already_indented > actual_so_qty):
					frappe.throw(
						_("Material Request of maximum {0} can be made for Product {1} against Sales Order {2}").format(
							actual_so_qty - already_indented, product, so_no
						)
					)

	def validate(self):
		super(MaterialRequest, self).validate()

		self.validate_schedule_date()
		self.check_for_on_hold_or_closed_status("Sales Order", "sales_order")
		self.validate_uom_is_integer("uom", "qty")
		self.validate_material_request_type()

		if not self.status:
			self.status = "Draft"

		from erpnext.controllers.status_updater import validate_status

		validate_status(
			self.status,
			[
				"Draft",
				"Submitted",
				"Stopped",
				"Cancelled",
				"Pending",
				"Partially Ordered",
				"Ordered",
				"Issued",
				"Transferred",
				"Received",
			],
		)

		validate_for_products(self)

		self.set_title()
		# self.validate_qty_against_so()
		# NOTE: Since Product BOM and FG quantities are combined, using current data, it cannot be validated
		# Though the creation of Material Request from a Production Plan can be rethought to fix this

		self.reset_default_field_value("set_warehouse", "products", "warehouse")
		self.reset_default_field_value("set_from_warehouse", "products", "from_warehouse")

	def before_update_after_submit(self):
		self.validate_schedule_date()

	def validate_material_request_type(self):
		"""Validate fields in accordance with selected type"""

		if self.material_request_type != "Customer Provided":
			self.customer = None

	def set_title(self):
		"""Set title as comma separated list of products"""
		if not self.title:
			products = ", ".join([d.product_name for d in self.products][:3])
			self.title = _("{0} Request for {1}").format(self.material_request_type, products)[:100]

	def on_submit(self):
		self.update_requested_qty()
		self.update_requested_qty_in_production_plan()
		if self.material_request_type == "Purchase":
			self.validate_budget()

	def before_save(self):
		self.set_status(update=True)

	def before_submit(self):
		self.set_status(update=True)

	def before_cancel(self):
		# if MRQ is already closed, no point saving the document
		check_on_hold_or_closed_status(self.doctype, self.name)

		self.set_status(update=True, status="Cancelled")

	def check_modified_date(self):
		mod_db = frappe.db.sql(
			"""select modified from `tabMaterial Request` where name = %s""", self.name
		)
		date_diff = frappe.db.sql(
			"""select TIMEDIFF('%s', '%s')""" % (mod_db[0][0], cstr(self.modified))
		)

		if date_diff and date_diff[0][0]:
			frappe.throw(_("{0} {1} has been modified. Please refresh.").format(_(self.doctype), self.name))

	def update_status(self, status):
		self.check_modified_date()
		self.status_can_change(status)
		self.set_status(update=True, status=status)
		self.update_requested_qty()

	def status_can_change(self, status):
		"""
		validates that `status` is acceptable for the present controller status
		and throws an Exception if otherwise.
		"""
		if self.status and self.status == "Cancelled":
			# cancelled documents cannot change
			if status != self.status:
				frappe.throw(
					_("{0} {1} is cancelled so the action cannot be completed").format(
						_(self.doctype), self.name
					),
					frappe.InvalidStatusError,
				)

		elif self.status and self.status == "Draft":
			# draft document to pending only
			if status != "Pending":
				frappe.throw(
					_("{0} {1} has not been submitted so the action cannot be completed").format(
						_(self.doctype), self.name
					),
					frappe.InvalidStatusError,
				)

	def on_cancel(self):
		self.update_requested_qty()
		self.update_requested_qty_in_production_plan()

	def get_mr_products_ordered_qty(self, mr_products):
		mr_products_ordered_qty = {}
		mr_products = [d.name for d in self.get("products") if d.name in mr_products]

		doctype = qty_field = None
		if self.material_request_type in ("Material Issue", "Material Transfer", "Customer Provided"):
			doctype = frappe.qb.DocType("Stock Entry Detail")
			qty_field = doctype.transfer_qty
		elif self.material_request_type == "Manufacture":
			doctype = frappe.qb.DocType("Work Order")
			qty_field = doctype.qty

		if doctype and qty_field:
			query = (
				frappe.qb.from_(doctype)
				.select(doctype.material_request_product, Sum(qty_field))
				.where(
					(doctype.material_request == self.name)
					& (doctype.material_request_product.isin(mr_products))
					& (doctype.docstatus == 1)
				)
				.groupby(doctype.material_request_product)
			)

			mr_products_ordered_qty = frappe._dict(query.run())

		return mr_products_ordered_qty

	def update_completed_qty(self, mr_products=None, update_modified=True):
		if self.material_request_type == "Purchase":
			return

		if not mr_products:
			mr_products = [d.name for d in self.get("products")]

		mr_products_ordered_qty = self.get_mr_products_ordered_qty(mr_products)
		mr_qty_allowance = frappe.db.get_single_value("Stock Settings", "mr_qty_allowance")

		for d in self.get("products"):
			if d.name in mr_products:
				if self.material_request_type in ("Material Issue", "Material Transfer", "Customer Provided"):
					d.ordered_qty = flt(mr_products_ordered_qty.get(d.name))

					if mr_qty_allowance:
						allowed_qty = d.qty + (d.qty * (mr_qty_allowance / 100))
						if d.ordered_qty and d.ordered_qty > allowed_qty:
							frappe.throw(
								_(
									"The total Issue / Transfer quantity {0} in Material Request {1}  cannot be greater than allowed requested quantity {2} for Product {3}"
								).format(d.ordered_qty, d.parent, allowed_qty, d.product_code)
							)

					elif d.ordered_qty and d.ordered_qty > d.stock_qty:
						frappe.throw(
							_(
								"The total Issue / Transfer quantity {0} in Material Request {1} cannot be greater than requested quantity {2} for Product {3}"
							).format(d.ordered_qty, d.parent, d.qty, d.product_code)
						)

				elif self.material_request_type == "Manufacture":
					d.ordered_qty = flt(mr_products_ordered_qty.get(d.name))

				frappe.db.set_value(d.doctype, d.name, "ordered_qty", d.ordered_qty)

		self._update_percent_field(
			{
				"target_dt": "Material Request Product",
				"target_parent_dt": self.doctype,
				"target_parent_field": "per_ordered",
				"target_ref_field": "stock_qty",
				"target_field": "ordered_qty",
				"name": self.name,
			},
			update_modified,
		)

	def update_requested_qty(self, mr_product_rows=None):
		"""update requested qty (before ordered_qty is updated)"""
		product_wh_list = []
		for d in self.get("products"):
			if (
				(not mr_product_rows or d.name in mr_product_rows)
				and [d.product_code, d.warehouse] not in product_wh_list
				and d.warehouse
				and frappe.db.get_value("Product", d.product_code, "is_stock_product") == 1
			):
				product_wh_list.append([d.product_code, d.warehouse])

		for product_code, warehouse in product_wh_list:
			update_bin_qty(product_code, warehouse, {"indented_qty": get_indented_qty(product_code, warehouse)})

	def update_requested_qty_in_production_plan(self):
		production_plans = []
		for d in self.get("products"):
			if d.production_plan and d.material_request_plan_product:
				qty = d.qty if self.docstatus == 1 else 0
				frappe.db.set_value(
					"Material Request Plan Product", d.material_request_plan_product, "requested_qty", qty
				)

				if d.production_plan not in production_plans:
					production_plans.append(d.production_plan)

		for production_plan in production_plans:
			doc = frappe.get_doc("Production Plan", production_plan)
			doc.set_status()
			doc.db_set("status", doc.status)


def update_completed_and_requested_qty(stock_entry, method):
	if stock_entry.doctype == "Stock Entry":
		material_request_map = {}

		for d in stock_entry.get("products"):
			if d.material_request:
				material_request_map.setdefault(d.material_request, []).append(d.material_request_product)

		for mr, mr_product_rows in material_request_map.products():
			if mr and mr_product_rows:
				mr_obj = frappe.get_doc("Material Request", mr)

				if mr_obj.status in ["Stopped", "Cancelled"]:
					frappe.throw(
						_("{0} {1} is cancelled or stopped").format(_("Material Request"), mr),
						frappe.InvalidStatusError,
					)

				mr_obj.update_completed_qty(mr_product_rows)
				mr_obj.update_requested_qty(mr_product_rows)


def set_missing_values(source, target_doc):
	if target_doc.doctype == "Purchase Order" and getdate(target_doc.schedule_date) < getdate(
		nowdate()
	):
		target_doc.schedule_date = None
	target_doc.run_method("set_missing_values")
	target_doc.run_method("calculate_taxes_and_totals")


def update_product(obj, target, source_parent):
	target.conversion_factor = obj.conversion_factor
	target.qty = flt(flt(obj.stock_qty) - flt(obj.ordered_qty)) / target.conversion_factor
	target.stock_qty = target.qty * target.conversion_factor
	if getdate(target.schedule_date) < getdate(nowdate()):
		target.schedule_date = None


def get_list_context(context=None):
	from erpnext.controllers.website_list_for_contact import get_list_context

	list_context = get_list_context(context)
	list_context.update(
		{
			"show_sidebar": True,
			"show_search": True,
			"no_breadcrumbs": True,
			"title": _("Material Request"),
		}
	)

	return list_context


@frappe.whitelist()
def update_status(name, status):
	material_request = frappe.get_doc("Material Request", name)
	material_request.check_permission("write")
	material_request.update_status(status)


@frappe.whitelist()
def make_purchase_order(source_name, target_doc=None, args=None):
	if args is None:
		args = {}
	if isinstance(args, str):
		args = json.loads(args)

	def postprocess(source, target_doc):
		if frappe.flags.args and frappe.flags.args.default_supplier:
			# products only for given default supplier
			supplier_products = []
			for d in target_doc.products:
				default_supplier = get_product_defaults(d.product_code, target_doc.company).get("default_supplier")
				if frappe.flags.args.default_supplier == default_supplier:
					supplier_products.append(d)
			target_doc.products = supplier_products

		set_missing_values(source, target_doc)

	def select_product(d):
		filtered_products = args.get("filtered_children", [])
		child_filter = d.name in filtered_products if filtered_products else True

		return d.ordered_qty < d.stock_qty and child_filter

	doclist = get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Purchase Order",
				"validation": {"docstatus": ["=", 1], "material_request_type": ["=", "Purchase"]},
			},
			"Material Request Product": {
				"doctype": "Purchase Order Product",
				"field_map": [
					["name", "material_request_product"],
					["parent", "material_request"],
					["uom", "stock_uom"],
					["uom", "uom"],
					["sales_order", "sales_order"],
					["sales_order_product", "sales_order_product"],
				],
				"postprocess": update_product,
				"condition": select_product,
			},
		},
		target_doc,
		postprocess,
	)

	return doclist


@frappe.whitelist()
def make_request_for_quotation(source_name, target_doc=None):
	doclist = get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Request for Quotation",
				"validation": {"docstatus": ["=", 1], "material_request_type": ["=", "Purchase"]},
			},
			"Material Request Product": {
				"doctype": "Request for Quotation Product",
				"field_map": [
					["name", "material_request_product"],
					["parent", "material_request"],
					["uom", "uom"],
				],
			},
		},
		target_doc,
	)

	return doclist


@frappe.whitelist()
def make_purchase_order_based_on_supplier(source_name, target_doc=None, args=None):
	mr = source_name

	supplier_products = get_products_based_on_default_supplier(args.get("supplier"))

	def postprocess(source, target_doc):
		target_doc.supplier = args.get("supplier")
		if getdate(target_doc.schedule_date) < getdate(nowdate()):
			target_doc.schedule_date = None
		target_doc.set(
			"products",
			[
				d for d in target_doc.get("products") if d.get("product_code") in supplier_products and d.get("qty") > 0
			],
		)

		set_missing_values(source, target_doc)

	target_doc = get_mapped_doc(
		"Material Request",
		mr,
		{
			"Material Request": {
				"doctype": "Purchase Order",
			},
			"Material Request Product": {
				"doctype": "Purchase Order Product",
				"field_map": [
					["name", "material_request_product"],
					["parent", "material_request"],
					["uom", "stock_uom"],
					["uom", "uom"],
				],
				"postprocess": update_product,
				"condition": lambda doc: doc.ordered_qty < doc.qty,
			},
		},
		target_doc,
		postprocess,
	)

	return target_doc


@frappe.whitelist()
def get_products_based_on_default_supplier(supplier):
	supplier_products = [
		d.parent
		for d in frappe.db.get_all(
			"Product Default", {"default_supplier": supplier, "parenttype": "Product"}, "parent"
		)
	]

	return supplier_products


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_material_requests_based_on_supplier(doctype, txt, searchfield, start, page_len, filters):
	conditions = ""
	if txt:
		conditions += "and mr.name like '%%" + txt + "%%' "

	if filters.get("transaction_date"):
		date = filters.get("transaction_date")[1]
		conditions += "and mr.transaction_date between '{0}' and '{1}' ".format(date[0], date[1])

	supplier = filters.get("supplier")
	supplier_products = get_products_based_on_default_supplier(supplier)

	if not supplier_products:
		frappe.throw(_("{0} is not the default supplier for any products.").format(supplier))

	material_requests = frappe.db.sql(
		"""select distinct mr.name, transaction_date,company
		from `tabMaterial Request` mr, `tabMaterial Request Product` mr_product
		where mr.name = mr_product.parent
			and mr_product.product_code in ({0})
			and mr.material_request_type = 'Purchase'
			and mr.per_ordered < 99.99
			and mr.docstatus = 1
			and mr.status != 'Stopped'
			and mr.company = %s
			{1}
		order by mr_product.product_code ASC
		limit {2} offset {3} """.format(
			", ".join(["%s"] * len(supplier_products)), conditions, cint(page_len), cint(start)
		),
		tuple(supplier_products) + (filters.get("company"),),
		as_dict=1,
	)

	return material_requests


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_default_supplier_query(doctype, txt, searchfield, start, page_len, filters):
	doc = frappe.get_doc("Material Request", filters.get("doc"))
	product_list = []
	for d in doc.products:
		product_list.append(d.product_code)

	return frappe.db.sql(
		"""select default_supplier
		from `tabProduct Default`
		where parent in ({0}) and
		default_supplier IS NOT NULL
		""".format(
			", ".join(["%s"] * len(product_list))
		),
		tuple(product_list),
	)


@frappe.whitelist()
def make_supplier_quotation(source_name, target_doc=None):
	def postprocess(source, target_doc):
		set_missing_values(source, target_doc)

	doclist = get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Supplier Quotation",
				"validation": {"docstatus": ["=", 1], "material_request_type": ["=", "Purchase"]},
			},
			"Material Request Product": {
				"doctype": "Supplier Quotation Product",
				"field_map": {
					"name": "material_request_product",
					"parent": "material_request",
					"sales_order": "sales_order",
				},
			},
		},
		target_doc,
		postprocess,
	)

	return doclist


@frappe.whitelist()
def make_stock_entry(source_name, target_doc=None):
	def update_product(obj, target, source_parent):
		qty = (
			flt(flt(obj.stock_qty) - flt(obj.ordered_qty)) / target.conversion_factor
			if flt(obj.stock_qty) > flt(obj.ordered_qty)
			else 0
		)
		target.qty = qty
		target.transfer_qty = qty * obj.conversion_factor
		target.conversion_factor = obj.conversion_factor

		if (
			source_parent.material_request_type == "Material Transfer"
			or source_parent.material_request_type == "Customer Provided"
		):
			target.t_warehouse = obj.warehouse
		else:
			target.s_warehouse = obj.warehouse

		if source_parent.material_request_type == "Customer Provided":
			target.allow_zero_valuation_rate = 1

		if source_parent.material_request_type == "Material Transfer":
			target.s_warehouse = obj.from_warehouse

	def set_missing_values(source, target):
		target.purpose = source.material_request_type
		target.from_warehouse = source.set_from_warehouse
		target.to_warehouse = source.set_warehouse

		if source.job_card:
			target.purpose = "Material Transfer for Manufacture"

		if source.material_request_type == "Customer Provided":
			target.purpose = "Material Receipt"

		target.set_transfer_qty()
		target.set_actual_qty()
		target.calculate_rate_and_amount(raise_error_if_no_rate=False)
		target.stock_entry_type = target.purpose
		target.set_job_card_data()

		if source.job_card:
			job_card_details = frappe.get_all(
				"Job Card", filters={"name": source.job_card}, fields=["bom_no", "for_quantity"]
			)

			if job_card_details and job_card_details[0]:
				target.bom_no = job_card_details[0].bom_no
				target.fg_completed_qty = job_card_details[0].for_quantity
				target.from_bom = 1

	doclist = get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Stock Entry",
				"validation": {
					"docstatus": ["=", 1],
					"material_request_type": ["in", ["Material Transfer", "Material Issue", "Customer Provided"]],
				},
			},
			"Material Request Product": {
				"doctype": "Stock Entry Detail",
				"field_map": {
					"name": "material_request_product",
					"parent": "material_request",
					"uom": "stock_uom",
					"job_card_product": "job_card_product",
				},
				"postprocess": update_product,
				"condition": lambda doc: doc.ordered_qty < doc.stock_qty,
			},
		},
		target_doc,
		set_missing_values,
	)

	return doclist


@frappe.whitelist()
def raise_work_orders(material_request):
	mr = frappe.get_doc("Material Request", material_request)
	errors = []
	work_orders = []
	default_wip_warehouse = frappe.db.get_single_value(
		"Manufacturing Settings", "default_wip_warehouse"
	)

	for d in mr.products:
		if (d.stock_qty - d.ordered_qty) > 0:
			if frappe.db.exists("BOM", {"product": d.product_code, "is_default": 1}):
				wo_order = frappe.new_doc("Work Order")
				wo_order.update(
					{
						"production_product": d.product_code,
						"qty": d.stock_qty - d.ordered_qty,
						"fg_warehouse": d.warehouse,
						"wip_warehouse": default_wip_warehouse,
						"description": d.description,
						"stock_uom": d.stock_uom,
						"expected_delivery_date": d.schedule_date,
						"sales_order": d.sales_order,
						"sales_order_product": d.get("sales_order_product"),
						"bom_no": get_product_details(d.product_code).bom_no,
						"material_request": mr.name,
						"material_request_product": d.name,
						"planned_start_date": mr.transaction_date,
						"company": mr.company,
					}
				)

				wo_order.set_work_order_operations()
				wo_order.save()

				work_orders.append(wo_order.name)
			else:
				errors.append(
					_("Row {0}: Bill of Materials not found for the Product {1}").format(
						d.idx, get_link_to_form("Product", d.product_code)
					)
				)

	if work_orders:
		work_orders_list = [get_link_to_form("Work Order", d) for d in work_orders]

		if len(work_orders) > 1:
			msgprint(
				_("The following {0} were created: {1}").format(
					frappe.bold(_("Work Orders")), "<br>" + ", ".join(work_orders_list)
				)
			)
		else:
			msgprint(
				_("The {0} {1} created sucessfully").format(frappe.bold(_("Work Order")), work_orders_list[0])
			)

	if errors:
		frappe.throw(
			_("Work Order cannot be created for following reason: <br> {0}").format(new_line_sep(errors))
		)

	return work_orders


@frappe.whitelist()
def create_pick_list(source_name, target_doc=None):
	doc = get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Pick List",
				"field_map": {"material_request_type": "purpose"},
				"validation": {"docstatus": ["=", 1]},
			},
			"Material Request Product": {
				"doctype": "Pick List Product",
				"field_map": {"name": "material_request_product", "qty": "stock_qty"},
			},
		},
		target_doc,
	)

	doc.set_product_locations()

	return doc


@frappe.whitelist()
def make_in_transit_stock_entry(source_name, in_transit_warehouse):
	ste_doc = make_stock_entry(source_name)
	ste_doc.add_to_transit = 1
	ste_doc.to_warehouse = in_transit_warehouse

	for row in ste_doc.products:
		row.t_warehouse = in_transit_warehouse

	return ste_doc
