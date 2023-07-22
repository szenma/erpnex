# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import copy
import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, floor, flt, nowdate

from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.utils import get_stock_balance


class PutawayRule(Document):
	def validate(self):
		self.validate_duplicate_rule()
		self.validate_warehouse_and_company()
		self.validate_capacity()
		self.validate_priority()
		self.set_stock_capacity()

	def validate_duplicate_rule(self):
		existing_rule = frappe.db.exists(
			"Putaway Rule", {"product_code": self.product_code, "warehouse": self.warehouse}
		)
		if existing_rule and existing_rule != self.name:
			frappe.throw(
				_("Putaway Rule already exists for Product {0} in Warehouse {1}.").format(
					frappe.bold(self.product_code), frappe.bold(self.warehouse)
				),
				title=_("Duplicate"),
			)

	def validate_priority(self):
		if self.priority < 1:
			frappe.throw(_("Priority cannot be lesser than 1."), title=_("Invalid Priority"))

	def validate_warehouse_and_company(self):
		company = frappe.db.get_value("Warehouse", self.warehouse, "company")
		if company != self.company:
			frappe.throw(
				_("Warehouse {0} does not belong to Company {1}.").format(
					frappe.bold(self.warehouse), frappe.bold(self.company)
				),
				title=_("Invalid Warehouse"),
			)

	def validate_capacity(self):
		stock_uom = frappe.db.get_value("Product", self.product_code, "stock_uom")
		balance_qty = get_stock_balance(self.product_code, self.warehouse, nowdate())

		if flt(self.stock_capacity) < flt(balance_qty):
			frappe.throw(
				_(
					"Warehouse Capacity for Product '{0}' must be greater than the existing stock level of {1} {2}."
				).format(self.product_code, frappe.bold(balance_qty), stock_uom),
				title=_("Insufficient Capacity"),
			)

		if not self.capacity:
			frappe.throw(_("Capacity must be greater than 0"), title=_("Invalid"))

	def set_stock_capacity(self):
		self.stock_capacity = (flt(self.conversion_factor) or 1) * flt(self.capacity)


@frappe.whitelist()
def get_available_putaway_capacity(rule):
	stock_capacity, product_code, warehouse = frappe.db.get_value(
		"Putaway Rule", rule, ["stock_capacity", "product_code", "warehouse"]
	)
	balance_qty = get_stock_balance(product_code, warehouse, nowdate())
	free_space = flt(stock_capacity) - flt(balance_qty)
	return free_space if free_space > 0 else 0


@frappe.whitelist()
def apply_putaway_rule(doctype, products, company, sync=None, purpose=None):
	"""Applies Putaway Rule on line products.

	products: List of Purchase Receipt/Stock Entry Products
	company: Company in the Purchase Receipt/Stock Entry
	doctype: Doctype to apply rule on
	purpose: Purpose of Stock Entry
	sync (optional): Sync with client side only for client side calls
	"""
	if isinstance(products, str):
		products = json.loads(products)

	products_not_accomodated, updated_table = [], []
	product_wise_rules = defaultdict(list)

	for product in products:
		if isinstance(product, dict):
			product = frappe._dict(product)

		source_warehouse = product.get("s_warehouse")
		serial_nos = get_serial_nos(product.get("serial_no"))
		product.conversion_factor = flt(product.conversion_factor) or 1.0
		pending_qty, product_code = flt(product.qty), product.product_code
		pending_stock_qty = flt(product.transfer_qty) if doctype == "Stock Entry" else flt(product.stock_qty)
		uom_must_be_whole_number = frappe.db.get_value("UOM", product.uom, "must_be_whole_number")

		if not pending_qty or not product_code:
			updated_table = add_row(product, pending_qty, source_warehouse or product.warehouse, updated_table)
			continue

		at_capacity, rules = get_ordered_putaway_rules(
			product_code, company, source_warehouse=source_warehouse
		)

		if not rules:
			warehouse = source_warehouse or product.get("warehouse")
			if at_capacity:
				# rules available, but no free space
				products_not_accomodated.append([product_code, pending_qty])
			else:
				updated_table = add_row(product, pending_qty, warehouse, updated_table)
			continue

		# maintain product/product-warehouse wise rules, to handle if product is entered twice
		# in the table, due to different price, etc.
		key = product_code
		if doctype == "Stock Entry" and purpose == "Material Transfer" and source_warehouse:
			key = (product_code, source_warehouse)

		if not product_wise_rules[key]:
			product_wise_rules[key] = rules

		for rule in product_wise_rules[key]:
			if pending_stock_qty > 0 and rule.free_space:
				stock_qty_to_allocate = (
					flt(rule.free_space) if pending_stock_qty >= flt(rule.free_space) else pending_stock_qty
				)
				qty_to_allocate = stock_qty_to_allocate / product.conversion_factor

				if uom_must_be_whole_number:
					qty_to_allocate = floor(qty_to_allocate)
					stock_qty_to_allocate = qty_to_allocate * product.conversion_factor

				if not qty_to_allocate:
					break

				updated_table = add_row(
					product, qty_to_allocate, rule.warehouse, updated_table, rule.name, serial_nos=serial_nos
				)

				pending_stock_qty -= stock_qty_to_allocate
				pending_qty -= qty_to_allocate
				rule["free_space"] -= stock_qty_to_allocate

				if not pending_stock_qty > 0:
					break

		# if pending qty after applying all rules, add row without warehouse
		if pending_stock_qty > 0:
			products_not_accomodated.append([product.product_code, pending_qty])

	if products_not_accomodated:
		show_unassigned_products_message(products_not_accomodated)

	if updated_table and _products_changed(products, updated_table, doctype):
		products[:] = updated_table
		frappe.msgprint(_("Applied putaway rules."), alert=True)

	if sync and json.loads(sync):  # sync with client side
		return products


