# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
from collections import OrderedDict, defaultdict
from itertools import groupby
from typing import Dict, List

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import map_child_doc
from frappe.query_builder import Case
from frappe.query_builder.custom import GROUP_CONCAT
from frappe.query_builder.functions import Coalesce, IfNull, Locate, Replace, Sum
from frappe.utils import cint, floor, flt, today
from frappe.utils.nestedset import get_descendants_of

from erpnext.selling.doctype.sales_order.sales_order import (
	make_delivery_note as create_delivery_note_from_sales_order,
)
from erpnext.stock.get_product_details import get_conversion_factor

# TODO: Prioritize SO or WO group warehouse


class PickList(Document):
	def validate(self):
		self.validate_for_qty()

	def before_save(self):
		self.update_status()
		self.set_product_locations()

		# set percentage picked in SO
		for location in self.get("locations"):
			if (
				location.sales_order
				and frappe.db.get_value("Sales Order", location.sales_order, "per_picked", cache=True) == 100
			):
				frappe.throw(
					_("Row #{}: product {} has been picked already.").format(location.idx, location.product_code)
				)

	def before_submit(self):
		self.validate_picked_products()

	def validate_picked_products(self):
		for product in self.locations:
			if self.scan_mode and product.picked_qty < product.stock_qty:
				frappe.throw(
					_(
						"Row {0} picked quantity is less than the required quantity, additional {1} {2} required."
					).format(product.idx, product.stock_qty - product.picked_qty, product.stock_uom),
					title=_("Pick List Incomplete"),
				)

			if not self.scan_mode and product.picked_qty == 0:
				# if the user has not entered any picked qty, set it to stock_qty, before submit
				product.picked_qty = product.stock_qty

			if not frappe.get_cached_value("Product", product.product_code, "has_serial_no"):
				continue

			if not product.serial_no:
				frappe.throw(
					_("Row #{0}: {1} does not have any available serial numbers in {2}").format(
						frappe.bold(product.idx), frappe.bold(product.product_code), frappe.bold(product.warehouse)
					),
					title=_("Serial Nos Required"),
				)

			if len(product.serial_no.split("\n")) != product.picked_qty:
				frappe.throw(
					_(
						"For product {0} at row {1}, count of serial numbers does not match with the picked quantity"
					).format(frappe.bold(product.product_code), frappe.bold(product.idx)),
					title=_("Quantity Mismatch"),
				)

	def on_submit(self):
		self.update_status()
		self.update_bundle_picked_qty()
		self.update_reference_qty()
		self.update_sales_order_picking_status()

	def on_cancel(self):
		self.update_status()
		self.update_bundle_picked_qty()
		self.update_reference_qty()
		self.update_sales_order_picking_status()

	def update_status(self, status=None):
		if not status:
			if self.docstatus == 0:
				status = "Draft"
			elif self.docstatus == 1:
				if target_document_exists(self.name, self.purpose):
					status = "Completed"
				else:
					status = "Open"
			elif self.docstatus == 2:
				status = "Cancelled"

		if status:
			self.db_set("status", status)

	def update_reference_qty(self):
		packed_products = []
		so_products = []

		for product in self.locations:
			if product.product_bundle_product:
				packed_products.append(product.sales_order_product)
			elif product.sales_order_product:
				so_products.append(product.sales_order_product)

		if packed_products:
			self.update_packed_products_qty(packed_products)

		if so_products:
			self.update_sales_order_product_qty(so_products)

	def update_packed_products_qty(self, packed_products):
		picked_products = get_picked_products_qty(packed_products)
		self.validate_picked_qty(picked_products)

		picked_qty = frappe._dict()
		for d in picked_products:
			picked_qty[d.sales_order_product] = d.picked_qty

		for packed_product in packed_products:
			frappe.db.set_value(
				"Packed Product",
				packed_product,
				"picked_qty",
				flt(picked_qty.get(packed_product)),
				update_modified=False,
			)

	def update_sales_order_product_qty(self, so_products):
		picked_products = get_picked_products_qty(so_products)
		self.validate_picked_qty(picked_products)

		picked_qty = frappe._dict()
		for d in picked_products:
			picked_qty[d.sales_order_product] = d.picked_qty

		for so_product in so_products:
			frappe.db.set_value(
				"Sales Order Product",
				so_product,
				"picked_qty",
				flt(picked_qty.get(so_product)),
				update_modified=False,
			)

	def update_sales_order_picking_status(self) -> None:
		sales_orders = []
		for row in self.locations:
			if row.sales_order and row.sales_order not in sales_orders:
				sales_orders.append(row.sales_order)

		for sales_order in sales_orders:
			frappe.get_doc("Sales Order", sales_order, for_update=True).update_picking_status()

	def validate_picked_qty(self, data):
		over_delivery_receipt_allowance = 100 + flt(
			frappe.db.get_single_value("Stock Settings", "over_delivery_receipt_allowance")
		)

		for row in data:
			if (row.picked_qty / row.stock_qty) * 100 > over_delivery_receipt_allowance:
				frappe.throw(
					_(
						"You are picking more than required quantity for the product {0}. Check if there is any other pick list created for the sales order {1}."
					).format(row.product_code, row.sales_order)
				)

	@frappe.whitelist()
	def set_product_locations(self, save=False):
		self.validate_for_qty()
		products = self.aggregate_product_qty()
		picked_products_details = self.get_picked_products_details(products)
		self.product_location_map = frappe._dict()

		from_warehouses = None
		if self.parent_warehouse:
			from_warehouses = get_descendants_of("Warehouse", self.parent_warehouse)

		# Create replica before resetting, to handle empty table on update after submit.
		locations_replica = self.get("locations")

		# reset
		self.delete_key("locations")
		updated_locations = frappe._dict()
		for product_doc in products:
			product_code = product_doc.product_code

			self.product_location_map.setdefault(
				product_code,
				get_available_product_locations(
					product_code,
					from_warehouses,
					self.product_count_map.get(product_code),
					self.company,
					picked_product_details=picked_products_details.get(product_code),
				),
			)

			locations = get_products_with_location_and_quantity(
				product_doc, self.product_location_map, self.docstatus
			)

			product_doc.idx = None
			product_doc.name = None

			for row in locations:
				location = product_doc.as_dict()
				location.update(row)
				key = (
					location.product_code,
					location.warehouse,
					location.uom,
					location.batch_no,
					location.serial_no,
					location.sales_order_product or location.material_request_product,
				)

				if key not in updated_locations:
					updated_locations.setdefault(key, location)
				else:
					updated_locations[key].qty += location.qty
					updated_locations[key].stock_qty += location.stock_qty

		for location in updated_locations.values():
			if location.picked_qty > location.stock_qty:
				location.picked_qty = location.stock_qty

			self.append("locations", location)

		# If table is empty on update after submit, set stock_qty, picked_qty to 0 so that indicator is red
		# and give feedback to the user. This is to avoid empty Pick Lists.
		if not self.get("locations") and self.docstatus == 1:
			for location in locations_replica:
				location.stock_qty = 0
				location.picked_qty = 0
				self.append("locations", location)
			frappe.msgprint(
				_(
					"Please Restock Products and Update the Pick List to continue. To discontinue, cancel the Pick List."
				),
				title=_("Out of Stock"),
				indicator="red",
			)

		if save:
			self.save()

	def aggregate_product_qty(self):
		locations = self.get("locations")
		self.product_count_map = {}
		# aggregate qty for same product
		product_map = OrderedDict()
		for product in locations:
			if not product.product_code:
				frappe.throw("Row #{0}: Product Code is Mandatory".format(product.idx))
			if not cint(
				frappe.get_cached_value("Product", product.product_code, "is_stock_product")
			) and not frappe.db.exists("Product Bundle", {"new_product_code": product.product_code}):
				continue
			product_code = product.product_code
			reference = product.sales_order_product or product.material_request_product
			key = (product_code, product.uom, product.warehouse, product.batch_no, reference)

			product.idx = None
			product.name = None

			if product_map.get(key):
				product_map[key].qty += product.qty
				product_map[key].stock_qty += flt(product.stock_qty, product.precision("stock_qty"))
			else:
				product_map[key] = product

			# maintain count of each product (useful to limit get query)
			self.product_count_map.setdefault(product_code, 0)
			self.product_count_map[product_code] += flt(product.stock_qty, product.precision("stock_qty"))

		return product_map.values()

	def validate_for_qty(self):
		if self.purpose == "Material Transfer for Manufacture" and (
			self.for_qty is None or self.for_qty == 0
		):
			frappe.throw(_("Qty of Finished Goods Product should be greater than 0."))

	def before_print(self, settings=None):
		if self.group_same_products:
			self.group_similar_products()

	def group_similar_products(self):
		group_product_qty = defaultdict(float)
		group_picked_qty = defaultdict(float)

		for product in self.locations:
			group_product_qty[(product.product_code, product.warehouse)] += product.qty
			group_picked_qty[(product.product_code, product.warehouse)] += product.picked_qty

		duplicate_list = []
		for product in self.locations:
			if (product.product_code, product.warehouse) in group_product_qty:
				product.qty = group_product_qty[(product.product_code, product.warehouse)]
				product.picked_qty = group_picked_qty[(product.product_code, product.warehouse)]
				product.stock_qty = group_product_qty[(product.product_code, product.warehouse)]
				del group_product_qty[(product.product_code, product.warehouse)]
			else:
				duplicate_list.append(product)

		for product in duplicate_list:
			self.remove(product)

		for idx, product in enumerate(self.locations, start=1):
			product.idx = idx

	def update_bundle_picked_qty(self):
		product_bundles = self._get_product_bundles()
		product_bundle_qty_map = self._get_product_bundle_qty_map(product_bundles.values())

		for so_row, product_code in product_bundles.products():
			picked_qty = self._compute_picked_qty_for_bundle(so_row, product_bundle_qty_map[product_code])
			product_table = "Sales Order Product"
			already_picked = frappe.db.get_value(product_table, so_row, "picked_qty", for_update=True)
			frappe.db.set_value(
				product_table,
				so_row,
				"picked_qty",
				already_picked + (picked_qty * (1 if self.docstatus == 1 else -1)),
			)

	def get_picked_products_details(self, products):
		picked_products = frappe._dict()

		if products:
			pi = frappe.qb.DocType("Pick List")
			pi_product = frappe.qb.DocType("Pick List Product")
			query = (
				frappe.qb.from_(pi)
				.inner_join(pi_product)
				.on(pi.name == pi_product.parent)
				.select(
					pi_product.product_code,
					pi_product.warehouse,
					pi_product.batch_no,
					Sum(Case().when(pi_product.picked_qty > 0, pi_product.picked_qty).else_(pi_product.stock_qty)).as_(
						"picked_qty"
					),
					Replace(GROUP_CONCAT(pi_product.serial_no), ",", "\n").as_("serial_no"),
				)
				.where(
					(pi_product.product_code.isin([x.product_code for x in products]))
					& ((pi_product.picked_qty > 0) | (pi_product.stock_qty > 0))
					& (pi.status != "Completed")
					& (pi.status != "Cancelled")
					& (pi_product.docstatus != 2)
				)
				.groupby(
					pi_product.product_code,
					pi_product.warehouse,
					pi_product.batch_no,
				)
			)

			if self.name:
				query = query.where(pi_product.parent != self.name)

			products_data = query.run(as_dict=True)

			for product_data in products_data:
				key = (product_data.warehouse, product_data.batch_no) if product_data.batch_no else product_data.warehouse
				serial_no = [x for x in product_data.serial_no.split("\n") if x] if product_data.serial_no else None
				data = {"picked_qty": product_data.picked_qty}
				if serial_no:
					data["serial_no"] = serial_no
				if product_data.product_code not in picked_products:
					picked_products[product_data.product_code] = {key: data}
				else:
					picked_products[product_data.product_code][key] = data

		return picked_products

	def _get_product_bundles(self) -> Dict[str, str]:
		# Dict[so_product_row: product_code]
		product_bundles = {}
		for product in self.locations:
			if not product.product_bundle_product:
				continue
			product_bundles[product.product_bundle_product] = frappe.db.get_value(
				"Sales Order Product",
				product.product_bundle_product,
				"product_code",
			)
		return product_bundles

	def _get_product_bundle_qty_map(self, bundles: List[str]) -> Dict[str, Dict[str, float]]:
		# bundle_product_code: Dict[component, qty]
		product_bundle_qty_map = {}
		for bundle_product_code in bundles:
			bundle = frappe.get_last_doc("Product Bundle", {"new_product_code": bundle_product_code})
			product_bundle_qty_map[bundle_product_code] = {product.product_code: product.qty for product in bundle.products}
		return product_bundle_qty_map

	def _compute_picked_qty_for_bundle(self, bundle_row, bundle_products) -> int:
		"""Compute how many full bundles can be created from picked products."""
		precision = frappe.get_precision("Stock Ledger Entry", "qty_after_transaction")

		possible_bundles = []
		for product in self.locations:
			if product.product_bundle_product != bundle_row:
				continue

			if qty_in_bundle := bundle_products.get(product.product_code):
				possible_bundles.append(product.picked_qty / qty_in_bundle)
			else:
				possible_bundles.append(0)
		return int(flt(min(possible_bundles), precision or 6))


