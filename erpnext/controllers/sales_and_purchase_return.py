# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.meta import get_field_precision
from frappe.utils import flt, format_datetime, get_datetime

import erpnext
from erpnext.stock.utils import get_incoming_rate


class StockOverReturnError(frappe.ValidationError):
	pass


def validate_return(doc):
	if not doc.meta.get_field("is_return") or not doc.is_return:
		return

	if doc.return_against:
		validate_return_against(doc)
		validate_returned_products(doc)


def validate_return_against(doc):
	if not frappe.db.exists(doc.doctype, doc.return_against):
		frappe.throw(
			_("Invalid {0}: {1}").format(doc.meta.get_label("return_against"), doc.return_against)
		)
	else:
		ref_doc = frappe.get_doc(doc.doctype, doc.return_against)

		party_type = "customer" if doc.doctype in ("Sales Invoice", "Delivery Note") else "supplier"

		if (
			ref_doc.company == doc.company
			and ref_doc.get(party_type) == doc.get(party_type)
			and ref_doc.docstatus.is_submitted()
		):
			# validate posting date time
			return_posting_datetime = "%s %s" % (doc.posting_date, doc.get("posting_time") or "00:00:00")
			ref_posting_datetime = "%s %s" % (
				ref_doc.posting_date,
				ref_doc.get("posting_time") or "00:00:00",
			)

			if get_datetime(return_posting_datetime) < get_datetime(ref_posting_datetime):
				frappe.throw(
					_("Posting timestamp must be after {0}").format(format_datetime(ref_posting_datetime))
				)

			# validate same exchange rate
			if doc.conversion_rate != ref_doc.conversion_rate:
				frappe.throw(
					_("Exchange Rate must be same as {0} {1} ({2})").format(
						doc.doctype, doc.return_against, ref_doc.conversion_rate
					)
				)

			# validate update stock
			if doc.doctype == "Sales Invoice" and doc.update_stock and not ref_doc.update_stock:
				frappe.throw(
					_("'Update Stock' can not be checked because products are not delivered via {0}").format(
						doc.return_against
					)
				)


def validate_returned_products(doc):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	valid_products = frappe._dict()

	select_fields = "product_code, qty, stock_qty, rate, parenttype, conversion_factor"
	if doc.doctype != "Purchase Invoice":
		select_fields += ",serial_no, batch_no"

	if doc.doctype in ["Purchase Invoice", "Purchase Receipt", "Subcontracting Receipt"]:
		select_fields += ",rejected_qty, received_qty"

	for d in frappe.db.sql(
		"""select {0} from `tab{1} Product` where parent = %s""".format(select_fields, doc.doctype),
		doc.return_against,
		as_dict=1,
	):
		valid_products = get_ref_product_dict(valid_products, d)

	if doc.doctype in ("Delivery Note", "Sales Invoice"):
		for d in frappe.db.sql(
			"""select product_code, qty, serial_no, batch_no from `tabPacked Product`
			where parent = %s""",
			doc.return_against,
			as_dict=1,
		):
			valid_products = get_ref_product_dict(valid_products, d)

	already_returned_products = get_already_returned_products(doc)

	# ( not mandatory when it is Purchase Invoice or a Sales Invoice without Update Stock )
	warehouse_mandatory = not (
		(doc.doctype == "Purchase Invoice" or doc.doctype == "Sales Invoice") and not doc.update_stock
	)

	products_returned = False
	for d in doc.get("products"):
		if d.product_code and (flt(d.qty) < 0 or flt(d.get("received_qty")) < 0):
			if d.product_code not in valid_products:
				frappe.throw(
					_("Row # {0}: Returned Product {1} does not exist in {2} {3}").format(
						d.idx, d.product_code, doc.doctype, doc.return_against
					)
				)
			else:
				ref = valid_products.get(d.product_code, frappe._dict())
				validate_quantity(doc, d, ref, valid_products, already_returned_products)

				if ref.rate and doc.doctype in ("Delivery Note", "Sales Invoice") and flt(d.rate) > ref.rate:
					frappe.throw(
						_("Row # {0}: Rate cannot be greater than the rate used in {1} {2}").format(
							d.idx, doc.doctype, doc.return_against
						)
					)

				elif ref.batch_no and d.batch_no not in ref.batch_no:
					frappe.throw(
						_("Row # {0}: Batch No must be same as {1} {2}").format(
							d.idx, doc.doctype, doc.return_against
						)
					)

				elif ref.serial_no:
					if d.qty and not d.serial_no:
						frappe.throw(_("Row # {0}: Serial No is mandatory").format(d.idx))
					else:
						serial_nos = get_serial_nos(d.serial_no)
						for s in serial_nos:
							if s not in ref.serial_no:
								frappe.throw(
									_("Row # {0}: Serial No {1} does not match with {2} {3}").format(
										d.idx, s, doc.doctype, doc.return_against
									)
								)

				if (
					warehouse_mandatory
					and not d.get("warehouse")
					and frappe.db.get_value("Product", d.product_code, "is_stock_product")
				):
					frappe.throw(_("Warehouse is mandatory"))

			products_returned = True

		elif d.product_name:
			products_returned = True

	if not products_returned:
		frappe.throw(_("Atleast one product should be entered with negative quantity in return document"))


