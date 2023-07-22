# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import copy
import json

import frappe
from frappe import _, msgprint
from frappe.model.document import Document
from frappe.query_builder.functions import IfNull, Sum
from frappe.utils import (
	add_days,
	ceil,
	cint,
	comma_and,
	flt,
	get_link_to_form,
	getdate,
	now_datetime,
	nowdate,
)
from frappe.utils.csvutils import build_csv_response
from pypika.terms import ExistsCriterion

from erpnext.manufacturing.doctype.bom.bom import get_children as get_bom_children
from erpnext.manufacturing.doctype.bom.bom import validate_bom_no
from erpnext.manufacturing.doctype.work_order.work_order import get_product_details
from erpnext.setup.doctype.product_group.product_group import get_product_group_defaults
from erpnext.stock.get_product_details import get_conversion_factor
from erpnext.stock.utils import get_or_make_bin
from erpnext.utilities.transaction_base import validate_uom_is_integer


class ProductionPlan(Document):
	def validate(self):
		self.set_pending_qty_in_row_without_reference()
		self.calculate_total_planned_qty()
		self.set_status()
		self._rename_temporary_references()
		validate_uom_is_integer(self, "stock_uom", "planned_qty")

	def set_pending_qty_in_row_without_reference(self):
		"Set Pending Qty in independent rows (not from SO or MR)."
		if self.docstatus > 0:  # set only to initialise value before submit
			return

		for product in self.po_products:
			if not product.get("sales_order") or not product.get("material_request"):
				product.pending_qty = product.planned_qty

	def calculate_total_planned_qty(self):
		self.total_planned_qty = 0
		for d in self.po_products:
			self.total_planned_qty += flt(d.planned_qty)

	def validate_data(self):
		for d in self.get("po_products"):
			if not d.bom_no:
				frappe.throw(_("Please select BOM for Product in Row {0}").format(d.idx))
			else:
				validate_bom_no(d.product_code, d.bom_no)

			if not flt(d.planned_qty):
				frappe.throw(_("Please enter Planned Qty for Product {0} at row {1}").format(d.product_code, d.idx))

	def _rename_temporary_references(self):
		"""po_products and sub_assembly_products products are both constructed client side without saving.

		Attempt to fix linkages by using temporary names to map final row names.
		"""
		new_name_map = {d.temporary_name: d.name for d in self.po_products if d.temporary_name}
		actual_names = {d.name for d in self.po_products}

		for sub_assy in self.sub_assembly_products:
			if sub_assy.production_plan_product not in actual_names:
				sub_assy.production_plan_product = new_name_map.get(sub_assy.production_plan_product)

	@frappe.whitelist()
	def get_open_sales_orders(self):
		"""Pull sales orders  which are pending to deliver based on criteria selected"""
		open_so = get_sales_orders(self)

		if open_so:
			self.add_so_in_table(open_so)
		else:
			frappe.msgprint(_("Sales orders are not available for production"))

	def add_so_in_table(self, open_so):
		"""Add sales orders in the table"""
		self.set("sales_orders", [])

		for data in open_so:
			self.append(
				"sales_orders",
				{
					"sales_order": data.name,
					"sales_order_date": data.transaction_date,
					"customer": data.customer,
					"grand_total": data.base_grand_total,
				},
			)

	@frappe.whitelist()
	def get_pending_material_requests(self):
		"""Pull Material Requests that are pending based on criteria selected"""

		bom = frappe.qb.DocType("BOM")
		mr = frappe.qb.DocType("Material Request")
		mr_product = frappe.qb.DocType("Material Request Product")

		pending_mr_query = (
			frappe.qb.from_(mr)
			.from_(mr_product)
			.select(mr.name, mr.transaction_date)
			.distinct()
			.where(
				(mr_product.parent == mr.name)
				& (mr.material_request_type == "Manufacture")
				& (mr.docstatus == 1)
				& (mr.status != "Stopped")
				& (mr.company == self.company)
				& (mr_product.qty > IfNull(mr_product.ordered_qty, 0))
				& (
					ExistsCriterion(
						frappe.qb.from_(bom)
						.select(bom.name)
						.where((bom.product == mr_product.product_code) & (bom.is_active == 1))
					)
				)
			)
		)

		if self.from_date:
			pending_mr_query = pending_mr_query.where(mr.transaction_date >= self.from_date)

		if self.to_date:
			pending_mr_query = pending_mr_query.where(mr.transaction_date <= self.to_date)

		if self.warehouse:
			pending_mr_query = pending_mr_query.where(mr_product.warehouse == self.warehouse)

		if self.product_code:
			pending_mr_query = pending_mr_query.where(mr_product.product_code == self.product_code)

		pending_mr = pending_mr_query.run(as_dict=True)

		self.add_mr_in_table(pending_mr)

	def add_mr_in_table(self, pending_mr):
		"""Add Material Requests in the table"""
		self.set("material_requests", [])

		for data in pending_mr:
			self.append(
				"material_requests",
				{"material_request": data.name, "material_request_date": data.transaction_date},
			)

	@frappe.whitelist()
	def get_products(self):
		self.set("po_products", [])
		if self.get_products_from == "Sales Order":
			self.get_so_products()

		elif self.get_products_from == "Material Request":
			self.get_mr_products()

	def get_so_mr_list(self, field, table):
		"""Returns a list of Sales Orders or Material Requests from the respective tables"""
		so_mr_list = [d.get(field) for d in self.get(table) if d.get(field)]
		return so_mr_list

	def get_bom_product_condition(self):
		"""Check if Product or if its Template has a BOM."""
		bom_product_condition = None
		has_bom = frappe.db.exists({"doctype": "BOM", "product": self.product_code, "docstatus": 1})

		if not has_bom:
			bom = frappe.qb.DocType("BOM")
			template_product = frappe.db.get_value("Product", self.product_code, ["variant_of"])
			bom_product_condition = bom.product == template_product or None

		return bom_product_condition

	def get_so_products(self):
		# Check for empty table or empty rows
		if not self.get("sales_orders") or not self.get_so_mr_list("sales_order", "sales_orders"):
			frappe.throw(_("Please fill the Sales Orders table"), title=_("Sales Orders Required"))

		so_list = self.get_so_mr_list("sales_order", "sales_orders")

		bom = frappe.qb.DocType("BOM")
		so_product = frappe.qb.DocType("Sales Order Product")

		products_subquery = frappe.qb.from_(bom).select(bom.name).where(bom.is_active == 1)
		products_query = (
			frappe.qb.from_(so_product)
			.select(
				so_product.parent,
				so_product.product_code,
				so_product.warehouse,
				(
					(so_product.qty - so_product.work_order_qty - so_product.delivered_qty) * so_product.conversion_factor
				).as_("pending_qty"),
				so_product.description,
				so_product.name,
			)
			.distinct()
			.where(
				(so_product.parent.isin(so_list))
				& (so_product.docstatus == 1)
				& (so_product.qty > so_product.work_order_qty)
			)
		)

		if self.product_code and frappe.db.exists("Product", self.product_code):
			products_query = products_query.where(so_product.product_code == self.product_code)
			products_subquery = products_subquery.where(
				self.get_bom_product_condition() or bom.product == so_product.product_code
			)

		products_query = products_query.where(ExistsCriterion(products_subquery))

		products = products_query.run(as_dict=True)

		pi = frappe.qb.DocType("Packed Product")

		packed_products_query = (
			frappe.qb.from_(so_product)
			.from_(pi)
			.select(
				pi.parent,
				pi.product_code,
				pi.warehouse.as_("warehouse"),
				(((so_product.qty - so_product.work_order_qty) * pi.qty) / so_product.qty).as_("pending_qty"),
				pi.parent_product,
				pi.description,
				so_product.name,
			)
			.distinct()
			.where(
				(so_product.parent == pi.parent)
				& (so_product.docstatus == 1)
				& (pi.parent_product == so_product.product_code)
				& (so_product.parent.isin(so_list))
				& (so_product.qty > so_product.work_order_qty)
				& (
					ExistsCriterion(
						frappe.qb.from_(bom)
						.select(bom.name)
						.where((bom.product == pi.product_code) & (bom.is_active == 1))
					)
				)
			)
		)

		if self.product_code:
			packed_products_query = packed_products_query.where(so_product.product_code == self.product_code)

		packed_products = packed_products_query.run(as_dict=True)

		self.add_products(products + packed_products)
		self.calculate_total_planned_qty()

	def get_mr_products(self):
		# Check for empty table or empty rows
		if not self.get("material_requests") or not self.get_so_mr_list(
			"material_request", "material_requests"
		):
			frappe.throw(
				_("Please fill the Material Requests table"), title=_("Material Requests Required")
			)

		mr_list = self.get_so_mr_list("material_request", "material_requests")

		bom = frappe.qb.DocType("BOM")
		mr_product = frappe.qb.DocType("Material Request Product")

		products_query = (
			frappe.qb.from_(mr_product)
			.select(
				mr_product.parent,
				mr_product.name,
				mr_product.product_code,
				mr_product.warehouse,
				mr_product.description,
				((mr_product.qty - mr_product.ordered_qty) * mr_product.conversion_factor).as_("pending_qty"),
			)
			.distinct()
			.where(
				(mr_product.parent.isin(mr_list))
				& (mr_product.docstatus == 1)
				& (mr_product.qty > mr_product.ordered_qty)
				& (
					ExistsCriterion(
						frappe.qb.from_(bom)
						.select(bom.name)
						.where((bom.product == mr_product.product_code) & (bom.is_active == 1))
					)
				)
			)
		)

		if self.product_code:
			products_query = products_query.where(mr_product.product_code == self.product_code)

		products = products_query.run(as_dict=True)

		self.add_products(products)
		self.calculate_total_planned_qty()

	def add_products(self, products):
		refs = {}
		for data in products:
			if not data.pending_qty:
				continue

			product_details = get_product_details(data.product_code)
			if self.combine_products:
				if product_details.bom_no in refs:
					refs[product_details.bom_no]["so_details"].append(
						{"sales_order": data.parent, "sales_order_product": data.name, "qty": data.pending_qty}
					)
					refs[product_details.bom_no]["qty"] += data.pending_qty
					continue

				else:
					refs[product_details.bom_no] = {
						"qty": data.pending_qty,
						"po_product_ref": data.name,
						"so_details": [],
					}
					refs[product_details.bom_no]["so_details"].append(
						{"sales_order": data.parent, "sales_order_product": data.name, "qty": data.pending_qty}
					)

			pi = self.append(
				"po_products",
				{
					"warehouse": data.warehouse,
					"product_code": data.product_code,
					"description": data.description or product_details.description,
					"stock_uom": product_details and product_details.stock_uom or "",
					"bom_no": product_details and product_details.bom_no or "",
					"planned_qty": data.pending_qty,
					"pending_qty": data.pending_qty,
					"planned_start_date": now_datetime(),
					"product_bundle_product": data.parent_product,
				},
			)
			pi._set_defaults()

			if self.get_products_from == "Sales Order":
				pi.sales_order = data.parent
				pi.sales_order_product = data.name
				pi.description = data.description

			elif self.get_products_from == "Material Request":
				pi.material_request = data.parent
				pi.material_request_product = data.name
				pi.description = data.description

		if refs:
			for po_product in self.po_products:
				po_product.planned_qty = refs[po_product.bom_no]["qty"]
				po_product.pending_qty = refs[po_product.bom_no]["qty"]
				po_product.sales_order = ""
			self.add_pp_ref(refs)

	def add_pp_ref(self, refs):
		for bom_no in refs:
			for so_detail in refs[bom_no]["so_details"]:
				self.append(
					"prod_plan_references",
					{
						"product_reference": refs[bom_no]["po_product_ref"],
						"sales_order": so_detail["sales_order"],
						"sales_order_product": so_detail["sales_order_product"],
						"qty": so_detail["qty"],
					},
				)

	def calculate_total_produced_qty(self):
		self.total_produced_qty = 0
		for d in self.po_products:
			self.total_produced_qty += flt(d.produced_qty)

		self.db_set("total_produced_qty", self.total_produced_qty, update_modified=False)

	def update_produced_pending_qty(self, produced_qty, production_plan_product):
		for data in self.po_products:
			if data.name == production_plan_product:
				data.produced_qty = produced_qty
				data.pending_qty = flt(data.planned_qty - produced_qty)
				data.db_update()

		self.calculate_total_produced_qty()
		self.set_status()
		self.db_set("status", self.status)

	def on_submit(self):
		self.update_bin_qty()

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		self.delete_draft_work_order()
		self.update_bin_qty()

	def update_bin_qty(self):
		for d in self.mr_products:
			if d.warehouse:
				bin_name = get_or_make_bin(d.product_code, d.warehouse)
				bin = frappe.get_doc("Bin", bin_name, for_update=True)
				bin.update_reserved_qty_for_production_plan()

	def delete_draft_work_order(self):
		for d in frappe.get_all(
			"Work Order", fields=["name"], filters={"docstatus": 0, "production_plan": ("=", self.name)}
		):
			frappe.delete_doc("Work Order", d.name)

	@frappe.whitelist()
	def set_status(self, close=None):
		self.status = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(self.docstatus)

		if close:
			self.db_set("status", "Closed")
			return

		if self.total_produced_qty > 0:
			self.status = "In Process"
			if self.all_products_completed():
				self.status = "Completed"

		if self.status != "Completed":
			self.update_ordered_status()
			self.update_requested_status()

		if close is not None:
			self.db_set("status", self.status)

	def update_ordered_status(self):
		update_status = False
		for d in self.po_products:
			if d.planned_qty == d.ordered_qty:
				update_status = True

		if update_status and self.status != "Completed":
			self.status = "In Process"

	def update_requested_status(self):
		if not self.mr_products:
			return

		update_status = True
		for d in self.mr_products:
			if d.quantity != d.requested_qty:
				update_status = False

		if update_status:
			self.status = "Material Requested"

	def get_production_products(self):
		product_dict = {}

		for d in self.po_products:
			product_details = {
				"production_product": d.product_code,
				"use_multi_level_bom": d.include_exploded_products,
				"sales_order": d.sales_order,
				"sales_order_product": d.sales_order_product,
				"material_request": d.material_request,
				"material_request_product": d.material_request_product,
				"bom_no": d.bom_no,
				"description": d.description,
				"stock_uom": d.stock_uom,
				"company": self.company,
				"fg_warehouse": d.warehouse,
				"production_plan": self.name,
				"production_plan_product": d.name,
				"product_bundle_product": d.product_bundle_product,
				"planned_start_date": d.planned_start_date,
				"project": self.project,
			}

			if not product_details["project"] and d.sales_order:
				product_details["project"] = frappe.get_cached_value("Sales Order", d.sales_order, "project")

			if self.get_products_from == "Material Request":
				product_details.update({"qty": d.planned_qty})
				product_dict[(d.product_code, d.material_request_product, d.warehouse)] = product_details
			else:
				product_details.update(
					{
						"qty": flt(product_dict.get((d.product_code, d.sales_order, d.warehouse), {}).get("qty"))
						+ (flt(d.planned_qty) - flt(d.ordered_qty))
					}
				)
				product_dict[(d.product_code, d.sales_order, d.warehouse)] = product_details

		return product_dict

	@frappe.whitelist()
	def make_work_order(self):
		from erpnext.manufacturing.doctype.work_order.work_order import get_default_warehouse

		wo_list, po_list = [], []
		subcontracted_po = {}
		default_warehouses = get_default_warehouse()

		self.make_work_order_for_finished_goods(wo_list, default_warehouses)
		self.make_work_order_for_subassembly_products(wo_list, subcontracted_po, default_warehouses)
		self.make_subcontracted_purchase_order(subcontracted_po, po_list)
		self.show_list_created_message("Work Order", wo_list)
		self.show_list_created_message("Purchase Order", po_list)

		if not wo_list:
			frappe.msgprint(_("No Work Orders were created"))

	def make_work_order_for_finished_goods(self, wo_list, default_warehouses):
		products_data = self.get_production_products()

		for key, product in products_data.products():
			if self.sub_assembly_products:
				product["use_multi_level_bom"] = 0

			set_default_warehouses(product, default_warehouses)
			work_order = self.create_work_order(product)
			if work_order:
				wo_list.append(work_order)

	def make_work_order_for_subassembly_products(self, wo_list, subcontracted_po, default_warehouses):
		for row in self.sub_assembly_products:
			if row.type_of_manufacturing == "Subcontract":
				subcontracted_po.setdefault(row.supplier, []).append(row)
				continue

			if row.type_of_manufacturing == "Material Request":
				continue

			work_order_data = {
				"wip_warehouse": default_warehouses.get("wip_warehouse"),
				"fg_warehouse": default_warehouses.get("fg_warehouse"),
				"company": self.get("company"),
			}

			self.prepare_data_for_sub_assembly_products(row, work_order_data)
			work_order = self.create_work_order(work_order_data)
			if work_order:
				wo_list.append(work_order)

	def prepare_data_for_sub_assembly_products(self, row, wo_data):
		for field in [
			"production_product",
			"product_name",
			"qty",
			"fg_warehouse",
			"description",
			"bom_no",
			"stock_uom",
			"bom_level",
			"schedule_date",
		]:
			if row.get(field):
				wo_data[field] = row.get(field)

		wo_data.update(
			{
				"use_multi_level_bom": 0,
				"production_plan": self.name,
				"production_plan_sub_assembly_product": row.name,
			}
		)

	def make_subcontracted_purchase_order(self, subcontracted_po, purchase_orders):
		if not subcontracted_po:
			return

		for supplier, po_list in subcontracted_po.products():
			po = frappe.new_doc("Purchase Order")
			po.company = self.company
			po.supplier = supplier
			po.schedule_date = getdate(po_list[0].schedule_date) if po_list[0].schedule_date else nowdate()
			po.is_subcontracted = 1
			for row in po_list:
				po_data = {
					"fg_product": row.production_product,
					"warehouse": row.fg_warehouse,
					"production_plan_sub_assembly_product": row.name,
					"bom": row.bom_no,
					"production_plan": self.name,
					"fg_product_qty": row.qty,
				}

				for field in [
					"schedule_date",
					"qty",
					"description",
					"production_plan_product",
				]:
					po_data[field] = row.get(field)

				po.append("products", po_data)

			po.set_missing_values()
			po.flags.ignore_mandatory = True
			po.flags.ignore_validate = True
			po.insert()
			purchase_orders.append(po.name)

	def show_list_created_message(self, doctype, doc_list=None):
		if not doc_list:
			return

		frappe.flags.mute_messages = False
		if doc_list:
			doc_list = [get_link_to_form(doctype, p) for p in doc_list]
			msgprint(_("{0} created").format(comma_and(doc_list)))

	def create_work_order(self, product):
		from erpnext.manufacturing.doctype.work_order.work_order import OverProductionError

		if product.get("qty") <= 0:
			return

		wo = frappe.new_doc("Work Order")
		wo.update(product)
		wo.planned_start_date = product.get("planned_start_date") or product.get("schedule_date")

		if product.get("warehouse"):
			wo.fg_warehouse = product.get("warehouse")

		wo.set_work_order_operations()
		wo.set_required_products()

		try:
			wo.flags.ignore_mandatory = True
			wo.flags.ignore_validate = True
			wo.insert()
			return wo.name
		except OverProductionError:
			pass

	@frappe.whitelist()
	def make_material_request(self):
		"""Create Material Requests grouped by Sales Order and Material Request Type"""
		material_request_list = []
		material_request_map = {}

		for product in self.mr_products:
			product_doc = frappe.get_cached_doc("Product", product.product_code)

			material_request_type = product.material_request_type or product_doc.default_material_request_type

			# key for Sales Order:Material Request Type:Customer
			key = "{}:{}:{}".format(product.sales_order, material_request_type, product_doc.customer or "")
			schedule_date = add_days(nowdate(), cint(product_doc.lead_time_days))

			if not key in material_request_map:
				# make a new MR for the combination
				material_request_map[key] = frappe.new_doc("Material Request")
				material_request = material_request_map[key]
				material_request.update(
					{
						"transaction_date": nowdate(),
						"status": "Draft",
						"company": self.company,
						"material_request_type": material_request_type,
						"customer": product_doc.customer or "",
					}
				)
				material_request_list.append(material_request)
			else:
				material_request = material_request_map[key]

			# add product
			material_request.append(
				"products",
				{
					"product_code": product.product_code,
					"from_warehouse": product.from_warehouse,
					"qty": product.quantity,
					"schedule_date": schedule_date,
					"warehouse": product.warehouse,
					"sales_order": product.sales_order,
					"production_plan": self.name,
					"material_request_plan_product": product.name,
					"project": frappe.db.get_value("Sales Order", product.sales_order, "project")
					if product.sales_order
					else None,
				},
			)

		for material_request in material_request_list:
			# submit
			material_request.flags.ignore_permissions = 1
			material_request.run_method("set_missing_values")

			material_request.save()
			if self.get("submit_material_request"):
				material_request.submit()

		frappe.flags.mute_messages = False

		if material_request_list:
			material_request_list = [
				"""<a href="/app/Form/Material Request/{0}">{1}</a>""".format(m.name, m.name)
				for m in material_request_list
			]
			msgprint(_("{0} created").format(comma_and(material_request_list)))
		else:
			msgprint(_("No material request created"))

	@frappe.whitelist()
	def get_sub_assembly_products(self, manufacturing_type=None):
		"Fetch sub assembly products and optionally combine them."
		self.sub_assembly_products = []
		sub_assembly_products_store = []  # temporary store to process all subassembly products

		for row in self.po_products:
			if not row.product_code:
				frappe.throw(_("Row #{0}: Please select Product Code in Assembly Products").format(row.idx))

			bom_data = []

			warehouse = row.warehouse if self.skip_available_sub_assembly_product else None
			get_sub_assembly_products(row.bom_no, bom_data, row.planned_qty, self.company, warehouse=warehouse)
			self.set_sub_assembly_products_based_on_level(row, bom_data, manufacturing_type)
			sub_assembly_products_store.extend(bom_data)

		if self.combine_sub_products:
			# Combine subassembly products
			sub_assembly_products_store = self.combine_subassembly_products(sub_assembly_products_store)

		sub_assembly_products_store.sort(key=lambda d: d.bom_level, reverse=True)  # sort by bom level

		for idx, row in enumerate(sub_assembly_products_store):
			row.idx = idx + 1
			self.append("sub_assembly_products", row)

		self.set_default_supplier_for_subcontracting_order()

	def set_sub_assembly_products_based_on_level(self, row, bom_data, manufacturing_type=None):
		"Modify bom_data, set additional details."
		for data in bom_data:
			data.qty = data.stock_qty
			data.production_plan_product = row.name
			data.fg_warehouse = row.warehouse
			data.schedule_date = row.planned_start_date
			data.type_of_manufacturing = manufacturing_type or (
				"Subcontract" if data.is_sub_contracted_product else "In House"
			)

	def set_default_supplier_for_subcontracting_order(self):
		products = [
			d.production_product for d in self.sub_assembly_products if d.type_of_manufacturing == "Subcontract"
		]

		if not products:
			return

		default_supplier = frappe._dict(
			frappe.get_all(
				"Product Default",
				fields=["parent", "default_supplier"],
				filters={"parent": ("in", products), "default_supplier": ("is", "set")},
				as_list=1,
			)
		)

		if not default_supplier:
			return

		for row in self.sub_assembly_products:
			if row.type_of_manufacturing != "Subcontract":
				continue

			row.supplier = default_supplier.get(row.production_product)

	def combine_subassembly_products(self, sub_assembly_products_store):
		"Aggregate if same: Product, Warehouse, Inhouse/Outhouse Manu.g, BOM No."
		key_wise_data = {}
		for row in sub_assembly_products_store:
			key = (
				row.get("production_product"),
				row.get("fg_warehouse"),
				row.get("bom_no"),
				row.get("type_of_manufacturing"),
			)
			if key not in key_wise_data:
				# intialise (product, wh, bom no, man.g type) wise dict
				key_wise_data[key] = row
				continue

			existing_row = key_wise_data[key]
			if existing_row:
				# if row with same (product, wh, bom no, man.g type) key, merge
				existing_row.qty += flt(row.qty)
				existing_row.stock_qty += flt(row.stock_qty)
				existing_row.bom_level = max(existing_row.bom_level, row.bom_level)
				continue
			else:
				# add row with key
				key_wise_data[key] = row

		sub_assembly_products_store = [
			key_wise_data[key] for key in key_wise_data
		]  # unpack into single level list
		return sub_assembly_products_store

	def all_products_completed(self):
		all_products_produced = all(
			flt(d.planned_qty) - flt(d.produced_qty) < 0.000001 for d in self.po_products
		)
		if not all_products_produced:
			return False

		wo_status = frappe.get_all(
			"Work Order",
			filters={
				"production_plan": self.name,
				"status": ("not in", ["Closed", "Stopped"]),
				"docstatus": ("<", 2),
			},
			fields="status",
			pluck="status",
		)
		all_work_orders_completed = all(s == "Completed" for s in wo_status)
		return all_work_orders_completed