def update_pick_list_status(pick_list):
	if pick_list:
		doc = frappe.get_doc("Pick List", pick_list)
		doc.run_method("update_status")


def get_picked_products_qty(products) -> List[Dict]:
	pi_product = frappe.qb.DocType("Pick List Product")
	return (
		frappe.qb.from_(pi_product)
		.select(
			pi_product.sales_order_product,
			pi_product.product_code,
			pi_product.sales_order,
			Sum(pi_product.stock_qty).as_("stock_qty"),
			Sum(pi_product.picked_qty).as_("picked_qty"),
		)
		.where((pi_product.docstatus == 1) & (pi_product.sales_order_product.isin(products)))
		.groupby(
			pi_product.sales_order_product,
			pi_product.sales_order,
		)
		.for_update()
	).run(as_dict=True)


def validate_product_locations(pick_list):
	if not pick_list.locations:
		frappe.throw(_("Add products in the Product Locations table"))


def get_products_with_location_and_quantity(product_doc, product_location_map, docstatus):
	available_locations = product_location_map.get(product_doc.product_code)
	locations = []

	# if stock qty is zero on submitted entry, show positive remaining qty to recalculate in case of restock.
	remaining_stock_qty = (
		product_doc.qty if (docstatus == 1 and product_doc.stock_qty == 0) else product_doc.stock_qty
	)

	while flt(remaining_stock_qty) > 0 and available_locations:
		product_location = available_locations.pop(0)
		product_location = frappe._dict(product_location)

		stock_qty = (
			remaining_stock_qty if product_location.qty >= remaining_stock_qty else product_location.qty
		)
		qty = stock_qty / (product_doc.conversion_factor or 1)

		uom_must_be_whole_number = frappe.get_cached_value("UOM", product_doc.uom, "must_be_whole_number")
		if uom_must_be_whole_number:
			qty = floor(qty)
			stock_qty = qty * product_doc.conversion_factor
			if not stock_qty:
				break

		serial_nos = None
		if product_location.serial_no:
			serial_nos = "\n".join(product_location.serial_no[0 : cint(stock_qty)])

		locations.append(
			frappe._dict(
				{
					"qty": qty,
					"stock_qty": stock_qty,
					"warehouse": product_location.warehouse,
					"serial_no": serial_nos,
					"batch_no": product_location.batch_no,
				}
			)
		)

		remaining_stock_qty -= stock_qty

		qty_diff = product_location.qty - stock_qty
		# if extra quantity is available push current warehouse to available locations
		if qty_diff > 0:
			product_location.qty = qty_diff
			if product_location.serial_no:
				# set remaining serial numbers
				product_location.serial_no = product_location.serial_no[-int(qty_diff) :]
			available_locations = [product_location] + available_locations

	# update available locations for the product
	product_location_map[product_doc.product_code] = available_locations
	return locations


