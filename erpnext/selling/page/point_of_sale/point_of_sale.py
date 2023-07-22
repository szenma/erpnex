# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json
from typing import Dict, Optional

import frappe
from frappe.utils import cint
from frappe.utils.nestedset import get_root_of

from erpnext.accounts.doctype.pos_invoice.pos_invoice import get_stock_availability
from erpnext.accounts.doctype.pos_profile.pos_profile import get_child_nodes, get_product_groups
from erpnext.stock.utils import scan_barcode


def search_by_term(search_term, warehouse, price_list):
	result = search_for_serial_or_batch_or_barcode_number(search_term) or {}
	product_code = result.get("product_code", search_term)
	serial_no = result.get("serial_no", "")
	batch_no = result.get("batch_no", "")
	barcode = result.get("barcode", "")
	if not result:
		return
	product_doc = frappe.get_doc("Product", product_code)
	if not product_doc:
		return
	product = {
		"barcode": barcode,
		"batch_no": batch_no,
		"description": product_doc.description,
		"is_stock_product": product_doc.is_stock_product,
		"product_code": product_doc.name,
		"product_image": product_doc.image,
		"product_name": product_doc.product_name,
		"serial_no": serial_no,
		"stock_uom": product_doc.stock_uom,
		"uom": product_doc.stock_uom,
	}
	if barcode:
		barcode_info = next(filter(lambda x: x.barcode == barcode, product_doc.get("barcodes", [])), None)
		if barcode_info and barcode_info.uom:
			uom = next(filter(lambda x: x.uom == barcode_info.uom, product_doc.uoms), {})
			product.update(
				{
					"uom": barcode_info.uom,
					"conversion_factor": uom.get("conversion_factor", 1),
				}
			)

	product_stock_qty, is_stock_product = get_stock_availability(product_code, warehouse)
	product_stock_qty = product_stock_qty // product.get("conversion_factor", 1)
	product.update({"actual_qty": product_stock_qty})

	price = frappe.get_list(
		doctype="Product Price",
		filters={
			"price_list": price_list,
			"product_code": product_code,
		},
		fields=["uom", "currency", "price_list_rate"],
	)

	def __sort(p):
		p_uom = p.get("uom")
		if p_uom == product.get("uom"):
			return 0
		elif p_uom == product.get("stock_uom"):
			return 1
		else:
			return 2

	# sort by fallback preference. always pick exact uom match if available
	price = sorted(price, key=__sort)
	if len(price) > 0:
		p = price.pop(0)
		product.update(
			{
				"currency": p.get("currency"),
				"price_list_rate": p.get("price_list_rate"),
			}
		)
	return {"products": [product]}


@frappe.whitelist()
def get_products(start, page_length, price_list, product_group, pos_profile, search_term=""):
	warehouse, hide_unavailable_products = frappe.db.get_value(
		"POS Profile", pos_profile, ["warehouse", "hide_unavailable_products"]
	)

	result = []

	if search_term:
		result = search_by_term(search_term, warehouse, price_list) or []
		if result:
			return result

	if not frappe.db.exists("Product Group", product_group):
		product_group = get_root_of("Product Group")

	condition = get_conditions(search_term)
	condition += get_product_group_condition(pos_profile)

	lft, rgt = frappe.db.get_value("Product Group", product_group, ["lft", "rgt"])

	bin_join_selection, bin_join_condition = "", ""
	if hide_unavailable_products:
		bin_join_selection = ", `tabBin` bin"
		bin_join_condition = (
			"AND bin.warehouse = %(warehouse)s AND bin.product_code = product.name AND bin.actual_qty > 0"
		)

	products_data = frappe.db.sql(
		"""
		SELECT
			product.name AS product_code,
			product.product_name,
			product.description,
			product.stock_uom,
			product.image AS product_image,
			product.is_stock_product
		FROM
			`tabProduct` product {bin_join_selection}
		WHERE
			product.disabled = 0
			AND product.has_variants = 0
			AND product.is_sales_product = 1
			AND product.is_fixed_asset = 0
			AND product.product_group in (SELECT name FROM `tabProduct Group` WHERE lft >= {lft} AND rgt <= {rgt})
			AND {condition}
			{bin_join_condition}
		ORDER BY
			product.name asc
		LIMIT
			{page_length} offset {start}""".format(
			start=cint(start),
			page_length=cint(page_length),
			lft=cint(lft),
			rgt=cint(rgt),
			condition=condition,
			bin_join_selection=bin_join_selection,
			bin_join_condition=bin_join_condition,
		),
		{"warehouse": warehouse},
		as_dict=1,
	)

	if products_data:
		products = [d.product_code for d in products_data]
		product_prices_data = frappe.get_all(
			"Product Price",
			fields=["product_code", "price_list_rate", "currency"],
			filters={"price_list": price_list, "product_code": ["in", products]},
		)

		product_prices = {}
		for d in product_prices_data:
			product_prices[d.product_code] = d

		for product in products_data:
			product_code = product.product_code
			product_price = product_prices.get(product_code) or {}
			product_stock_qty, is_stock_product = get_stock_availability(product_code, warehouse)

			row = {}
			row.update(product)
			row.update(
				{
					"price_list_rate": product_price.get("price_list_rate"),
					"currency": product_price.get("currency"),
					"actual_qty": product_stock_qty,
				}
			)
			result.append(row)

	return {"products": result}