@frappe.whitelist()
def download_raw_materials(doc, warehouses=None):
	if isinstance(doc, str):
		doc = frappe._dict(json.loads(doc))

	product_list = [
		[
			"Product Code",
			"Product Name",
			"Description",
			"Stock UOM",
			"Warehouse",
			"Required Qty as per BOM",
			"Projected Qty",
			"Available Qty In Hand",
			"Ordered Qty",
			"Planned Qty",
			"Reserved Qty for Production",
			"Safety Stock",
			"Required Qty",
		]
	]

	doc.warehouse = None
	frappe.flags.show_qty_in_stock_uom = 1
	products = get_products_for_material_requests(
		doc, warehouses=warehouses, get_parent_warehouse_data=True
	)

	for d in products:
		product_list.append(
			[
				d.get("product_code"),
				d.get("product_name"),
				d.get("description"),
				d.get("stock_uom"),
				d.get("warehouse"),
				d.get("required_bom_qty"),
				d.get("projected_qty"),
				d.get("actual_qty"),
				d.get("ordered_qty"),
				d.get("planned_qty"),
				d.get("reserved_qty_for_production"),
				d.get("safety_stock"),
				d.get("quantity"),
			]
		)

		if not doc.get("for_warehouse"):
			row = {"product_code": d.get("product_code")}
			for bin_dict in get_bin_details(row, doc.company, all_warehouse=True):
				if d.get("warehouse") == bin_dict.get("warehouse"):
					continue

				product_list.append(
					[
						"",
						"",
						"",
						bin_dict.get("warehouse"),
						"",
						bin_dict.get("projected_qty", 0),
						bin_dict.get("actual_qty", 0),
						bin_dict.get("ordered_qty", 0),
						bin_dict.get("reserved_qty_for_production", 0),
					]
				)

	build_csv_response(product_list, doc.name)