def get_available_product_locations(
	product_code,
	from_warehouses,
	required_qty,
	company,
	ignore_validation=False,
	picked_product_details=None,
):
	locations = []
	total_picked_qty = (
		sum([v.get("picked_qty") for k, v in picked_product_details.products()]) if picked_product_details else 0
	)
	has_serial_no = frappe.get_cached_value("Product", product_code, "has_serial_no")
	has_batch_no = frappe.get_cached_value("Product", product_code, "has_batch_no")

	if has_batch_no and has_serial_no:
		locations = get_available_product_locations_for_serial_and_batched_product(
			product_code, from_warehouses, required_qty, company, total_picked_qty
		)
	elif has_serial_no:
		locations = get_available_product_locations_for_serialized_product(
			product_code, from_warehouses, required_qty, company, total_picked_qty
		)
	elif has_batch_no:
		locations = get_available_product_locations_for_batched_product(
			product_code, from_warehouses, required_qty, company, total_picked_qty
		)
	else:
		locations = get_available_product_locations_for_other_product(
			product_code, from_warehouses, required_qty, company, total_picked_qty
		)

	total_qty_available = sum(location.get("qty") for location in locations)
	remaining_qty = required_qty - total_qty_available

	if remaining_qty > 0 and not ignore_validation:
		frappe.msgprint(
			_("{0} units of Product {1} is not available.").format(
				remaining_qty, frappe.get_desk_link("Product", product_code)
			),
			title=_("Insufficient Stock"),
		)

	if picked_product_details:
		for location in list(locations):
			key = (
				(location["warehouse"], location["batch_no"])
				if location.get("batch_no")
				else location["warehouse"]
			)

			if key in picked_product_details:
				picked_detail = picked_product_details[key]

				if picked_detail.get("serial_no") and location.get("serial_no"):
					location["serial_no"] = list(
						set(location["serial_no"]).difference(set(picked_detail["serial_no"]))
					)
					location["qty"] = len(location["serial_no"])
				else:
					location["qty"] -= picked_detail.get("picked_qty")

			if location["qty"] < 1:
				locations.remove(location)

		total_qty_available = sum(location.get("qty") for location in locations)
		remaining_qty = required_qty - total_qty_available

		if remaining_qty > 0 and not ignore_validation:
			frappe.msgprint(
				_("{0} units of Product {1} is picked in another Pick List.").format(
					remaining_qty, frappe.get_desk_link("Product", product_code)
				),
				title=_("Already Picked"),
			)

	return locations