def validate_quantity(doc, args, ref, valid_products, already_returned_products):
	fields = ["stock_qty"]
	if doc.doctype in ["Purchase Receipt", "Purchase Invoice", "Subcontracting Receipt"]:
		fields.extend(["received_qty", "rejected_qty"])

	already_returned_data = already_returned_products.get(args.product_code) or {}

	company_currency = erpnext.get_company_currency(doc.company)
	stock_qty_precision = get_field_precision(
		frappe.get_meta(doc.doctype + " Product").get_field("stock_qty"), company_currency
	)

	for column in fields:
		returned_qty = flt(already_returned_data.get(column, 0)) if len(already_returned_data) > 0 else 0

		if column == "stock_qty":
			reference_qty = ref.get(column)
			current_stock_qty = args.get(column)
		else:
			reference_qty = ref.get(column) * ref.get("conversion_factor", 1.0)
			current_stock_qty = args.get(column) * args.get("conversion_factor", 1.0)

		max_returnable_qty = flt(reference_qty, stock_qty_precision) - returned_qty
		label = column.replace("_", " ").title()

		if reference_qty:
			if flt(args.get(column)) > 0:
				frappe.throw(_("{0} must be negative in return document").format(label))
			elif returned_qty >= reference_qty and args.get(column):
				frappe.throw(
					_("Product {0} has already been returned").format(args.product_code), StockOverReturnError
				)
			elif abs(flt(current_stock_qty, stock_qty_precision)) > max_returnable_qty:
				frappe.throw(
					_("Row # {0}: Cannot return more than {1} for Product {2}").format(
						args.idx, max_returnable_qty, args.product_code
					),
					StockOverReturnError,
				)


def get_ref_product_dict(valid_products, ref_product_row):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	valid_products.setdefault(
		ref_product_row.product_code,
		frappe._dict(
			{
				"qty": 0,
				"rate": 0,
				"stock_qty": 0,
				"rejected_qty": 0,
				"received_qty": 0,
				"serial_no": [],
				"conversion_factor": ref_product_row.get("conversion_factor", 1),
				"batch_no": [],
			}
		),
	)
	product_dict = valid_products[ref_product_row.product_code]
	product_dict["qty"] += ref_product_row.qty
	product_dict["stock_qty"] += ref_product_row.get("stock_qty", 0)
	if ref_product_row.get("rate", 0) > product_dict["rate"]:
		product_dict["rate"] = ref_product_row.get("rate", 0)

	if ref_product_row.parenttype in ["Purchase Invoice", "Purchase Receipt", "Subcontracting Receipt"]:
		product_dict["received_qty"] += ref_product_row.received_qty
		product_dict["rejected_qty"] += ref_product_row.rejected_qty

	if ref_product_row.get("serial_no"):
		product_dict["serial_no"] += get_serial_nos(ref_product_row.serial_no)

	if ref_product_row.get("batch_no"):
		product_dict["batch_no"].append(ref_product_row.batch_no)

	return valid_products