def get_exploded_products(
	product_details, company, bom_no, include_non_stock_products, planned_qty=1, doc=None
):
	bei = frappe.qb.DocType("BOM Explosion Product")
	bom = frappe.qb.DocType("BOM")
	product = frappe.qb.DocType("Product")
	product_default = frappe.qb.DocType("Product Default")
	product_uom = frappe.qb.DocType("UOM Conversion Detail")

	data = (
		frappe.qb.from_(bei)
		.join(bom)
		.on(bom.name == bei.parent)
		.join(product)
		.on(product.name == bei.product_code)
		.left_join(product_default)
		.on((product_default.parent == product.name) & (product_default.company == company))
		.left_join(product_uom)
		.on((product.name == product_uom.parent) & (product_uom.uom == product.purchase_uom))
		.select(
			(IfNull(Sum(bei.stock_qty / IfNull(bom.quantity, 1)), 0) * planned_qty).as_("qty"),
			product.product_name,
			product.name.as_("product_code"),
			bei.description,
			bei.stock_uom,
			product.min_order_qty,
			bei.source_warehouse,
			product.default_material_request_type,
			product.min_order_qty,
			product_default.default_warehouse,
			product.purchase_uom,
			product_uom.conversion_factor,
			product.safety_stock,
		)
		.where(
			(bei.docstatus < 2)
			& (bom.name == bom_no)
			& (product.is_stock_product.isin([0, 1]) if include_non_stock_products else product.is_stock_product == 1)
		)
		.groupby(bei.product_code, bei.stock_uom)
	).run(as_dict=True)

	for d in data:
		if not d.conversion_factor and d.purchase_uom:
			d.conversion_factor = get_uom_conversion_factor(d.product_code, d.purchase_uom)
		product_details.setdefault(d.get("product_code"), d)

	return product_details