def get_available_product_locations_for_serialized_product(
	product_code, from_warehouses, required_qty, company, total_picked_qty=0
):
	sn = frappe.qb.DocType("Serial No")
	query = (
		frappe.qb.from_(sn)
		.select(sn.name, sn.warehouse)
		.where((sn.product_code == product_code) & (sn.company == company))
		.orderby(sn.purchase_date)
		.limit(cint(required_qty + total_picked_qty))
	)

	if from_warehouses:
		query = query.where(sn.warehouse.isin(from_warehouses))
	else:
		query = query.where(Coalesce(sn.warehouse, "") != "")

	serial_nos = query.run(as_list=True)

	warehouse_serial_nos_map = frappe._dict()
	for serial_no, warehouse in serial_nos:
		warehouse_serial_nos_map.setdefault(warehouse, []).append(serial_no)

	locations = []
	for warehouse, serial_nos in warehouse_serial_nos_map.products():
		locations.append({"qty": len(serial_nos), "warehouse": warehouse, "serial_no": serial_nos})

	return locations


def get_available_product_locations_for_batched_product(
	product_code, from_warehouses, required_qty, company, total_picked_qty=0
):
	sle = frappe.qb.DocType("Stock Ledger Entry")
	batch = frappe.qb.DocType("Batch")

	query = (
		frappe.qb.from_(sle)
		.from_(batch)
		.select(sle.warehouse, sle.batch_no, Sum(sle.actual_qty).as_("qty"))
		.where(
			(sle.batch_no == batch.name)
			& (sle.product_code == product_code)
			& (sle.company == company)
			& (batch.disabled == 0)
			& (sle.is_cancelled == 0)
			& (IfNull(batch.expiry_date, "2200-01-01") > today())
		)
		.groupby(sle.warehouse, sle.batch_no, sle.product_code)
		.having(Sum(sle.actual_qty) > 0)
		.orderby(IfNull(batch.expiry_date, "2200-01-01"), batch.creation, sle.batch_no, sle.warehouse)
		.limit(cint(required_qty + total_picked_qty))
	)

	if from_warehouses:
		query = query.where(sle.warehouse.isin(from_warehouses))

	return query.run(as_dict=True)


