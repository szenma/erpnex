# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt

from erpnext.buying.doctype.purchase_order.purchase_order import is_subcontracting_order_created
from erpnext.controllers.subcontracting_controller import SubcontractingController
from erpnext.stock.stock_balance import get_ordered_qty, update_bin_qty
from erpnext.stock.utils import get_bin


class SubcontractingOrder(SubcontractingController):
	def before_validate(self):
		super(SubcontractingOrder, self).before_validate()

	def validate(self):
		super(SubcontractingOrder, self).validate()
		self.validate_purchase_order_for_subcontracting()
		self.validate_products()
		self.validate_service_products()
		self.validate_supplied_products()
		self.set_missing_values()
		self.reset_default_field_value("set_warehouse", "products", "warehouse")

	def on_submit(self):
		self.update_ordered_qty_for_subcontracting()
		self.update_reserved_qty_for_subcontracting()
		self.update_status()

	def on_cancel(self):
		self.update_ordered_qty_for_subcontracting()
		self.update_reserved_qty_for_subcontracting()
		self.update_status()

	def validate_purchase_order_for_subcontracting(self):
		if self.purchase_order:
			if is_subcontracting_order_created(self.purchase_order):
				frappe.throw(
					_(
						"Only one Subcontracting Order can be created against a Purchase Order, cancel the existing Subcontracting Order to create a new one."
					)
				)

			po = frappe.get_doc("Purchase Order", self.purchase_order)

			if not po.is_subcontracted:
				frappe.throw(_("Please select a valid Purchase Order that is configured for Subcontracting."))

			if po.is_old_subcontracting_flow:
				frappe.throw(_("Please select a valid Purchase Order that has Service Products."))

			if po.docstatus != 1:
				msg = f"Please submit Purchase Order {po.name} before proceeding."
				frappe.throw(_(msg))

			if po.per_received == 100:
				msg = f"Cannot create more Subcontracting Orders against the Purchase Order {po.name}."
				frappe.throw(_(msg))
		else:
			self.service_products = self.products = self.supplied_products = None
			frappe.throw(_("Please select a Subcontracting Purchase Order."))

	def validate_service_products(self):
		for product in self.service_products:
			if frappe.get_value("Product", product.product_code, "is_stock_product"):
				msg = f"Service Product {product.product_name} must be a non-stock product."
				frappe.throw(_(msg))

	def validate_supplied_products(self):
		if self.supplier_warehouse:
			for product in self.supplied_products:
				if self.supplier_warehouse == product.reserve_warehouse:
					msg = f"Reserve Warehouse must be different from Supplier Warehouse for Supplied Product {product.main_product_code}."
					frappe.throw(_(msg))

	def set_missing_values(self):
		self.calculate_additional_costs()
		self.calculate_service_costs()
		self.calculate_supplied_products_qty_and_amount()
		self.calculate_products_qty_and_amount()

	def calculate_service_costs(self):
		for idx, product in enumerate(self.get("service_products")):
			self.products[idx].service_cost_per_qty = product.amount / self.products[idx].qty

	def calculate_supplied_products_qty_and_amount(self):
		for product in self.get("products"):
			bom = frappe.get_doc("BOM", product.bom)
			rm_cost = sum(flt(rm_product.amount) for rm_product in bom.products)
			product.rm_cost_per_qty = rm_cost / flt(bom.quantity)

	def calculate_products_qty_and_amount(self):
		total_qty = total = 0
		for product in self.products:
			product.rate = product.rm_cost_per_qty + product.service_cost_per_qty + flt(product.additional_cost_per_qty)
			product.amount = product.qty * product.rate
			total_qty += flt(product.qty)
			total += flt(product.amount)
		else:
			self.total_qty = total_qty
			self.total = total

	def update_ordered_qty_for_subcontracting(self, sco_product_rows=None):
		product_wh_list = []
		for product in self.get("products"):
			if (
				(not sco_product_rows or product.name in sco_product_rows)
				and [product.product_code, product.warehouse] not in product_wh_list
				and frappe.get_cached_value("Product", product.product_code, "is_stock_product")
				and product.warehouse
			):
				product_wh_list.append([product.product_code, product.warehouse])
		for product_code, warehouse in product_wh_list:
			update_bin_qty(product_code, warehouse, {"ordered_qty": get_ordered_qty(product_code, warehouse)})

	def update_reserved_qty_for_subcontracting(self):
		for product in self.supplied_products:
			if product.rm_product_code:
				stock_bin = get_bin(product.rm_product_code, product.reserve_warehouse)
				stock_bin.update_reserved_qty_for_sub_contracting()

	def populate_products_table(self):
		products = []

		for si in self.service_products:
			if si.fg_product:
				product = frappe.get_doc("Product", si.fg_product)
				bom = frappe.db.get_value("BOM", {"product": product.product_code, "is_active": 1, "is_default": 1})

				products.append(
					{
						"product_code": product.product_code,
						"product_name": product.product_name,
						"schedule_date": self.schedule_date,
						"description": product.description,
						"qty": si.fg_product_qty,
						"stock_uom": product.stock_uom,
						"bom": bom,
					},
				)
			else:
				frappe.throw(
					_("Please select Finished Good Product for Service Product {0}").format(
						si.product_name or si.product_code
					)
				)
		else:
			for product in products:
				self.append("products", product)
			else:
				self.set_missing_values()

	def update_status(self, status=None, update_modified=True):
		if self.docstatus >= 1 and not status:
			if self.docstatus == 1:
				if self.status == "Draft":
					status = "Open"
				elif self.per_received >= 100:
					status = "Completed"
				elif self.per_received > 0 and self.per_received < 100:
					status = "Partially Received"
					for product in self.supplied_products:
						if not product.returned_qty or (product.supplied_qty - product.consumed_qty - product.returned_qty) > 0:
							break
					else:
						status = "Closed"
				else:
					total_required_qty = total_supplied_qty = 0
					for product in self.supplied_products:
						total_required_qty += product.required_qty
						total_supplied_qty += flt(product.supplied_qty)
					if total_supplied_qty:
						status = "Partial Material Transferred"
						if total_supplied_qty >= total_required_qty:
							status = "Material Transferred"
					else:
						status = "Open"
			elif self.docstatus == 2:
				status = "Cancelled"

		if status:
			frappe.db.set_value(
				"Subcontracting Order", self.name, "status", status, update_modified=update_modified
			)