def get_uom_conversion_factor(product_code, uom):
	return frappe.db.get_value(
		"UOM Conversion Detail", {"parent": product_code, "uom": uom}, "conversion_factor"
	)


def get_subproducts(
	doc,
	data,
	product_details,
	bom_no,
	company,
	include_non_stock_products,
	include_subcontracted_products,
	parent_qty,
	planned_qty=1,
):
	bom_product = frappe.qb.DocType("BOM Product")
	bom = frappe.qb.DocType("BOM")
	product = frappe.qb.DocType("Product")
	product_default = frappe.qb.DocType("Product Default")
	product_uom = frappe.qb.DocType("UOM Conversion Detail")

	products = (
		frappe.qb.from_(bom_product)
		.join(bom)
		.on(bom.name == bom_product.parent)
		.join(product)
		.on(bom_product.product_code == product.name)
		.left_join(product_default)
		.on((product.name == product_default.parent) & (product_default.company == company))
		.left_join(product_uom)
		.on((product.name == product_uom.parent) & (product_uom.uom == product.purchase_uom))
		.select(
			bom_product.product_code,
			product.default_material_request_type,
			product.product_name,
			IfNull(parent_qty * Sum(bom_product.stock_qty / IfNull(bom.quantity, 1)) * planned_qty, 0).as_(
				"qty"
			),
			product.is_sub_contracted_product.as_("is_sub_contracted"),
			bom_product.source_warehouse,
			product.default_bom.as_("default_bom"),
			bom_product.description.as_("description"),
			bom_product.stock_uom.as_("stock_uom"),
			product.min_order_qty.as_("min_order_qty"),
			product.safety_stock.as_("safety_stock"),
			product_default.default_warehouse,
			product.purchase_uom,
			product_uom.conversion_factor,
		)
		.where(
			(bom.name == bom_no)
			& (bom_product.docstatus < 2)
			& (product.is_stock_product.isin([0, 1]) if include_non_stock_products else product.is_stock_product == 1)
		)
		.groupby(bom_product.product_code)
	).run(as_dict=True)

	for d in products:
		if not data.get("include_exploded_products") or not d.default_bom:
			if d.product_code in product_details:
				product_details[d.product_code].qty = product_details[d.product_code].qty + d.qty
			else:
				if not d.conversion_factor and d.purchase_uom:
					d.conversion_factor = get_uom_conversion_factor(d.product_code, d.purchase_uom)

				product_details[d.product_code] = d

		if data.get("include_exploded_products") and d.default_bom:
			if (
				d.default_material_request_type in ["Manufacture", "Purchase"] and not d.is_sub_contracted
			) or (d.is_sub_contracted and include_subcontracted_products):
				if d.qty > 0:
					get_subproducts(
						doc,
						data,
						product_details,
						d.default_bom,
						company,
						include_non_stock_products,
						include_subcontracted_products,
						d.qty,
					)
	return product_details