@frappe.whitelist()
def search_for_serial_or_batch_or_barcode_number(search_value: str) -> Dict[str, Optional[str]]:
	return scan_barcode(search_value)


def get_conditions(search_term):
	condition = "("
	condition += """product.name like {search_term}
		or product.product_name like {search_term}""".format(
		search_term=frappe.db.escape("%" + search_term + "%")
	)
	condition += add_search_fields_condition(search_term)
	condition += ")"

	return condition


def add_search_fields_condition(search_term):
	condition = ""
	search_fields = frappe.get_all("POS Search Fields", fields=["fieldname"])
	if search_fields:
		for field in search_fields:
			condition += " or product.`{0}` like {1}".format(
				field["fieldname"], frappe.db.escape("%" + search_term + "%")
			)
	return condition


def get_product_group_condition(pos_profile):
	cond = "and 1=1"
	product_groups = get_product_groups(pos_profile)
	if product_groups:
		cond = "and product.product_group in (%s)" % (", ".join(["%s"] * len(product_groups)))

	return cond % tuple(product_groups)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def product_group_query(doctype, txt, searchfield, start, page_len, filters):
	product_groups = []
	cond = "1=1"
	pos_profile = filters.get("pos_profile")

	if pos_profile:
		product_groups = get_product_groups(pos_profile)

		if product_groups:
			cond = "name in (%s)" % (", ".join(["%s"] * len(product_groups)))
			cond = cond % tuple(product_groups)

	return frappe.db.sql(
		""" select distinct name from `tabProduct Group`
			where {condition} and (name like %(txt)s) limit {page_len} offset {start}""".format(
			condition=cond, start=start, page_len=page_len
		),
		{"txt": "%%%s%%" % txt},
	)


@frappe.whitelist()
def check_opening_entry(user):
	open_vouchers = frappe.db.get_all(
		"POS Opening Entry",
		filters={"user": user, "pos_closing_entry": ["in", ["", None]], "docstatus": 1},
		fields=["name", "company", "pos_profile", "period_start_date"],
		order_by="period_start_date desc",
	)

	return open_vouchers


@frappe.whitelist()
def create_opening_voucher(pos_profile, company, balance_details):
	balance_details = json.loads(balance_details)

	new_pos_opening = frappe.get_doc(
		{
			"doctype": "POS Opening Entry",
			"period_start_date": frappe.utils.get_datetime(),
			"posting_date": frappe.utils.getdate(),
			"user": frappe.session.user,
			"pos_profile": pos_profile,
			"company": company,
		}
	)
	new_pos_opening.set("balance_details", balance_details)
	new_pos_opening.submit()

	return new_pos_opening.as_dict()


@frappe.whitelist()
def get_past_order_list(search_term, status, limit=20):
	fields = ["name", "grand_total", "currency", "customer", "posting_time", "posting_date"]
	invoice_list = []

	if search_term and status:
		invoices_by_customer = frappe.db.get_all(
			"POS Invoice",
			filters={"customer": ["like", "%{}%".format(search_term)], "status": status},
			fields=fields,
		)
		invoices_by_name = frappe.db.get_all(
			"POS Invoice",
			filters={"name": ["like", "%{}%".format(search_term)], "status": status},
			fields=fields,
		)

		invoice_list = invoices_by_customer + invoices_by_name
	elif status:
		invoice_list = frappe.db.get_all("POS Invoice", filters={"status": status}, fields=fields)

	return invoice_list


@frappe.whitelist()
def set_customer_info(fieldname, customer, value=""):
	if fieldname == "loyalty_program":
		frappe.db.set_value("Customer", customer, "loyalty_program", value)

	contact = frappe.get_cached_value("Customer", customer, "customer_primary_contact")
	if not contact:
		contact = frappe.db.sql(
			"""
			SELECT parent FROM `tabDynamic Link`
			WHERE
				parenttype = 'Contact' AND
				parentfield = 'links' AND
				link_doctype = 'Customer' AND
				link_name = %s
			""",
			(customer),
			as_dict=1,
		)
		contact = contact[0].get("parent") if contact else None

	if not contact:
		new_contact = frappe.new_doc("Contact")
		new_contact.is_primary_contact = 1
		new_contact.first_name = customer
		new_contact.set("links", [{"link_doctype": "Customer", "link_name": customer}])
		new_contact.save()
		contact = new_contact.name
		frappe.db.set_value("Customer", customer, "customer_primary_contact", contact)

	contact_doc = frappe.get_doc("Contact", contact)
	if fieldname == "email_id":
		contact_doc.set("email_ids", [{"email_id": value, "is_primary": 1}])
		frappe.db.set_value("Customer", customer, "email_id", value)
	elif fieldname == "mobile_no":
		contact_doc.set("phone_nos", [{"phone": value, "is_primary_mobile_no": 1}])
		frappe.db.set_value("Customer", customer, "mobile_no", value)
	contact_doc.save()


@frappe.whitelist()
def get_pos_profile_data(pos_profile):
	pos_profile = frappe.get_doc("POS Profile", pos_profile)
	pos_profile = pos_profile.as_dict()

	_customer_groups_with_children = []
	for row in pos_profile.customer_groups:
		children = get_child_nodes("Customer Group", row.customer_group)
		_customer_groups_with_children.extend(children)

	pos_profile.customer_groups = _customer_groups_with_children
	return pos_profile
