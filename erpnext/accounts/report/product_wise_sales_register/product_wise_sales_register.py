# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.meta import get_field_precision
from frappe.utils import cstr, flt
from frappe.utils.xlsxutils import handle_html

from erpnext.accounts.report.sales_register.sales_register import get_mode_of_payments
from erpnext.accounts.report.utils import get_query_columns, get_values_for_columns
from erpnext.selling.report.product_wise_sales_history.product_wise_sales_history import (
	get_customer_details,
)


def execute(filters=None):
	return _execute(filters)


def _execute(filters=None, additional_table_columns=None, additional_conditions=None):
	if not filters:
		filters = {}
	columns = get_columns(additional_table_columns, filters)

	company_currency = frappe.get_cached_value("Company", filters.get("company"), "default_currency")

	product_list = get_products(filters, get_query_columns(additional_table_columns), additional_conditions)
	if product_list:
		productised_tax, tax_columns = get_tax_accounts(product_list, columns, company_currency)

		scrubbed_tax_fields = {}

		for tax in tax_columns:
			scrubbed_tax_fields.update(
				{
					tax + " Rate": frappe.scrub(tax + " Rate"),
					tax + " Amount": frappe.scrub(tax + " Amount"),
				}
			)

	mode_of_payments = get_mode_of_payments(set(d.parent for d in product_list))
	so_dn_map = get_delivery_notes_against_sales_order(product_list)

	data = []
	total_row_map = {}
	skip_total_row = 0
	prev_group_by_value = ""

	if filters.get("group_by"):
		grand_total = get_grand_total(filters, "Sales Invoice")

	customer_details = get_customer_details()

	for d in product_list:
		customer_record = customer_details.get(d.customer)

		delivery_note = None
		if d.delivery_note:
			delivery_note = d.delivery_note
		elif d.so_detail:
			delivery_note = ", ".join(so_dn_map.get(d.so_detail, []))

		if not delivery_note and d.update_stock:
			delivery_note = d.parent

		row = {
			"product_code": d.product_code,
			"product_name": d.si_product_name if d.si_product_name else d.i_product_name,
			"product_group": d.si_product_group if d.si_product_group else d.i_product_group,
			"description": d.description,
			"invoice": d.parent,
			"posting_date": d.posting_date,
			"customer": d.customer,
			"customer_name": customer_record.customer_name,
			"customer_group": customer_record.customer_group,
			**get_values_for_columns(additional_table_columns, d),
			"debit_to": d.debit_to,
			"mode_of_payment": ", ".join(mode_of_payments.get(d.parent, [])),
			"territory": d.territory,
			"project": d.project,
			"company": d.company,
			"sales_order": d.sales_order,
			"delivery_note": d.delivery_note,
			"income_account": d.unrealized_profit_loss_account
			if d.is_internal_customer == 1
			else d.income_account,
			"cost_center": d.cost_center,
			"stock_qty": d.stock_qty,
			"stock_uom": d.stock_uom,
		}

		if d.stock_uom != d.uom and d.stock_qty:
			row.update({"rate": (d.base_net_rate * d.qty) / d.stock_qty, "amount": d.base_net_amount})
		else:
			row.update({"rate": d.base_net_rate, "amount": d.base_net_amount})

		total_tax = 0
		total_other_charges = 0
		for tax in tax_columns:
			product_tax = productised_tax.get(d.name, {}).get(tax, {})
			row.update(
				{
					scrubbed_tax_fields[tax + " Rate"]: product_tax.get("tax_rate", 0),
					scrubbed_tax_fields[tax + " Amount"]: product_tax.get("tax_amount", 0),
				}
			)
			if product_tax.get("is_other_charges"):
				total_other_charges += flt(product_tax.get("tax_amount"))
			else:
				total_tax += flt(product_tax.get("tax_amount"))

		row.update(
			{
				"total_tax": total_tax,
				"total_other_charges": total_other_charges,
				"total": d.base_net_amount + total_tax,
				"currency": company_currency,
			}
		)

		if filters.get("group_by"):
			row.update({"percent_gt": flt(row["total"] / grand_total) * 100})
			group_by_field, subtotal_display_field = get_group_by_and_display_fields(filters)
			data, prev_group_by_value = add_total_row(
				data,
				filters,
				prev_group_by_value,
				d,
				total_row_map,
				group_by_field,
				subtotal_display_field,
				grand_total,
				tax_columns,
			)
			add_sub_total_row(row, total_row_map, d.get(group_by_field, ""), tax_columns)

		data.append(row)

	if filters.get("group_by") and product_list:
		total_row = total_row_map.get(prev_group_by_value or d.get("product_name"))
		total_row["percent_gt"] = flt(total_row["total"] / grand_total * 100)
		data.append(total_row)
		data.append({})
		add_sub_total_row(total_row, total_row_map, "total_row", tax_columns)
		data.append(total_row_map.get("total_row"))
		skip_total_row = 1

	return columns, data, None, None, None, skip_total_row