def get_material_request_products(
	row, sales_order, company, ignore_existing_ordered_qty, include_safety_stock, warehouse, bin_dict
):
	total_qty = row["qty"]

	required_qty = 0
	if ignore_existing_ordered_qty or bin_dict.get("projected_qty", 0) < 0:
		required_qty = total_qty
	elif total_qty > bin_dict.get("projected_qty", 0):
		required_qty = total_qty - bin_dict.get("projected_qty", 0)
	if required_qty > 0 and required_qty < row["min_order_qty"]:
		required_qty = row["min_order_qty"]
	product_group_defaults = get_product_group_defaults(row.product_code, company)

	if not row["purchase_uom"]:
		row["purchase_uom"] = row["stock_uom"]

	if row["purchase_uom"] != row["stock_uom"]:
		if not (row["conversion_factor"] or frappe.flags.show_qty_in_stock_uom):
			frappe.throw(
				_("UOM Conversion factor ({0} -> {1}) not found for product: {2}").format(
					row["purchase_uom"], row["stock_uom"], row.product_code
				)
			)

			required_qty = required_qty / row["conversion_factor"]

	if frappe.db.get_value("UOM", row["purchase_uom"], "must_be_whole_number"):
		required_qty = ceil(required_qty)

	if include_safety_stock:
		required_qty += flt(row["safety_stock"])

	product_details = frappe.get_cached_value(
		"Product", row.product_code, ["purchase_uom", "stock_uom"], as_dict=1
	)

	conversion_factor = 1.0
	if (
		row.get("default_material_request_type") == "Purchase"
		and product_details.purchase_uom
		and product_details.purchase_uom != product_details.stock_uom
	):
		conversion_factor = (
			get_conversion_factor(row.product_code, product_details.purchase_uom).get("conversion_factor") or 1.0
		)

	if required_qty > 0:
		return {
			"product_code": row.product_code,
			"product_name": row.product_name,
			"quantity": required_qty / conversion_factor,
			"conversion_factor": conversion_factor,
			"required_bom_qty": total_qty,
			"stock_uom": row.get("stock_uom"),
			"warehouse": warehouse
			or row.get("source_warehouse")
			or row.get("default_warehouse")
			or product_group_defaults.get("default_warehouse"),
			"safety_stock": row.safety_stock,
			"actual_qty": bin_dict.get("actual_qty", 0),
			"projected_qty": bin_dict.get("projected_qty", 0),
			"ordered_qty": bin_dict.get("ordered_qty", 0),
			"reserved_qty_for_production": bin_dict.get("reserved_qty_for_production", 0),
			"min_order_qty": row["min_order_qty"],
			"material_request_type": row.get("default_material_request_type"),
			"sales_order": sales_order,
			"description": row.get("description"),
			"uom": row.get("purchase_uom") or row.get("stock_uom"),
		}