def get_already_returned_products(doc):
	column = "child.product_code, sum(abs(child.qty)) as qty, sum(abs(child.stock_qty)) as stock_qty"
	if doc.doctype in ["Purchase Invoice", "Purchase Receipt", "Subcontracting Receipt"]:
		column += """, sum(abs(child.rejected_qty) * child.conversion_factor) as rejected_qty,
			sum(abs(child.received_qty) * child.conversion_factor) as received_qty"""

	data = frappe.db.sql(
		"""
		select {0}
		from
			`tab{1} Product` child, `tab{2}` par
		where
			child.parent = par.name and par.docstatus = 1
			and par.is_return = 1 and par.return_against = %s
		group by product_code
	""".format(
			column, doc.doctype, doc.doctype
		),
		doc.return_against,
		as_dict=1,
	)

	products = {}

	for d in data:
		products.setdefault(
			d.product_code,
			frappe._dict(
				{
					"qty": d.get("qty"),
					"stock_qty": d.get("stock_qty"),
					"received_qty": d.get("received_qty"),
					"rejected_qty": d.get("rejected_qty"),
				}
			),
		)

	return products


def get_returned_qty_map_for_row(return_against, party, row_name, doctype):
	child_doctype = doctype + " Product"
	reference_field = "dn_detail" if doctype == "Delivery Note" else frappe.scrub(child_doctype)

	if doctype in ("Purchase Receipt", "Purchase Invoice", "Subcontracting Receipt"):
		party_type = "supplier"
	else:
		party_type = "customer"

	fields = [
		"sum(abs(`tab{0}`.qty)) as qty".format(child_doctype),
	]

	if doctype != "Subcontracting Receipt":
		fields += [
			"sum(abs(`tab{0}`.stock_qty)) as stock_qty".format(child_doctype),
		]

	if doctype in ("Purchase Receipt", "Purchase Invoice", "Subcontracting Receipt"):
		fields += [
			"sum(abs(`tab{0}`.rejected_qty)) as rejected_qty".format(child_doctype),
			"sum(abs(`tab{0}`.received_qty)) as received_qty".format(child_doctype),
		]

		if doctype == "Purchase Receipt":
			fields += ["sum(abs(`tab{0}`.received_stock_qty)) as received_stock_qty".format(child_doctype)]

	# Used retrun against and supplier and is_retrun because there is an index added for it
	data = frappe.get_all(
		doctype,
		fields=fields,
		filters=[
			[doctype, "return_against", "=", return_against],
			[doctype, party_type, "=", party],
			[doctype, "docstatus", "=", 1],
			[doctype, "is_return", "=", 1],
			[child_doctype, reference_field, "=", row_name],
		],
	)

	return data[0]