@frappe.whitelist()
def make_subcontracting_receipt(source_name, target_doc=None):
	return get_mapped_subcontracting_receipt(source_name, target_doc)


def get_mapped_subcontracting_receipt(source_name, target_doc=None):
	def update_product(obj, target, source_parent):
		target.qty = flt(obj.qty) - flt(obj.received_qty)
		target.amount = (flt(obj.qty) - flt(obj.received_qty)) * flt(obj.rate)

	target_doc = get_mapped_doc(
		"Subcontracting Order",
		source_name,
		{
			"Subcontracting Order": {
				"doctype": "Subcontracting Receipt",
				"field_map": {"supplier_warehouse": "supplier_warehouse"},
				"validation": {
					"docstatus": ["=", 1],
				},
			},
			"Subcontracting Order Product": {
				"doctype": "Subcontracting Receipt Product",
				"field_map": {
					"name": "subcontracting_order_product",
					"parent": "subcontracting_order",
					"bom": "bom",
				},
				"postprocess": update_product,
				"condition": lambda doc: abs(doc.received_qty) < abs(doc.qty),
			},
		},
		target_doc,
	)

	return target_doc


@frappe.whitelist()
def update_subcontracting_order_status(sco):
	if isinstance(sco, str):
		sco = frappe.get_doc("Subcontracting Order", sco)

	sco.update_status()