def get_sales_orders(self):
	bom = frappe.qb.DocType("BOM")
	pi = frappe.qb.DocType("Packed Product")
	so = frappe.qb.DocType("Sales Order")
	so_product = frappe.qb.DocType("Sales Order Product")

	open_so_subquery1 = frappe.qb.from_(bom).select(bom.name).where(bom.is_active == 1)

	open_so_subquery2 = (
		frappe.qb.from_(pi)
		.select(pi.name)
		.where(
			(pi.parent == so.name)
			& (pi.parent_product == so_product.product_code)
			& (
				ExistsCriterion(
					frappe.qb.from_(bom).select(bom.name).where((bom.product == pi.product_code) & (bom.is_active == 1))
				)
			)
		)
	)

	open_so_query = (
		frappe.qb.from_(so)
		.from_(so_product)
		.select(so.name, so.transaction_date, so.customer, so.base_grand_total)
		.distinct()
		.where(
			(so_product.parent == so.name)
			& (so.docstatus == 1)
			& (so.status.notin(["Stopped", "Closed"]))
			& (so.company == self.company)
			& (so_product.qty > so_product.work_order_qty)
		)
	)

	date_field_mapper = {
		"from_date": self.from_date >= so.transaction_date,
		"to_date": self.to_date <= so.transaction_date,
		"from_delivery_date": self.from_delivery_date >= so_product.delivery_date,
		"to_delivery_date": self.to_delivery_date <= so_product.delivery_date,
	}

	for field, value in date_field_mapper.products():
		if self.get(field):
			open_so_query = open_so_query.where(value)

	for field in ("customer", "project", "sales_order_status"):
		if self.get(field):
			so_field = "status" if field == "sales_order_status" else field
			open_so_query = open_so_query.where(so[so_field] == self.get(field))

	if self.product_code and frappe.db.exists("Product", self.product_code):
		open_so_query = open_so_query.where(so_product.product_code == self.product_code)
		open_so_subquery1 = open_so_subquery1.where(
			self.get_bom_product_condition() or bom.product == so_product.product_code
		)

	open_so_query = open_so_query.where(
		(ExistsCriterion(open_so_subquery1) | ExistsCriterion(open_so_subquery2))
	)

	open_so = open_so_query.run(as_dict=True)

	return open_so


@frappe.whitelist()
def get_bin_details(row, company, for_warehouse=None, all_warehouse=False):
	if isinstance(row, str):
		row = frappe._dict(json.loads(row))

	bin = frappe.qb.DocType("Bin")
	wh = frappe.qb.DocType("Warehouse")

	subquery = frappe.qb.from_(wh).select(wh.name).where(wh.company == company)

	warehouse = ""
	if not all_warehouse:
		warehouse = for_warehouse or row.get("source_warehouse") or row.get("default_warehouse")

	if warehouse:
		lft, rgt = frappe.db.get_value("Warehouse", warehouse, ["lft", "rgt"])
		subquery = subquery.where((wh.lft >= lft) & (wh.rgt <= rgt) & (wh.name == bin.warehouse))

	query = (
		frappe.qb.from_(bin)
		.select(
			bin.warehouse,
			IfNull(Sum(bin.projected_qty), 0).as_("projected_qty"),
			IfNull(Sum(bin.actual_qty), 0).as_("actual_qty"),
			IfNull(Sum(bin.ordered_qty), 0).as_("ordered_qty"),
			IfNull(Sum(bin.reserved_qty_for_production), 0).as_("reserved_qty_for_production"),
			IfNull(Sum(bin.planned_qty), 0).as_("planned_qty"),
		)
		.where((bin.product_code == row["product_code"]) & (bin.warehouse.isin(subquery)))
		.groupby(bin.product_code, bin.warehouse)
	)

	return query.run(as_dict=True)


@frappe.whitelist()
def get_so_details(sales_order):
	return frappe.db.get_value(
		"Sales Order", sales_order, ["transaction_date", "customer", "grand_total"], as_dict=1
	)