def make_return_doc(doctype: str, source_name: str, target_doc=None):
	from frappe.model.mapper import get_mapped_doc

	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	company = frappe.db.get_value("Delivery Note", source_name, "company")
	default_warehouse_for_sales_return = frappe.get_cached_value(
		"Company", company, "default_warehouse_for_sales_return"
	)

	def set_missing_values(source, target):
		doc = frappe.get_doc(target)
		doc.is_return = 1
		doc.return_against = source.name
		doc.set_warehouse = ""
		if doctype == "Sales Invoice" or doctype == "POS Invoice":
			doc.is_pos = source.is_pos

			# look for Print Heading "Credit Note"
			if not doc.select_print_heading:
				doc.select_print_heading = frappe.get_cached_value("Print Heading", _("Credit Note"))

		elif doctype == "Purchase Invoice":
			# look for Print Heading "Debit Note"
			doc.select_print_heading = frappe.get_cached_value("Print Heading", _("Debit Note"))

		for tax in doc.get("taxes") or []:
			if tax.charge_type == "Actual":
				tax.tax_amount = -1 * tax.tax_amount

		if doc.get("is_return"):
			if doc.doctype == "Sales Invoice" or doc.doctype == "POS Invoice":
				doc.consolidated_invoice = ""
				doc.set("payments", [])
				for data in source.payments:
					paid_amount = 0.00
					base_paid_amount = 0.00
					data.base_amount = flt(
						data.amount * source.conversion_rate, source.precision("base_paid_amount")
					)
					paid_amount += data.amount
					base_paid_amount += data.base_amount
					doc.append(
						"payments",
						{
							"mode_of_payment": data.mode_of_payment,
							"type": data.type,
							"amount": -1 * paid_amount,
							"base_amount": -1 * base_paid_amount,
							"account": data.account,
							"default": data.default,
						},
					)
				if doc.is_pos:
					doc.paid_amount = -1 * source.paid_amount
			elif doc.doctype == "Purchase Invoice":
				doc.paid_amount = -1 * source.paid_amount
				doc.base_paid_amount = -1 * source.base_paid_amount
				doc.payment_terms_template = ""
				doc.payment_schedule = []

		if doc.get("is_return") and hasattr(doc, "packed_products"):
			for d in doc.get("packed_products"):
				d.qty = d.qty * -1

		if doc.get("discount_amount"):
			doc.discount_amount = -1 * source.discount_amount

		if doctype != "Subcontracting Receipt":
			doc.run_method("calculate_taxes_and_totals")

	def update_product(source_doc, target_doc, source_parent):
		target_doc.qty = -1 * source_doc.qty

		if source_doc.serial_no:
			returned_serial_nos = get_returned_serial_nos(source_doc, source_parent)
			serial_nos = list(set(get_serial_nos(source_doc.serial_no)) - set(returned_serial_nos))
			if serial_nos:
				target_doc.serial_no = "\n".join(serial_nos)

		if source_doc.get("rejected_serial_no"):
			returned_serial_nos = get_returned_serial_nos(
				source_doc, source_parent, serial_no_field="rejected_serial_no"
			)
			rejected_serial_nos = list(
				set(get_serial_nos(source_doc.rejected_serial_no)) - set(returned_serial_nos)
			)
			if rejected_serial_nos:
				target_doc.rejected_serial_no = "\n".join(rejected_serial_nos)

		if doctype in ["Purchase Receipt", "Subcontracting Receipt"]:
			returned_qty_map = get_returned_qty_map_for_row(
				source_parent.name, source_parent.supplier, source_doc.name, doctype
			)

			if doctype == "Subcontracting Receipt":
				target_doc.received_qty = -1 * flt(source_doc.qty)
			else:
				target_doc.received_qty = -1 * flt(
					source_doc.received_qty - (returned_qty_map.get("received_qty") or 0)
				)
				target_doc.rejected_qty = -1 * flt(
					source_doc.rejected_qty - (returned_qty_map.get("rejected_qty") or 0)
				)

			target_doc.qty = -1 * flt(source_doc.qty - (returned_qty_map.get("qty") or 0))

			if hasattr(target_doc, "stock_qty"):
				target_doc.stock_qty = -1 * flt(
					source_doc.stock_qty - (returned_qty_map.get("stock_qty") or 0)
				)
				target_doc.received_stock_qty = -1 * flt(
					source_doc.received_stock_qty - (returned_qty_map.get("received_stock_qty") or 0)
				)

			if doctype == "Subcontracting Receipt":
				target_doc.subcontracting_order = source_doc.subcontracting_order
				target_doc.subcontracting_order_product = source_doc.subcontracting_order_product
				target_doc.rejected_warehouse = source_doc.rejected_warehouse
				target_doc.subcontracting_receipt_product = source_doc.name
			else:
				target_doc.purchase_order = source_doc.purchase_order
				target_doc.purchase_order_product = source_doc.purchase_order_product
				target_doc.rejected_warehouse = source_doc.rejected_warehouse
				target_doc.purchase_receipt_product = source_doc.name

		elif doctype == "Purchase Invoice":
			returned_qty_map = get_returned_qty_map_for_row(
				source_parent.name, source_parent.supplier, source_doc.name, doctype
			)
			target_doc.received_qty = -1 * flt(
				source_doc.received_qty - (returned_qty_map.get("received_qty") or 0)
			)
			target_doc.rejected_qty = -1 * flt(
				source_doc.rejected_qty - (returned_qty_map.get("rejected_qty") or 0)
			)
			target_doc.qty = -1 * flt(source_doc.qty - (returned_qty_map.get("qty") or 0))

			target_doc.stock_qty = -1 * flt(source_doc.stock_qty - (returned_qty_map.get("stock_qty") or 0))
			target_doc.purchase_order = source_doc.purchase_order
			target_doc.purchase_receipt = source_doc.purchase_receipt
			target_doc.rejected_warehouse = source_doc.rejected_warehouse
			target_doc.po_detail = source_doc.po_detail
			target_doc.pr_detail = source_doc.pr_detail
			target_doc.purchase_invoice_product = source_doc.name

		elif doctype == "Delivery Note":
			returned_qty_map = get_returned_qty_map_for_row(
				source_parent.name, source_parent.customer, source_doc.name, doctype
			)
			target_doc.qty = -1 * flt(source_doc.qty - (returned_qty_map.get("qty") or 0))
			target_doc.stock_qty = -1 * flt(source_doc.stock_qty - (returned_qty_map.get("stock_qty") or 0))

			target_doc.against_sales_order = source_doc.against_sales_order
			target_doc.against_sales_invoice = source_doc.against_sales_invoice
			target_doc.so_detail = source_doc.so_detail
			target_doc.si_detail = source_doc.si_detail
			target_doc.expense_account = source_doc.expense_account
			target_doc.dn_detail = source_doc.name
			if default_warehouse_for_sales_return:
				target_doc.warehouse = default_warehouse_for_sales_return
		elif doctype == "Sales Invoice" or doctype == "POS Invoice":
			returned_qty_map = get_returned_qty_map_for_row(
				source_parent.name, source_parent.customer, source_doc.name, doctype
			)
			target_doc.qty = -1 * flt(source_doc.qty - (returned_qty_map.get("qty") or 0))
			target_doc.stock_qty = -1 * flt(source_doc.stock_qty - (returned_qty_map.get("stock_qty") or 0))

			target_doc.sales_order = source_doc.sales_order
			target_doc.delivery_note = source_doc.delivery_note
			target_doc.so_detail = source_doc.so_detail
			target_doc.dn_detail = source_doc.dn_detail
			target_doc.expense_account = source_doc.expense_account

			if doctype == "Sales Invoice":
				target_doc.sales_invoice_product = source_doc.name
			else:
				target_doc.pos_invoice_product = source_doc.name

			if default_warehouse_for_sales_return:
				target_doc.warehouse = default_warehouse_for_sales_return

	def update_terms(source_doc, target_doc, source_parent):
		target_doc.payment_amount = -source_doc.payment_amount

	doclist = get_mapped_doc(
		doctype,
		source_name,
		{
			doctype: {
				"doctype": doctype,
				"validation": {
					"docstatus": ["=", 1],
				},
			},
			doctype
			+ " Product": {
				"doctype": doctype + " Product",
				"field_map": {"serial_no": "serial_no", "batch_no": "batch_no", "bom": "bom"},
				"postprocess": update_product,
			},
			"Payment Schedule": {"doctype": "Payment Schedule", "postprocess": update_terms},
		},
		target_doc,
		set_missing_values,
	)

	doclist.set_onload("ignore_price_list", True)

	return doclist


