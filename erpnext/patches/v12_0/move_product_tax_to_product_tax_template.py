import json

import frappe
from frappe.model.naming import make_autoname


def execute():
	if "tax_type" not in frappe.db.get_table_columns("Product Tax"):
		return
	old_product_taxes = {}
	product_tax_templates = {}

	frappe.reload_doc("accounts", "doctype", "product_tax_template_detail", force=1)
	frappe.reload_doc("accounts", "doctype", "product_tax_template", force=1)
	existing_templates = frappe.db.sql(
		"""select template.name, details.tax_type, details.tax_rate
		from `tabProduct Tax Template` template, `tabProduct Tax Template Detail` details
		where details.parent=template.name
		""",
		as_dict=1,
	)

	if len(existing_templates):
		for d in existing_templates:
			product_tax_templates.setdefault(d.name, {})
			product_tax_templates[d.name][d.tax_type] = d.tax_rate

	for d in frappe.db.sql(
		"""select parent as product_code, tax_type, tax_rate from `tabProduct Tax`""", as_dict=1
	):
		old_product_taxes.setdefault(d.product_code, [])
		old_product_taxes[d.product_code].append(d)

	frappe.reload_doc("stock", "doctype", "product", force=1)
	frappe.reload_doc("stock", "doctype", "product_tax", force=1)
	frappe.reload_doc("selling", "doctype", "quotation_product", force=1)
	frappe.reload_doc("selling", "doctype", "sales_order_product", force=1)
	frappe.reload_doc("stock", "doctype", "delivery_note_product", force=1)
	frappe.reload_doc("accounts", "doctype", "sales_invoice_product", force=1)
	frappe.reload_doc("buying", "doctype", "supplier_quotation_product", force=1)
	frappe.reload_doc("buying", "doctype", "purchase_order_product", force=1)
	frappe.reload_doc("stock", "doctype", "purchase_receipt_product", force=1)
	frappe.reload_doc("accounts", "doctype", "purchase_invoice_product", force=1)
	frappe.reload_doc("accounts", "doctype", "accounts_settings", force=1)

	frappe.db.auto_commit_on_many_writes = True

	# for each product that have product tax rates
	for product_code in old_product_taxes.keys():
		# make current product's tax map
		product_tax_map = {}
		for d in old_product_taxes[product_code]:
			if d.tax_type not in product_tax_map:
				product_tax_map[d.tax_type] = d.tax_rate

		tax_types = []
		product_tax_template_name = get_product_tax_template(
			product_tax_templates, product_tax_map, product_code, tax_types=tax_types
		)

		# update the product tax table
		frappe.db.sql("delete from `tabProduct Tax` where parent=%s and parenttype='Product'", product_code)
		if product_tax_template_name:
			product = frappe.get_doc("Product", product_code)
			product.set("taxes", [])
			product.append("taxes", {"product_tax_template": product_tax_template_name, "tax_category": ""})
			for d in product.taxes:
				d.db_insert()

	doctypes = [
		"Quotation",
		"Sales Order",
		"Delivery Note",
		"Sales Invoice",
		"Supplier Quotation",
		"Purchase Order",
		"Purchase Receipt",
		"Purchase Invoice",
	]

	for dt in doctypes:
		for d in frappe.db.sql(
			"""select name, parenttype, parent, product_code, product_tax_rate from `tab{0} Product`
								where ifnull(product_tax_rate, '') not in ('', '{{}}')
								and product_tax_template is NULL""".format(
				dt
			),
			as_dict=1,
		):
			product_tax_map = json.loads(d.product_tax_rate)
			product_tax_template_name = get_product_tax_template(
				product_tax_templates, product_tax_map, d.product_code, d.parenttype, d.parent, tax_types=tax_types
			)
			frappe.db.set_value(dt + " Product", d.name, "product_tax_template", product_tax_template_name)

	frappe.db.auto_commit_on_many_writes = False

	settings = frappe.get_single("Accounts Settings")
	settings.add_taxes_from_product_tax_template = 0
	settings.determine_address_tax_category_from = "Billing Address"
	settings.save()


def get_product_tax_template(
	product_tax_templates, product_tax_map, product_code, parenttype=None, parent=None, tax_types=None
):
	# search for previously created product tax template by comparing tax maps
	for template, product_tax_template_map in product_tax_templates.products():
		if product_tax_map == product_tax_template_map:
			return template

	# if no product tax template found, create one
	product_tax_template = frappe.new_doc("Product Tax Template")
	product_tax_template.title = make_autoname("Product Tax Template-.####")
	product_tax_template_name = product_tax_template.title

	for tax_type, tax_rate in product_tax_map.products():
		account_details = frappe.db.get_value(
			"Account", tax_type, ["name", "account_type", "company"], as_dict=1
		)
		if account_details:
			product_tax_template.company = account_details.company
			if not product_tax_template_name:
				# set name once company is set as name is generated from company & title
				# setting name is required to update `product_tax_templates` dict
				product_tax_template_name = product_tax_template.set_new_name()
			if account_details.account_type not in (
				"Tax",
				"Chargeable",
				"Income Account",
				"Expense Account",
				"Expenses Included In Valuation",
			):
				frappe.db.set_value("Account", account_details.name, "account_type", "Chargeable")
		else:
			parts = tax_type.strip().split(" - ")
			account_name = " - ".join(parts[:-1])
			if not account_name:
				tax_type = None
			else:
				company = get_company(parts[-1], parenttype, parent)
				parent_account = frappe.get_value(
					"Account", {"account_name": account_name, "company": company}, "parent_account"
				)
				if not parent_account:
					parent_account = frappe.db.get_value(
						"Account",
						filters={"account_type": "Tax", "root_type": "Liability", "is_group": 0, "company": company},
						fieldname="parent_account",
					)
				if not parent_account:
					parent_account = frappe.db.get_value(
						"Account",
						filters={"account_type": "Tax", "root_type": "Liability", "is_group": 1, "company": company},
					)
				filters = {
					"account_name": account_name,
					"company": company,
					"account_type": "Tax",
					"parent_account": parent_account,
				}
				tax_type = frappe.db.get_value("Account", filters)
				if not tax_type:
					account = frappe.new_doc("Account")
					account.update(filters)
					try:
						account.insert()
						tax_type = account.name
					except frappe.DuplicateEntryError:
						tax_type = frappe.db.get_value(
							"Account", {"account_name": account_name, "company": company}, "name"
						)

		account_type = frappe.get_cached_value("Account", tax_type, "account_type")

		if tax_type and account_type in (
			"Tax",
			"Chargeable",
			"Income Account",
			"Expense Account",
			"Expenses Included In Valuation",
		):
			if tax_type not in tax_types:
				product_tax_template.append("taxes", {"tax_type": tax_type, "tax_rate": tax_rate})
				tax_types.append(tax_type)
			product_tax_templates.setdefault(product_tax_template_name, {})
			product_tax_templates[product_tax_template_name][tax_type] = tax_rate

	if product_tax_template.get("taxes"):
		product_tax_template.save()
		return product_tax_template.name


def get_company(company_abbr, parenttype=None, parent=None):
	if parenttype and parent:
		company = frappe.get_cached_value(parenttype, parent, "company")
	else:
		company = frappe.db.get_value("Company", filters={"abbr": company_abbr})

	if not company:
		companies = frappe.get_all("Company")
		if len(companies) == 1:
			company = companies[0].name

	return company