def get_warehouse_list(warehouses):
	warehouse_list = []

	if isinstance(warehouses, str):
		warehouses = json.loads(warehouses)

	for row in warehouses:
		child_warehouses = frappe.db.get_descendants("Warehouse", row.get("warehouse"))
		if child_warehouses:
			warehouse_list.extend(child_warehouses)
		else:
			warehouse_list.append(row.get("warehouse"))

	return warehouse_list


@frappe.whitelist()
def get_products_for_material_requests(doc, warehouses=None, get_parent_warehouse_data=None):
	if isinstance(doc, str):
		doc = frappe._dict(json.loads(doc))

	if warehouses:
		warehouses = list(set(get_warehouse_list(warehouses)))

		if (
			doc.get("for_warehouse")
			and not get_parent_warehouse_data
			and doc.get("for_warehouse") in warehouses
		):
			warehouses.remove(doc.get("for_warehouse"))

	doc["mr_products"] = []

	po_products = doc.get("po_products") if doc.get("po_products") else doc.get("products")

	if doc.get("sub_assembly_products"):
		for sa_row in doc.sub_assembly_products:
			sa_row = frappe._dict(sa_row)
			if sa_row.type_of_manufacturing == "Material Request":
				po_products.append(
					frappe._dict(
						{
							"product_code": sa_row.production_product,
							"required_qty": sa_row.qty,
							"include_exploded_products": 0,
						}
					)
				)

	# Check for empty table or empty rows
	if not po_products or not [row.get("product_code") for row in po_products if row.get("product_code")]:
		frappe.throw(
			_("Products to Manufacture are required to pull the Raw Materials associated with it."),
			title=_("Products Required"),
		)

	company = doc.get("company")
	ignore_existing_ordered_qty = doc.get("ignore_existing_ordered_qty")
	include_safety_stock = doc.get("include_safety_stock")

	so_product_details = frappe._dict()

	sub_assembly_products = {}
	if doc.get("skip_available_sub_assembly_product"):
		for d in doc.get("sub_assembly_products"):
			sub_assembly_products.setdefault((d.get("production_product"), d.get("bom_no")), d.get("qty"))

	for data in po_products:
		if not data.get("include_exploded_products") and doc.get("sub_assembly_products"):
			data["include_exploded_products"] = 1

		planned_qty = data.get("required_qty") or data.get("planned_qty")
		ignore_existing_ordered_qty = (
			data.get("ignore_existing_ordered_qty") or ignore_existing_ordered_qty
		)
		warehouse = doc.get("for_warehouse")

		product_details = {}
		if data.get("bom") or data.get("bom_no"):
			if data.get("required_qty"):
				bom_no = data.get("bom")
				include_non_stock_products = 1
				include_subcontracted_products = 1 if data.get("include_exploded_products") else 0
			else:
				bom_no = data.get("bom_no")
				include_subcontracted_products = doc.get("include_subcontracted_products")
				include_non_stock_products = doc.get("include_non_stock_products")

			if not planned_qty:
				frappe.throw(_("For row {0}: Enter Planned Qty").format(data.get("idx")))

			if bom_no:
				if (
					data.get("include_exploded_products")
					and doc.get("sub_assembly_products")
					and doc.get("skip_available_sub_assembly_product")
				):
					product_details = get_raw_materials_of_sub_assembly_products(
						product_details,
						company,
						bom_no,
						include_non_stock_products,
						sub_assembly_products,
						planned_qty=planned_qty,
					)

				elif data.get("include_exploded_products") and include_subcontracted_products:
					# fetch exploded products from BOM
					product_details = get_exploded_products(
						product_details, company, bom_no, include_non_stock_products, planned_qty=planned_qty, doc=doc
					)
				else:
					product_details = get_subproducts(
						doc,
						data,
						product_details,
						bom_no,
						company,
						include_non_stock_products,
						include_subcontracted_products,
						1,
						planned_qty=planned_qty,
					)
		elif data.get("product_code"):
			product_master = frappe.get_doc("Product", data["product_code"]).as_dict()
			purchase_uom = product_master.purchase_uom or product_master.stock_uom
			conversion_factor = (
				get_uom_conversion_factor(product_master.name, purchase_uom) if product_master.purchase_uom else 1.0
			)

			product_details[product_master.name] = frappe._dict(
				{
					"product_name": product_master.product_name,
					"default_bom": doc.bom,
					"purchase_uom": purchase_uom,
					"default_warehouse": product_master.default_warehouse,
					"min_order_qty": product_master.min_order_qty,
					"default_material_request_type": product_master.default_material_request_type,
					"qty": planned_qty or 1,
					"is_sub_contracted": product_master.is_subcontracted_product,
					"product_code": product_master.name,
					"description": product_master.description,
					"stock_uom": product_master.stock_uom,
					"conversion_factor": conversion_factor,
					"safety_stock": product_master.safety_stock,
				}
			)

		sales_order = doc.get("sales_order")

		for product_code, details in product_details.products():
			so_product_details.setdefault(sales_order, frappe._dict())
			if product_code in so_product_details.get(sales_order, {}):
				so_product_details[sales_order][product_code]["qty"] = so_product_details[sales_order][product_code].get(
					"qty", 0
				) + flt(details.qty)
			else:
				so_product_details[sales_order][product_code] = details

	mr_products = []
	for sales_order, product_code in so_product_details.products():
		product_dict = so_product_details[sales_order]
		for details in product_dict.values():
			bin_dict = get_bin_details(details, doc.company, warehouse)
			bin_dict = bin_dict[0] if bin_dict else {}

			if details.qty > 0:
				products = get_material_request_products(
					details,
					sales_order,
					company,
					ignore_existing_ordered_qty,
					include_safety_stock,
					warehouse,
					bin_dict,
				)
				if products:
					mr_products.append(products)

	if (not ignore_existing_ordered_qty or get_parent_warehouse_data) and warehouses:
		new_mr_products = []
		for product in mr_products:
			get_materials_from_other_locations(product, warehouses, new_mr_products, company)

		mr_products = new_mr_products

	if not mr_products:
		to_enable = frappe.bold(_("Ignore Existing Projected Quantity"))
		warehouse = frappe.bold(doc.get("for_warehouse"))
		message = (
			_(
				"As there are sufficient raw materials, Material Request is not required for Warehouse {0}."
			).format(warehouse)
			+ "<br><br>"
		)
		message += _("If you still want to proceed, please enable {0}.").format(to_enable)

		frappe.msgprint(message, title=_("Note"))

	return mr_products