def get_rate_for_return(
	voucher_type,
	voucher_no,
	product_code,
	return_against=None,
	product_row=None,
	voucher_detail_no=None,
	sle=None,
):
	if not return_against:
		return_against = frappe.get_cached_value(voucher_type, voucher_no, "return_against")

	return_against_product_field = get_return_against_product_fields(voucher_type)

	filters = get_filters(
		voucher_type,
		voucher_no,
		voucher_detail_no,
		return_against,
		product_code,
		return_against_product_field,
		product_row,
	)

	if voucher_type in ("Purchase Receipt", "Purchase Invoice", "Subcontracting Receipt"):
		select_field = "incoming_rate"
	else:
		select_field = "abs(stock_value_difference / actual_qty)"

	rate = flt(frappe.db.get_value("Stock Ledger Entry", filters, select_field))
	if not (rate and return_against) and voucher_type in ["Sales Invoice", "Delivery Note"]:
		rate = frappe.db.get_value(f"{voucher_type} Product", voucher_detail_no, "incoming_rate")

		if not rate and sle:
			rate = get_incoming_rate(
				{
					"product_code": sle.product_code,
					"warehouse": sle.warehouse,
					"posting_date": sle.get("posting_date"),
					"posting_time": sle.get("posting_time"),
					"qty": sle.actual_qty,
					"serial_no": sle.get("serial_no"),
					"batch_no": sle.get("batch_no"),
					"company": sle.company,
					"voucher_type": sle.voucher_type,
					"voucher_no": sle.voucher_no,
				},
				raise_error_if_no_rate=False,
			)

	return rate