def get_columns(additional_table_columns, filters):
	columns = []

	if filters.get("group_by") != ("Product"):
		columns.extend(
			[
				{
					"label": _("Product Code"),
					"fieldname": "product_code",
					"fieldtype": "Link",
					"options": "Product",
					"width": 120,
				},
				{"label": _("Product Name"), "fieldname": "product_name", "fieldtype": "Data", "width": 120},
			]
		)

	if filters.get("group_by") not in ("Product", "Product Group"):
		columns.extend(
			[
				{
					"label": _("Product Group"),
					"fieldname": "product_group",
					"fieldtype": "Link",
					"options": "Product Group",
					"width": 120,
				}
			]
		)

	columns.extend(
		[
			{"label": _("Description"), "fieldname": "description", "fieldtype": "Data", "width": 150},
			{
				"label": _("Invoice"),
				"fieldname": "invoice",
				"fieldtype": "Link",
				"options": "Sales Invoice",
				"width": 120,
			},
			{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 120},
		]
	)

	if filters.get("group_by") != "Customer":
		columns.extend(
			[
				{
					"label": _("Customer Group"),
					"fieldname": "customer_group",
					"fieldtype": "Link",
					"options": "Customer Group",
					"width": 120,
				}
			]
		)

	if filters.get("group_by") not in ("Customer", "Customer Group"):
		columns.extend(
			[
				{
					"label": _("Customer"),
					"fieldname": "customer",
					"fieldtype": "Link",
					"options": "Customer",
					"width": 120,
				},
				{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 120},
			]
		)

	if additional_table_columns:
		columns += additional_table_columns

	columns += [
		{
			"label": _("Receivable Account"),
			"fieldname": "debit_to",
			"fieldtype": "Link",
			"options": "Account",
			"width": 80,
		},
		{
			"label": _("Mode Of Payment"),
			"fieldname": "mode_of_payment",
			"fieldtype": "Data",
			"width": 120,
		},
	]

	if filters.get("group_by") != "Territory":
		columns.extend(
			[
				{
					"label": _("Territory"),
					"fieldname": "territory",
					"fieldtype": "Link",
					"options": "Territory",
					"width": 80,
				}
			]
		)

	columns += [
		{
			"label": _("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 80,
		},
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 80,
		},
		{
			"label": _("Sales Order"),
			"fieldname": "sales_order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 100,
		},
		{
			"label": _("Delivery Note"),
			"fieldname": "delivery_note",
			"fieldtype": "Link",
			"options": "Delivery Note",
			"width": 100,
		},
		{
			"label": _("Income Account"),
			"fieldname": "income_account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 100,
		},
		{
			"label": _("Cost Center"),
			"fieldname": "cost_center",
			"fieldtype": "Link",
			"options": "Cost Center",
			"width": 100,
		},
		{"label": _("Stock Qty"), "fieldname": "stock_qty", "fieldtype": "Float", "width": 100},
		{
			"label": _("Stock UOM"),
			"fieldname": "stock_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 100,
		},
		{
			"label": _("Rate"),
			"fieldname": "rate",
			"fieldtype": "Float",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Amount"),
			"fieldname": "amount",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
	]

	if filters.get("group_by"):
		columns.append(
			{"label": _("% Of Grand Total"), "fieldname": "percent_gt", "fieldtype": "Float", "width": 80}
		)

	return columns


def get_conditions(filters, additional_conditions=None):
	conditions = ""

	for opts in (
		("company", " and company=%(company)s"),
		("customer", " and `tabSales Invoice`.customer = %(customer)s"),
		("product_code", " and `tabSales Invoice Product`.product_code = %(product_code)s"),
		("from_date", " and `tabSales Invoice`.posting_date>=%(from_date)s"),
		("to_date", " and `tabSales Invoice`.posting_date<=%(to_date)s"),
	):
		if filters.get(opts[0]):
			conditions += opts[1]

	if additional_conditions:
		conditions += additional_conditions

	if filters.get("mode_of_payment"):
		conditions += """ and exists(select name from `tabSales Invoice Payment`
			where parent=`tabSales Invoice`.name
				and ifnull(`tabSales Invoice Payment`.mode_of_payment, '') = %(mode_of_payment)s)"""

	if filters.get("warehouse"):
		conditions += """and ifnull(`tabSales Invoice Product`.warehouse, '') = %(warehouse)s"""

	if filters.get("brand"):
		conditions += """and ifnull(`tabSales Invoice Product`.brand, '') = %(brand)s"""

	if filters.get("product_group"):
		conditions += """and ifnull(`tabSales Invoice Product`.product_group, '') = %(product_group)s"""

	if not filters.get("group_by"):
		conditions += (
			"ORDER BY `tabSales Invoice`.posting_date desc, `tabSales Invoice Product`.product_group desc"
		)
	else:
		conditions += get_group_by_conditions(filters, "Sales Invoice")

	return conditions


def get_group_by_conditions(filters, doctype):
	if filters.get("group_by") == "Invoice":
		return "ORDER BY `tab{0} Product`.parent desc".format(doctype)
	elif filters.get("group_by") == "Product":
		return "ORDER BY `tab{0} Product`.`product_code`".format(doctype)
	elif filters.get("group_by") == "Product Group":
		return "ORDER BY `tab{0} Product`.{1}".format(doctype, frappe.scrub(filters.get("group_by")))
	elif filters.get("group_by") in ("Customer", "Customer Group", "Territory", "Supplier"):
		return "ORDER BY `tab{0}`.{1}".format(doctype, frappe.scrub(filters.get("group_by")))


def get_products(filters, additional_query_columns, additional_conditions=None):
	conditions = get_conditions(filters, additional_conditions)

	return frappe.db.sql(
		"""
		select
			`tabSales Invoice Product`.name, `tabSales Invoice Product`.parent,
			`tabSales Invoice`.posting_date, `tabSales Invoice`.debit_to,
			`tabSales Invoice`.unrealized_profit_loss_account,
			`tabSales Invoice`.is_internal_customer,
			`tabSales Invoice`.customer, `tabSales Invoice`.remarks,
			`tabSales Invoice`.territory, `tabSales Invoice`.company, `tabSales Invoice`.base_net_total,
			`tabSales Invoice Product`.project,
			`tabSales Invoice Product`.product_code, `tabSales Invoice Product`.description,
			`tabSales Invoice Product`.`product_name`, `tabSales Invoice Product`.`product_group`,
			`tabSales Invoice Product`.`product_name` as si_product_name, `tabSales Invoice Product`.`product_group` as si_product_group,
			`tabProduct`.`product_name` as i_product_name, `tabProduct`.`product_group` as i_product_group,
			`tabSales Invoice Product`.sales_order, `tabSales Invoice Product`.delivery_note,
			`tabSales Invoice Product`.income_account, `tabSales Invoice Product`.cost_center,
			`tabSales Invoice Product`.stock_qty, `tabSales Invoice Product`.stock_uom,
			`tabSales Invoice Product`.base_net_rate, `tabSales Invoice Product`.base_net_amount,
			`tabSales Invoice`.customer_name, `tabSales Invoice`.customer_group, `tabSales Invoice Product`.so_detail,
			`tabSales Invoice`.update_stock, `tabSales Invoice Product`.uom, `tabSales Invoice Product`.qty {0}
		from `tabSales Invoice`, `tabSales Invoice Product`, `tabProduct`
		where `tabSales Invoice`.name = `tabSales Invoice Product`.parent and
			`tabProduct`.name = `tabSales Invoice Product`.`product_code` and
			`tabSales Invoice`.docstatus = 1 {1}
		""".format(
			additional_query_columns, conditions
		),
		filters,
		as_dict=1,
	)  # nosec


def get_delivery_notes_against_sales_order(product_list):
	so_dn_map = frappe._dict()
	so_product_rows = list(set([d.so_detail for d in product_list]))

	if so_product_rows:
		delivery_notes = frappe.db.sql(
			"""
			select parent, so_detail
			from `tabDelivery Note Product`
			where docstatus=1 and so_detail in (%s)
			group by so_detail, parent
		"""
			% (", ".join(["%s"] * len(so_product_rows))),
			tuple(so_product_rows),
			as_dict=1,
		)

		for dn in delivery_notes:
			so_dn_map.setdefault(dn.so_detail, []).append(dn.parent)

	return so_dn_map


def get_grand_total(filters, doctype):

	return frappe.db.sql(
		""" SELECT
		SUM(`tab{0}`.base_grand_total)
		FROM `tab{0}`
		WHERE `tab{0}`.docstatus = 1
		and posting_date between %s and %s
	""".format(
			doctype
		),
		(filters.get("from_date"), filters.get("to_date")),
	)[0][
		0
	]  # nosec


def get_tax_accounts(
	product_list,
	columns,
	company_currency,
	doctype="Sales Invoice",
	tax_doctype="Sales Taxes and Charges",
):
	import json

	product_row_map = {}
	tax_columns = []
	invoice_product_row = {}
	productised_tax = {}
	add_deduct_tax = "charge_type"

	tax_amount_precision = (
		get_field_precision(
			frappe.get_meta(tax_doctype).get_field("tax_amount"), currency=company_currency
		)
		or 2
	)

	for d in product_list:
		invoice_product_row.setdefault(d.parent, []).append(d)
		product_row_map.setdefault(d.parent, {}).setdefault(d.product_code or d.product_name, []).append(d)

	conditions = ""
	if doctype == "Purchase Invoice":
		conditions = " and category in ('Total', 'Valuation and Total') and base_tax_amount_after_discount_amount != 0"
		add_deduct_tax = "add_deduct_tax"

	tax_details = frappe.db.sql(
		"""
		select
			name, parent, description, product_wise_tax_detail, account_head,
			charge_type, {add_deduct_tax}, base_tax_amount_after_discount_amount
		from `tab%s`
		where
			parenttype = %s and docstatus = 1
			and (description is not null and description != '')
			and parent in (%s)
			%s
		order by description
	""".format(
			add_deduct_tax=add_deduct_tax
		)
		% (tax_doctype, "%s", ", ".join(["%s"] * len(invoice_product_row)), conditions),
		tuple([doctype] + list(invoice_product_row)),
	)

	account_doctype = frappe.qb.DocType("Account")

	query = (
		frappe.qb.from_(account_doctype)
		.select(account_doctype.name)
		.where((account_doctype.account_type == "Tax"))
	)

	tax_accounts = query.run()

	for (
		name,
		parent,
		description,
		product_wise_tax_detail,
		account_head,
		charge_type,
		add_deduct_tax,
		tax_amount,
	) in tax_details:
		description = handle_html(description)
		if description not in tax_columns and tax_amount:
			# as description is text editor earlier and markup can break the column convention in reports
			tax_columns.append(description)

		if product_wise_tax_detail:
			try:
				product_wise_tax_detail = json.loads(product_wise_tax_detail)

				for product_code, tax_data in product_wise_tax_detail.products():
					productised_tax.setdefault(product_code, frappe._dict())

					if isinstance(tax_data, list):
						tax_rate, tax_amount = tax_data
					else:
						tax_rate = tax_data
						tax_amount = 0

					if charge_type == "Actual" and not tax_rate:
						tax_rate = "NA"

					product_net_amount = sum(
						[flt(d.base_net_amount) for d in product_row_map.get(parent, {}).get(product_code, [])]
					)

					for d in product_row_map.get(parent, {}).get(product_code, []):
						product_tax_amount = (
							flt((tax_amount * d.base_net_amount) / product_net_amount) if product_net_amount else 0
						)
						if product_tax_amount:
							tax_value = flt(product_tax_amount, tax_amount_precision)
							tax_value = (
								tax_value * -1
								if (doctype == "Purchase Invoice" and add_deduct_tax == "Deduct")
								else tax_value
							)

							productised_tax.setdefault(d.name, {})[description] = frappe._dict(
								{
									"tax_rate": tax_rate,
									"tax_amount": tax_value,
									"is_other_charges": 0 if tuple([account_head]) in tax_accounts else 1,
								}
							)

			except ValueError:
				continue
		elif charge_type == "Actual" and tax_amount:
			for d in invoice_product_row.get(parent, []):
				productised_tax.setdefault(d.name, {})[description] = frappe._dict(
					{
						"tax_rate": "NA",
						"tax_amount": flt((tax_amount * d.base_net_amount) / d.base_net_total, tax_amount_precision),
					}
				)

	tax_columns.sort()
	for desc in tax_columns:
		columns.append(
			{
				"label": _(desc + " Rate"),
				"fieldname": frappe.scrub(desc + " Rate"),
				"fieldtype": "Float",
				"width": 100,
			}
		)

		columns.append(
			{
				"label": _(desc + " Amount"),
				"fieldname": frappe.scrub(desc + " Amount"),
				"fieldtype": "Currency",
				"options": "currency",
				"width": 100,
			}
		)

	columns += [
		{
			"label": _("Total Tax"),
			"fieldname": "total_tax",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Total Other Charges"),
			"fieldname": "total_other_charges",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Total"),
			"fieldname": "total",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Currency",
			"width": 80,
			"hidden": 1,
		},
	]

	return productised_tax, tax_columns


def add_total_row(
	data,
	filters,
	prev_group_by_value,
	product,
	total_row_map,
	group_by_field,
	subtotal_display_field,
	grand_total,
	tax_columns,
):
	if prev_group_by_value != product.get(group_by_field, ""):
		if prev_group_by_value:
			total_row = total_row_map.get(prev_group_by_value)
			data.append(total_row)
			data.append({})
			add_sub_total_row(total_row, total_row_map, "total_row", tax_columns)

		prev_group_by_value = product.get(group_by_field, "")

		total_row_map.setdefault(
			product.get(group_by_field, ""),
			{
				subtotal_display_field: get_display_value(filters, group_by_field, product),
				"stock_qty": 0.0,
				"amount": 0.0,
				"bold": 1,
				"total_tax": 0.0,
				"total": 0.0,
				"percent_gt": 0.0,
			},
		)

		total_row_map.setdefault(
			"total_row",
			{
				subtotal_display_field: "Total",
				"stock_qty": 0.0,
				"amount": 0.0,
				"bold": 1,
				"total_tax": 0.0,
				"total": 0.0,
				"percent_gt": 0.0,
			},
		)

	return data, prev_group_by_value


def get_display_value(filters, group_by_field, product):
	if filters.get("group_by") == "Product":
		if product.get("product_code") != product.get("product_name"):
			value = (
				cstr(product.get("product_code"))
				+ "<br><br>"
				+ "<span style='font-weight: normal'>"
				+ cstr(product.get("product_name"))
				+ "</span>"
			)
		else:
			value = product.get("product_code", "")
	elif filters.get("group_by") in ("Customer", "Supplier"):
		party = frappe.scrub(filters.get("group_by"))
		if product.get(party) != product.get(party + "_name"):
			value = (
				product.get(party)
				+ "<br><br>"
				+ "<span style='font-weight: normal'>"
				+ product.get(party + "_name")
				+ "</span>"
			)
		else:
			value = product.get(party)
	else:
		value = product.get(group_by_field)

	return value


def get_group_by_and_display_fields(filters):
	if filters.get("group_by") == "Product":
		group_by_field = "product_code"
		subtotal_display_field = "invoice"
	elif filters.get("group_by") == "Invoice":
		group_by_field = "parent"
		subtotal_display_field = "product_code"
	else:
		group_by_field = frappe.scrub(filters.get("group_by"))
		subtotal_display_field = "product_code"

	return group_by_field, subtotal_display_field


def add_sub_total_row(product, total_row_map, group_by_value, tax_columns):
	total_row = total_row_map.get(group_by_value)
	total_row["stock_qty"] += product["stock_qty"]
	total_row["amount"] += product["amount"]
	total_row["total_tax"] += product["total_tax"]
	total_row["total"] += product["total"]
	total_row["percent_gt"] += product["percent_gt"]

	for tax in tax_columns:
		total_row.setdefault(frappe.scrub(tax + " Amount"), 0.0)
		total_row[frappe.scrub(tax + " Amount")] += flt(product[frappe.scrub(tax + " Amount")])