def _products_changed(old, new, doctype: str) -> bool:
	"""Check if any products changed by application of putaway rules.

	If not, changing product table can have side effects since `name` products also changes.
	"""
	if len(old) != len(new):
		return True

	old = [frappe._dict(product) if isinstance(product, dict) else product for product in old]

	if doctype == "Stock Entry":
		compare_keys = ("product_code", "t_warehouse", "transfer_qty", "serial_no")
		sort_key = lambda product: (  # noqa
			product.product_code,
			cstr(product.t_warehouse),
			flt(product.transfer_qty),
			cstr(product.serial_no),
		)
	else:
		# purchase receipt / invoice
		compare_keys = ("product_code", "warehouse", "stock_qty", "received_qty", "serial_no")
		sort_key = lambda product: (  # noqa
			product.product_code,
			cstr(product.warehouse),
			flt(product.stock_qty),
			flt(product.received_qty),
			cstr(product.serial_no),
		)

	old_sorted = sorted(old, key=sort_key)
	new_sorted = sorted(new, key=sort_key)

	# Once sorted by all relevant keys both tables should align if they are same.
	for old_product, new_product in zip(old_sorted, new_sorted):
		for key in compare_keys:
			if old_product.get(key) != new_product.get(key):
				return True
	return False


def get_ordered_putaway_rules(product_code, company, source_warehouse=None):
	"""Returns an ordered list of putaway rules to apply on an product."""
	filters = {"product_code": product_code, "company": company, "disable": 0}
	if source_warehouse:
		filters.update({"warehouse": ["!=", source_warehouse]})

	rules = frappe.get_all(
		"Putaway Rule",
		fields=["name", "product_code", "stock_capacity", "priority", "warehouse"],
		filters=filters,
		order_by="priority asc, capacity desc",
	)

	if not rules:
		return False, None

	vacant_rules = []
	for rule in rules:
		balance_qty = get_stock_balance(rule.product_code, rule.warehouse, nowdate())
		free_space = flt(rule.stock_capacity) - flt(balance_qty)
		if free_space > 0:
			rule["free_space"] = free_space
			vacant_rules.append(rule)

	if not vacant_rules:
		# After iterating through rules, if no rules are left
		# then there is not enough space left in any rule
		return True, None

	vacant_rules = sorted(vacant_rules, key=lambda i: (i["priority"], -i["free_space"]))

	return False, vacant_rules


def add_row(product, to_allocate, warehouse, updated_table, rule=None, serial_nos=None):
	new_updated_table_row = copy.deepcopy(product)
	new_updated_table_row.idx = 1 if not updated_table else cint(updated_table[-1].idx) + 1
	new_updated_table_row.name = None
	new_updated_table_row.qty = to_allocate

	if product.doctype == "Stock Entry Detail":
		new_updated_table_row.t_warehouse = warehouse
		new_updated_table_row.transfer_qty = flt(to_allocate) * flt(
			new_updated_table_row.conversion_factor
		)
	else:
		new_updated_table_row.stock_qty = flt(to_allocate) * flt(new_updated_table_row.conversion_factor)
		new_updated_table_row.warehouse = warehouse
		new_updated_table_row.rejected_qty = 0
		new_updated_table_row.received_qty = to_allocate

	if rule:
		new_updated_table_row.putaway_rule = rule
	if serial_nos:
		new_updated_table_row.serial_no = get_serial_nos_to_allocate(serial_nos, to_allocate)

	updated_table.append(new_updated_table_row)
	return updated_table


def show_unassigned_products_message(products_not_accomodated):
	msg = _("The following Products, having Putaway Rules, could not be accomodated:") + "<br><br>"
	formatted_product_rows = ""

	for entry in products_not_accomodated:
		product_link = frappe.utils.get_link_to_form("Product", entry[0])
		formatted_product_rows += """
			<td>{0}</td>
			<td>{1}</td>
		</tr>""".format(
			product_link, frappe.bold(entry[1])
		)

	msg += """
		<table class="table">
			<thead>
				<td>{0}</td>
				<td>{1}</td>
			</thead>
			{2}
		</table>
	""".format(
		_("Product"), _("Unassigned Qty"), formatted_product_rows
	)

	frappe.msgprint(msg, title=_("Insufficient Capacity"), is_minimizable=True, wide=True)


def get_serial_nos_to_allocate(serial_nos, to_allocate):
	if serial_nos:
		allocated_serial_nos = serial_nos[0 : cint(to_allocate)]
		serial_nos[:] = serial_nos[cint(to_allocate) :]  # pop out allocated serial nos and modify list
		return "\n".join(allocated_serial_nos) if allocated_serial_nos else ""
	else:
		return ""