def get_return_against_product_fields(voucher_type):
	return_against_product_fields = {
		"Purchase Receipt": "purchase_receipt_product",
		"Purchase Invoice": "purchase_invoice_product",
		"Delivery Note": "dn_detail",
		"Sales Invoice": "sales_invoice_product",
		"Subcontracting Receipt": "subcontracting_receipt_product",
	}
	return return_against_product_fields[voucher_type]


def get_filters(
	voucher_type,
	voucher_no,
	voucher_detail_no,
	return_against,
	product_code,
	return_against_product_field,
	product_row,
):
	filters = {"voucher_type": voucher_type, "voucher_no": return_against, "product_code": product_code}

	if product_row:
		reference_voucher_detail_no = product_row.get(return_against_product_field)
	else:
		reference_voucher_detail_no = frappe.db.get_value(
			voucher_type + " Product", voucher_detail_no, return_against_product_field
		)

	if reference_voucher_detail_no:
		filters["voucher_detail_no"] = reference_voucher_detail_no

	if (
		voucher_type in ["Purchase Receipt", "Purchase Invoice"]
		and product_row
		and product_row.get("warehouse")
	):
		filters["warehouse"] = product_row.get("warehouse")

	return filters


def get_returned_serial_nos(child_doc, parent_doc, serial_no_field="serial_no"):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	return_ref_field = frappe.scrub(child_doc.doctype)
	if child_doc.doctype == "Delivery Note Product":
		return_ref_field = "dn_detail"

	serial_nos = []

	fields = [f"`{'tab' + child_doc.doctype}`.`{serial_no_field}`"]

	filters = [
		[parent_doc.doctype, "return_against", "=", parent_doc.name],
		[parent_doc.doctype, "is_return", "=", 1],
		[child_doc.doctype, return_ref_field, "=", child_doc.name],
		[parent_doc.doctype, "docstatus", "=", 1],
	]

	for row in frappe.get_all(parent_doc.doctype, fields=fields, filters=filters):
		serial_nos.extend(get_serial_nos(row.get(serial_no_field)))

	return serial_nos