def get_materials_from_other_locations(product, warehouses, new_mr_products, company):
	from erpnext.stock.doctype.pick_list.pick_list import get_available_product_locations

	locations = get_available_product_locations(
		product.get("product_code"), warehouses, product.get("quantity"), company, ignore_validation=True
	)

	required_qty = product.get("quantity")
	# get available material by transferring to production warehouse
	for d in locations:
		if required_qty <= 0:
			return

		new_dict = copy.deepcopy(product)
		quantity = required_qty if d.get("qty") > required_qty else d.get("qty")

		new_dict.update(
			{
				"quantity": quantity,
				"material_request_type": "Material Transfer",
				"uom": new_dict.get("stock_uom"),  # internal transfer should be in stock UOM
				"from_warehouse": d.get("warehouse"),
			}
		)

		required_qty -= quantity
		new_mr_products.append(new_dict)

	# raise purchase request for remaining qty
	if required_qty:
		stock_uom, purchase_uom = frappe.db.get_value(
			"Product", product["product_code"], ["stock_uom", "purchase_uom"]
		)

		if purchase_uom != stock_uom and purchase_uom == product["uom"]:
			conversion_factor = get_uom_conversion_factor(product["product_code"], product["uom"])
			if not (conversion_factor or frappe.flags.show_qty_in_stock_uom):
				frappe.throw(
					_("UOM Conversion factor ({0} -> {1}) not found for product: {2}").format(
						purchase_uom, stock_uom, product["product_code"]
					)
				)

			required_qty = required_qty / conversion_factor

		if frappe.db.get_value("UOM", purchase_uom, "must_be_whole_number"):
			required_qty = ceil(required_qty)

		product["quantity"] = required_qty

		new_mr_products.append(product)


@frappe.whitelist()
def get_product_data(product_code):
	product_details = get_product_details(product_code)

	return {
		"bom_no": product_details.get("bom_no"),
		"stock_uom": product_details.get("stock_uom")
		# 		"description": product_details.get("description")
	}


def get_sub_assembly_products(bom_no, bom_data, to_produce_qty, company, warehouse=None, indent=0):
	data = get_bom_children(parent=bom_no)
	for d in data:
		if d.expandable:
			parent_product_code = frappe.get_cached_value("BOM", bom_no, "product")
			stock_qty = (d.stock_qty / d.parent_bom_qty) * flt(to_produce_qty)

			if warehouse:
				bin_dict = get_bin_details(d, company, for_warehouse=warehouse)

				if bin_dict and bin_dict[0].projected_qty > 0:
					if bin_dict[0].projected_qty > stock_qty:
						continue
					else:
						stock_qty = stock_qty - bin_dict[0].projected_qty

			bom_data.append(
				frappe._dict(
					{
						"parent_product_code": parent_product_code,
						"description": d.description,
						"production_product": d.product_code,
						"product_name": d.product_name,
						"stock_uom": d.stock_uom,
						"uom": d.stock_uom,
						"bom_no": d.value,
						"is_sub_contracted_product": d.is_sub_contracted_product,
						"bom_level": indent,
						"indent": indent,
						"stock_qty": stock_qty,
					}
				)
			)

			if d.value:
				get_sub_assembly_products(d.value, bom_data, stock_qty, company, warehouse, indent=indent + 1)


def set_default_warehouses(row, default_warehouses):
	for field in ["wip_warehouse", "fg_warehouse"]:
		if not row.get(field):
			row[field] = default_warehouses.get(field)


def get_reserved_qty_for_production_plan(product_code, warehouse):
	from erpnext.manufacturing.doctype.work_order.work_order import get_reserved_qty_for_production

	table = frappe.qb.DocType("Production Plan")
	child = frappe.qb.DocType("Material Request Plan Product")

	query = (
		frappe.qb.from_(table)
		.inner_join(child)
		.on(table.name == child.parent)
		.select(Sum(child.required_bom_qty * IfNull(child.conversion_factor, 1.0)))
		.where(
			(table.docstatus == 1)
			& (child.product_code == product_code)
			& (child.warehouse == warehouse)
			& (table.status.notin(["Completed", "Closed"]))
		)
	).run()

	if not query:
		return 0.0

	reserved_qty_for_production_plan = flt(query[0][0])

	reserved_qty_for_production = flt(
		get_reserved_qty_for_production(product_code, warehouse, check_production_plan=True)
	)

	if reserved_qty_for_production > reserved_qty_for_production_plan:
		return 0.0

	return reserved_qty_for_production_plan - reserved_qty_for_production


def get_raw_materials_of_sub_assembly_products(
	product_details, company, bom_no, include_non_stock_products, sub_assembly_products, planned_qty=1
):

	bei = frappe.qb.DocType("BOM Product")
	bom = frappe.qb.DocType("BOM")
	product = frappe.qb.DocType("Product")
	product_default = frappe.qb.DocType("Product Default")
	product_uom = frappe.qb.DocType("UOM Conversion Detail")

	products = (
		frappe.qb.from_(bei)
		.join(bom)
		.on(bom.name == bei.parent)
		.join(product)
		.on(product.name == bei.product_code)
		.left_join(product_default)
		.on((product_default.parent == product.name) & (product_default.company == company))
		.left_join(product_uom)
		.on((product.name == product_uom.parent) & (product_uom.uom == product.purchase_uom))
		.select(
			(IfNull(Sum(bei.stock_qty / IfNull(bom.quantity, 1)), 0) * planned_qty).as_("qty"),
			product.product_name,
			product.name.as_("product_code"),
			bei.description,
			bei.stock_uom,
			bei.bom_no,
			product.min_order_qty,
			bei.source_warehouse,
			product.default_material_request_type,
			product.min_order_qty,
			product_default.default_warehouse,
			product.purchase_uom,
			product_uom.conversion_factor,
			product.safety_stock,
		)
		.where(
			(bei.docstatus == 1)
			& (bom.name == bom_no)
			& (product.is_stock_product.isin([0, 1]) if include_non_stock_products else product.is_stock_product == 1)
		)
		.groupby(bei.product_code, bei.stock_uom)
	).run(as_dict=True)

	for product in products:
		key = (product.product_code, product.bom_no)
		if product.bom_no and key in sub_assembly_products:
			planned_qty = flt(sub_assembly_products[key])
			get_raw_materials_of_sub_assembly_products(
				product_details,
				company,
				product.bom_no,
				include_non_stock_products,
				sub_assembly_products,
				planned_qty=planned_qty,
			)
		else:
			if not product.conversion_factor and product.purchase_uom:
				product.conversion_factor = get_uom_conversion_factor(product.product_code, product.purchase_uom)

			product_details.setdefault(product.get("product_code"), product)

	return product_details
