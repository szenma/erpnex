# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _, msgprint

from erpnext import get_company_currency


def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns(filters)
	entries = get_entries(filters)
	product_details = get_product_details()
	data = []

	company_currency = get_company_currency(filters.get("company"))

	for d in entries:
		if d.stock_qty > 0 or filters.get("show_return_entries", 0):
			data.append(
				[
					d.name,
					d.customer,
					d.territory,
					d.warehouse,
					d.posting_date,
					d.product_code,
					product_details.get(d.product_code, {}).get("product_group"),
					product_details.get(d.product_code, {}).get("brand"),
					d.stock_qty,
					d.base_net_amount,
					d.sales_person,
					d.allocated_percentage,
					d.contribution_amt,
					company_currency,
				]
			)

	if data:
		total_row = [""] * len(data[0])
		data.append(total_row)

	return columns, data


def get_columns(filters):
	if not filters.get("doc_type"):
		msgprint(_("Please select the document type first"), raise_exception=1)

	columns = [
		{
			"label": _(filters["doc_type"]),
			"options": filters["doc_type"],
			"fieldname": frappe.scrub(filters["doc_type"]),
			"fieldtype": "Link",
			"width": 140,
		},
		{
			"label": _("Customer"),
			"options": "Customer",
			"fieldname": "customer",
			"fieldtype": "Link",
			"width": 140,
		},
		{
			"label": _("Territory"),
			"options": "Territory",
			"fieldname": "territory",
			"fieldtype": "Link",
			"width": 140,
		},
		{
			"label": _("Warehouse"),
			"options": "Warehouse",
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"width": 140,
		},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 140},
		{
			"label": _("Product Code"),
			"options": "Product",
			"fieldname": "product_code",
			"fieldtype": "Link",
			"width": 140,
		},
		{
			"label": _("Product Group"),
			"options": "Product Group",
			"fieldname": "product_group",
			"fieldtype": "Link",
			"width": 140,
		},
		{
			"label": _("Brand"),
			"options": "Brand",
			"fieldname": "brand",
			"fieldtype": "Link",
			"width": 140,
		},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 140},
		{
			"label": _("Amount"),
			"options": "currency",
			"fieldname": "amount",
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"label": _("Sales Person"),
			"options": "Sales Person",
			"fieldname": "sales_person",
			"fieldtype": "Link",
			"width": 140,
		},
		{"label": _("Contribution %"), "fieldname": "contribution", "fieldtype": "Float", "width": 140},
		{
			"label": _("Contribution Amount"),
			"options": "currency",
			"fieldname": "contribution_amt",
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"label": _("Currency"),
			"options": "Currency",
			"fieldname": "currency",
			"fieldtype": "Link",
			"hidden": 1,
		},
	]

	return columns


def get_entries(filters):
	date_field = filters["doc_type"] == "Sales Order" and "transaction_date" or "posting_date"
	if filters["doc_type"] == "Sales Order":
		qty_field = "delivered_qty"
	else:
		qty_field = "qty"
	conditions, values = get_conditions(filters, date_field)

	entries = frappe.db.sql(
		"""
		SELECT
			dt.name, dt.customer, dt.territory, dt.%s as posting_date, dt_product.product_code,
			st.sales_person, st.allocated_percentage, dt_product.warehouse,
		CASE
			WHEN dt.status = "Closed" THEN dt_product.%s * dt_product.conversion_factor
			ELSE dt_product.stock_qty
		END as stock_qty,
		CASE
			WHEN dt.status = "Closed" THEN (dt_product.base_net_rate * dt_product.%s * dt_product.conversion_factor)
			ELSE dt_product.base_net_amount
		END as base_net_amount,
		CASE
			WHEN dt.status = "Closed" THEN ((dt_product.base_net_rate * dt_product.%s * dt_product.conversion_factor) * st.allocated_percentage/100)
			ELSE dt_product.base_net_amount * st.allocated_percentage/100
		END as contribution_amt
		FROM
			`tab%s` dt, `tab%s Product` dt_product, `tabSales Team` st
		WHERE
			st.parent = dt.name and dt.name = dt_product.parent and st.parenttype = %s
			and dt.docstatus = 1 %s order by st.sales_person, dt.name desc
		"""
		% (
			date_field,
			qty_field,
			qty_field,
			qty_field,
			filters["doc_type"],
			filters["doc_type"],
			"%s",
			conditions,
		),
		tuple([filters["doc_type"]] + values),
		as_dict=1,
	)

	return entries


def get_conditions(filters, date_field):
	conditions = [""]
	values = []

	for field in ["company", "customer", "territory"]:
		if filters.get(field):
			conditions.append("dt.{0}=%s".format(field))
			values.append(filters[field])

	if filters.get("sales_person"):
		lft, rgt = frappe.get_value("Sales Person", filters.get("sales_person"), ["lft", "rgt"])
		conditions.append(
			"exists(select name from `tabSales Person` where lft >= {0} and rgt <= {1} and name=st.sales_person)".format(
				lft, rgt
			)
		)

	if filters.get("from_date"):
		conditions.append("dt.{0}>=%s".format(date_field))
		values.append(filters["from_date"])

	if filters.get("to_date"):
		conditions.append("dt.{0}<=%s".format(date_field))
		values.append(filters["to_date"])

	products = get_products(filters)
	if products:
		conditions.append("dt_product.product_code in (%s)" % ", ".join(["%s"] * len(products)))
		values += products

	return " and ".join(conditions), values


def get_products(filters):
	if filters.get("product_group"):
		key = "product_group"
	elif filters.get("brand"):
		key = "brand"
	else:
		key = ""

	products = []
	if key:
		products = frappe.db.sql_list(
			"""select name from tabProduct where %s = %s""" % (key, "%s"), (filters[key])
		)

	return products


def get_product_details():
	product_details = {}
	for d in frappe.db.sql("""SELECT `name`, `product_group`, `brand` FROM `tabProduct`""", as_dict=1):
		product_details.setdefault(d.name, d)

	return product_details