def get_available_product_locations_for_serial_and_batched_product(
	product_code, from_warehouses, required_qty, company, total_picked_qty=0
):
	# Get batch nos by FIFO
	locations = get_available_product_locations_for_batched_product(
		product_code, from_warehouses, required_qty, company
	)

	if locations:
		sn = frappe.qb.DocType("Serial No")
		conditions = (sn.product_code == product_code) & (sn.company == company)

		for location in locations:
			location.qty = (
				required_qty if location.qty > required_qty else location.qty
			)  # if extra qty in batch

			serial_nos = (
				frappe.qb.from_(sn)
				.select(sn.name)
				.where(
					(conditions) & (sn.batch_no == location.batch_no) & (sn.warehouse == location.warehouse)
				)
				.orderby(sn.purchase_date)
				.limit(cint(location.qty + total_picked_qty))
			).run(as_dict=True)

			serial_nos = [sn.name for sn in serial_nos]
			location.serial_no = serial_nos
			location.qty = len(serial_nos)

	return locations


def get_available_product_locations_for_other_product(
	product_code, from_warehouses, required_qty, company, total_picked_qty=0
):
	bin = frappe.qb.DocType("Bin")
	query = (
		frappe.qb.from_(bin)
		.select(bin.warehouse, bin.actual_qty.as_("qty"))
		.where((bin.product_code == product_code) & (bin.actual_qty > 0))
		.orderby(bin.creation)
		.limit(cint(required_qty + total_picked_qty))
	)

	if from_warehouses:
		query = query.where(bin.warehouse.isin(from_warehouses))
	else:
		wh = frappe.qb.DocType("Warehouse")
		query = query.from_(wh).where((bin.warehouse == wh.name) & (wh.company == company))

	product_locations = query.run(as_dict=True)

	return product_locations


