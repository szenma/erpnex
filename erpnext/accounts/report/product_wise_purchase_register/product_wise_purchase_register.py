# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.utils import flt

import erpnext
from erpnext.accounts.report.product_wise_sales_register.product_wise_sales_register import (
	add_sub_total_row,
	add_total_row,
	get_grand_total,
	get_group_by_and_display_fields,
	get_group_by_conditions,
	get_tax_accounts,
)
from erpnext.accounts.report.utils import get_query_columns, get_values_for_columns


def execute(filters=None):
	return _execute(filters)


def _execute(filters=None, additional_table_columns=None):
	if not filters:
		filters = {}
	columns = get_columns(additional_table_columns, filters)

	company_currency = erpnext.get_company_currency(filters.company)

	product_list = get_products(filters, get_query_columns(additional_table_columns))
	aii_account_map = get_aii_accounts()
	if product_list:
		productised_tax, tax_columns = get_tax_accounts(
			product_list,
			columns,
			company_currency,
			doctype="Purchase Invoice",
			tax_doctype="Purchase Taxes and Charges",
		)

		scrubbed_tax_fields = {}

		for tax in tax_columns:
			scrubbed_tax_fields.update(
				{
					tax + " Rate": frappe.scrub(tax + " Rate"),
					tax + " Amount": frappe.scrub(tax + " Amount"),
				}
			)

	po_pr_map = get_purchase_receipts_against_purchase_order(product_list)

	data = []
	total_row_map = {}
	skip_total_row = 0
	prev_group_by_value = ""

	if filters.get("group_by"):
		grand_total = get_grand_total(filters, "Purchase Invoice")

	for d in product_list:
		purchase_receipt = None
		if d.purchase_receipt:
			purchase_receipt = d.purchase_receipt
		elif d.po_detail:
			purchase_receipt = ", ".join(po_pr_map.get(d.po_detail, []))

		expense_account = (
			d.unrealized_profit_loss_account or d.expense_account or aii_account_map.get(d.company)
		)

		row = {
			"product_code": d.product_code,
			"product_name": d.pi_product_name if d.pi_product_name else d.i_product_name,
			"product_group": d.pi_product_group if d.pi_product_group else d.i_product_group,
			"description": d.description,
			"invoice": d.parent,
			"posting_date": d.posting_date,
			"supplier": d.supplier,
			"supplier_name": d.supplier_name,
			**get_values_for_columns(additional_table_columns, d),
			"credit_to": d.credit_to,
			"mode_of_payment": d.mode_of_payment,
			"project": d.project,
			"company": d.company,
			"purchase_order": d.purchase_order,
			"purchase_receipt": purchase_receipt,
			"expense_account": expense_account,
			"stock_qty": d.stock_qty,
			"stock_uom": d.stock_uom,
			"rate": d.base_net_amount / d.stock_qty if d.stock_qty else d.base_net_amount,
			"amount": d.base_net_amount,
		}

		total_tax = 0
		for tax in tax_columns:
			product_tax = productised_tax.get(d.name, {}).get(tax, {})
			row.update(
				{
					scrubbed_tax_fields[tax + " Rate"]: product_tax.get("tax_rate", 0),
					scrubbed_tax_fields[tax + " Amount"]: product_tax.get("tax_amount", 0),
				}
			)
			total_tax += flt(product_tax.get("tax_amount"))

		row.update(
			{"total_tax": total_tax, "total": d.base_net_amount + total_tax, "currency": company_currency}
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
				"options": "Purchase Invoice",
				"width": 120,
			},
			{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 120},
		]
	)

	if filters.get("group_by") != "Supplier":
		columns.extend(
			[
				{
					"label": _("Supplier"),
					"fieldname": "supplier",
					"fieldtype": "Link",
					"options": "Supplier",
					"width": 120,
				},
				{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 120},
			]
		)

	if additional_table_columns:
		columns += additional_table_columns

	columns += [
		{
			"label": _("Payable Account"),
			"fieldname": "credit_to",
			"fieldtype": "Link",
			"options": "Account",
			"width": 80,
		},
		{
			"label": _("Mode Of Payment"),
			"fieldname": "mode_of_payment",
			"fieldtype": "Link",
			"options": "Mode of Payment",
			"width": 120,
		},
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
			"label": _("Purchase Order"),
			"fieldname": "purchase_order",
			"fieldtype": "Link",
			"options": "Purchase Order",
			"width": 100,
		},
		{
			"label": _("Purchase Receipt"),
			"fieldname": "purchase_receipt",
			"fieldtype": "Link",
			"options": "Purchase Receipt",
			"width": 100,
		},
		{
			"label": _("Expense Account"),
			"fieldname": "expense_account",
			"fieldtype": "Link",
			"options": "Account",
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


def get_conditions(filters):
	conditions = ""

	for opts in (
		("company", " and company=%(company)s"),
		("supplier", " and `tabPurchase Invoice`.supplier = %(supplier)s"),
		("product_code", " and `tabPurchase Invoice Product`.product_code = %(product_code)s"),
		("from_date", " and `tabPurchase Invoice`.posting_date>=%(from_date)s"),
		("to_date", " and `tabPurchase Invoice`.posting_date<=%(to_date)s"),
		("mode_of_payment", " and ifnull(mode_of_payment, '') = %(mode_of_payment)s"),
	):
		if filters.get(opts[0]):
			conditions += opts[1]

	if not filters.get("group_by"):
		conditions += (
			"ORDER BY `tabPurchase Invoice`.posting_date desc, `tabPurchase Invoice Product`.product_code desc"
		)
	else:
		conditions += get_group_by_conditions(filters, "Purchase Invoice")

	return conditions


def get_products(filters, additional_query_columns):
	conditions = get_conditions(filters)

	return frappe.db.sql(
		"""
		select
			`tabPurchase Invoice Product`.`name`, `tabPurchase Invoice Product`.`parent`,
			`tabPurchase Invoice`.posting_date, `tabPurchase Invoice`.credit_to, `tabPurchase Invoice`.company,
			`tabPurchase Invoice`.supplier, `tabPurchase Invoice`.remarks, `tabPurchase Invoice`.base_net_total,
			`tabPurchase Invoice`.unrealized_profit_loss_account,
			`tabPurchase Invoice Product`.`product_code`, `tabPurchase Invoice Product`.description,
			`tabPurchase Invoice Product`.`product_name` as pi_product_name, `tabPurchase Invoice Product`.`product_group` as pi_product_group,
			`tabProduct`.`product_name` as i_product_name, `tabProduct`.`product_group` as i_product_group,
			`tabPurchase Invoice Product`.`project`, `tabPurchase Invoice Product`.`purchase_order`,
			`tabPurchase Invoice Product`.`purchase_receipt`, `tabPurchase Invoice Product`.`po_detail`,
			`tabPurchase Invoice Product`.`expense_account`, `tabPurchase Invoice Product`.`stock_qty`,
			`tabPurchase Invoice Product`.`stock_uom`, `tabPurchase Invoice Product`.`base_net_amount`,
			`tabPurchase Invoice`.`supplier_name`, `tabPurchase Invoice`.`mode_of_payment` {0}
		from `tabPurchase Invoice`, `tabPurchase Invoice Product`, `tabProduct`
		where `tabPurchase Invoice`.name = `tabPurchase Invoice Product`.`parent` and
			`tabProduct`.name = `tabPurchase Invoice Product`.`product_code` and
			`tabPurchase Invoice`.docstatus = 1 {1}
	""".format(
			additional_query_columns, conditions
		),
		filters,
		as_dict=1,
	)


def get_aii_accounts():
	return dict(frappe.db.sql("select name, stock_received_but_not_billed from tabCompany"))


def get_purchase_receipts_against_purchase_order(product_list):
	po_pr_map = frappe._dict()
	po_product_rows = list(set(d.po_detail for d in product_list))

	if po_product_rows:
		purchase_receipts = frappe.db.sql(
			"""
			select parent, purchase_order_product
			from `tabPurchase Receipt Product`
			where docstatus=1 and purchase_order_product in (%s)
			group by purchase_order_product, parent
		"""
			% (", ".join(["%s"] * len(po_product_rows))),
			tuple(po_product_rows),
			as_dict=1,
		)

		for pr in purchase_receipts:
			po_pr_map.setdefault(pr.po_detail, []).append(pr.parent)

	return po_pr_map