@frappe.whitelist()
def create_delivery_note(source_name, target_doc=None):
	pick_list = frappe.get_doc("Pick List", source_name)
	validate_product_locations(pick_list)
	sales_dict = dict()
	sales_orders = []
	delivery_note = None
	for location in pick_list.locations:
		if location.sales_order:
			sales_orders.append(
				frappe.db.get_value(
					"Sales Order", location.sales_order, ["customer", "name as sales_order"], as_dict=True
				)
			)

	for customer, rows in groupby(sales_orders, key=lambda so: so["customer"]):
		sales_dict[customer] = {row.sales_order for row in rows}

	if sales_dict:
		delivery_note = create_dn_with_so(sales_dict, pick_list)

	if not all(product.sales_order for product in pick_list.locations):
		delivery_note = create_dn_wo_so(pick_list)

	frappe.msgprint(_("Delivery Note(s) created for the Pick List"))
	return delivery_note


def create_dn_wo_so(pick_list):
	delivery_note = frappe.new_doc("Delivery Note")

	product_table_mapper_without_so = {
		"doctype": "Delivery Note Product",
		"field_map": {
			"rate": "rate",
			"name": "name",
			"parent": "",
		},
	}
	map_pl_locations(pick_list, product_table_mapper_without_so, delivery_note)
	delivery_note.insert(ignore_mandatory=True)

	return delivery_note


def create_dn_with_so(sales_dict, pick_list):
	delivery_note = None

	product_table_mapper = {
		"doctype": "Delivery Note Product",
		"field_map": {
			"rate": "rate",
			"name": "so_detail",
			"parent": "against_sales_order",
		},
		"condition": lambda doc: abs(doc.delivered_qty) < abs(doc.qty)
		and doc.delivered_by_supplier != 1,
	}

	for customer in sales_dict:
		for so in sales_dict[customer]:
			delivery_note = None
			delivery_note = create_delivery_note_from_sales_order(so, delivery_note, skip_product_mapping=True)
			break
		if delivery_note:
			# map all products of all sales orders of that customer
			for so in sales_dict[customer]:
				map_pl_locations(pick_list, product_table_mapper, delivery_note, so)
			delivery_note.flags.ignore_mandatory = True
			delivery_note.insert()
			update_packed_product_details(pick_list, delivery_note)
			delivery_note.save()

	return delivery_note


def map_pl_locations(pick_list, product_mapper, delivery_note, sales_order=None):

	for location in pick_list.locations:
		if location.sales_order != sales_order or location.product_bundle_product:
			continue

		if location.sales_order_product:
			sales_order_product = frappe.get_doc("Sales Order Product", location.sales_order_product)
		else:
			sales_order_product = None

		source_doc = sales_order_product or location

		dn_product = map_child_doc(source_doc, delivery_note, product_mapper)

		if dn_product:
			dn_product.pick_list_product = location.name
			dn_product.warehouse = location.warehouse
			dn_product.qty = flt(location.picked_qty) / (flt(location.conversion_factor) or 1)
			dn_product.batch_no = location.batch_no
			dn_product.serial_no = location.serial_no

			update_delivery_note_product(source_doc, dn_product, delivery_note)

	add_product_bundles_to_delivery_note(pick_list, delivery_note, product_mapper)
	set_delivery_note_missing_values(delivery_note)

	delivery_note.pick_list = pick_list.name
	delivery_note.company = pick_list.company
	delivery_note.customer = frappe.get_value("Sales Order", sales_order, "customer")


def add_product_bundles_to_delivery_note(
	pick_list: "PickList", delivery_note, product_mapper
) -> None:
	"""Add product bundles found in pick list to delivery note.

	When mapping pick list products, the bundle product itself isn't part of the
	locations. Dynamically fetch and add parent bundle product into DN."""
	product_bundles = pick_list._get_product_bundles()
	product_bundle_qty_map = pick_list._get_product_bundle_qty_map(product_bundles.values())

	for so_row, product_code in product_bundles.products():
		sales_order_product = frappe.get_doc("Sales Order Product", so_row)
		dn_bundle_product = map_child_doc(sales_order_product, delivery_note, product_mapper)
		dn_bundle_product.qty = pick_list._compute_picked_qty_for_bundle(
			so_row, product_bundle_qty_map[product_code]
		)
		update_delivery_note_product(sales_order_product, dn_bundle_product, delivery_note)


def update_packed_product_details(pick_list: "PickList", delivery_note) -> None:
	"""Update stock details on packed products table of delivery note."""

	def _find_so_row(packed_product):
		for product in delivery_note.products:
			if packed_product.parent_detail_docname == product.name:
				return product.so_detail

	def _find_pick_list_location(bundle_row, packed_product):
		if not bundle_row:
			return
		for loc in pick_list.locations:
			if loc.product_bundle_product == bundle_row and loc.product_code == packed_product.product_code:
				return loc

	for packed_product in delivery_note.packed_products:
		so_row = _find_so_row(packed_product)
		location = _find_pick_list_location(so_row, packed_product)
		if not location:
			continue
		packed_product.warehouse = location.warehouse
		packed_product.batch_no = location.batch_no
		packed_product.serial_no = location.serial_no


@frappe.whitelist()
def create_stock_entry(pick_list):
	pick_list = frappe.get_doc(json.loads(pick_list))
	validate_product_locations(pick_list)

	if stock_entry_exists(pick_list.get("name")):
		return frappe.msgprint(_("Stock Entry has been already created against this Pick List"))

	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.pick_list = pick_list.get("name")
	stock_entry.purpose = pick_list.get("purpose")
	stock_entry.set_stock_entry_type()

	if pick_list.get("work_order"):
		stock_entry = update_stock_entry_based_on_work_order(pick_list, stock_entry)
	elif pick_list.get("material_request"):
		stock_entry = update_stock_entry_based_on_material_request(pick_list, stock_entry)
	else:
		stock_entry = update_stock_entry_products_with_no_reference(pick_list, stock_entry)

	stock_entry.set_missing_values()

	return stock_entry.as_dict()


@frappe.whitelist()
def get_pending_work_orders(doctype, txt, searchfield, start, page_length, filters, as_dict):
	wo = frappe.qb.DocType("Work Order")
	return (
		frappe.qb.from_(wo)
		.select(wo.name, wo.company, wo.planned_start_date)
		.where(
			(wo.status.notin(["Completed", "Stopped"]))
			& (wo.qty > wo.material_transferred_for_manufacturing)
			& (wo.docstatus == 1)
			& (wo.company == filters.get("company"))
			& (wo.name.like("%{0}%".format(txt)))
		)
		.orderby(Case().when(Locate(txt, wo.name) > 0, Locate(txt, wo.name)).else_(99999))
		.orderby(wo.name)
		.limit(cint(page_length))
		.offset(start)
	).run(as_dict=as_dict)


@frappe.whitelist()
def target_document_exists(pick_list_name, purpose):
	if purpose == "Delivery":
		return frappe.db.exists("Delivery Note", {"pick_list": pick_list_name})

	return stock_entry_exists(pick_list_name)


@frappe.whitelist()
def get_product_details(product_code, uom=None):
	details = frappe.db.get_value("Product", product_code, ["stock_uom", "name"], as_dict=1)
	details.uom = uom or details.stock_uom
	if uom:
		details.update(get_conversion_factor(product_code, uom))

	return details


def update_delivery_note_product(source, target, delivery_note):
	cost_center = frappe.db.get_value("Project", delivery_note.project, "cost_center")
	if not cost_center:
		cost_center = get_cost_center(source.product_code, "Product", delivery_note.company)

	if not cost_center:
		cost_center = get_cost_center(source.product_group, "Product Group", delivery_note.company)

	target.cost_center = cost_center


def get_cost_center(for_product, from_doctype, company):
	"""Returns Cost Center for Product or Product Group"""
	return frappe.db.get_value(
		"Product Default",
		fieldname=["buying_cost_center"],
		filters={"parent": for_product, "parenttype": from_doctype, "company": company},
	)


def set_delivery_note_missing_values(target):
	target.run_method("set_missing_values")
	target.run_method("set_po_nos")
	target.run_method("calculate_taxes_and_totals")


def stock_entry_exists(pick_list_name):
	return frappe.db.exists("Stock Entry", {"pick_list": pick_list_name})


def update_stock_entry_based_on_work_order(pick_list, stock_entry):
	work_order = frappe.get_doc("Work Order", pick_list.get("work_order"))

	stock_entry.work_order = work_order.name
	stock_entry.company = work_order.company
	stock_entry.from_bom = 1
	stock_entry.bom_no = work_order.bom_no
	stock_entry.use_multi_level_bom = work_order.use_multi_level_bom
	stock_entry.fg_completed_qty = pick_list.for_qty
	if work_order.bom_no:
		stock_entry.inspection_required = frappe.db.get_value(
			"BOM", work_order.bom_no, "inspection_required"
		)

	is_wip_warehouse_group = frappe.db.get_value("Warehouse", work_order.wip_warehouse, "is_group")
	if not (is_wip_warehouse_group and work_order.skip_transfer):
		wip_warehouse = work_order.wip_warehouse
	else:
		wip_warehouse = None
	stock_entry.to_warehouse = wip_warehouse

	stock_entry.project = work_order.project

	for location in pick_list.locations:
		product = frappe._dict()
		update_common_product_properties(product, location)
		product.t_warehouse = wip_warehouse

		stock_entry.append("products", product)

	return stock_entry


def update_stock_entry_based_on_material_request(pick_list, stock_entry):
	for location in pick_list.locations:
		target_warehouse = None
		if location.material_request_product:
			target_warehouse = frappe.get_value(
				"Material Request Product", location.material_request_product, "warehouse"
			)
		product = frappe._dict()
		update_common_product_properties(product, location)
		product.t_warehouse = target_warehouse
		stock_entry.append("products", product)

	return stock_entry


def update_stock_entry_products_with_no_reference(pick_list, stock_entry):
	for location in pick_list.locations:
		product = frappe._dict()
		update_common_product_properties(product, location)

		stock_entry.append("products", product)

	return stock_entry


def update_common_product_properties(product, location):
	product.product_code = location.product_code
	product.s_warehouse = location.warehouse
	product.qty = location.picked_qty * location.conversion_factor
	product.transfer_qty = location.picked_qty
	product.uom = location.uom
	product.conversion_factor = location.conversion_factor
	product.stock_uom = location.stock_uom
	product.material_request = location.material_request
	product.serial_no = location.serial_no
	product.batch_no = location.batch_no
	product.material_request_product = location.material_request_product
