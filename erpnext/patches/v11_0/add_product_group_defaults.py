# Copyright (c) 2018, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	"""

	Fields to move from product group to product defaults child table
	[ default_cost_center, default_expense_account, default_income_account ]

	"""

	frappe.reload_doc("stock", "doctype", "product_default")
	frappe.reload_doc("setup", "doctype", "product_group")

	companies = frappe.get_all("Company")
	product_groups = frappe.db.sql(
		"""select name, default_income_account, default_expense_account,\
		default_cost_center from `tabProduct Group`""",
		as_dict=True,
	)

	if len(companies) == 1:
		for product_group in product_groups:
			doc = frappe.get_doc("Product Group", product_group.get("name"))
			product_group_defaults = []
			product_group_defaults.append(
				{
					"company": companies[0].name,
					"income_account": product_group.get("default_income_account"),
					"expense_account": product_group.get("default_expense_account"),
					"buying_cost_center": product_group.get("default_cost_center"),
					"selling_cost_center": product_group.get("default_cost_center"),
				}
			)
			doc.extend("product_group_defaults", product_group_defaults)
			for child_doc in doc.product_group_defaults:
				child_doc.db_insert()
	else:
		product_group_dict = {
			"default_expense_account": ["expense_account"],
			"default_income_account": ["income_account"],
			"default_cost_center": ["buying_cost_center", "selling_cost_center"],
		}
		for product_group in product_groups:
			product_group_defaults = []

			def insert_into_product_defaults(doc_field_name, doc_field_value, company):
				for d in product_group_defaults:
					if d.get("company") == company:
						d[doc_field_name[0]] = doc_field_value
						if len(doc_field_name) > 1:
							d[doc_field_name[1]] = doc_field_value
						return

				product_group_defaults.append({"company": company, doc_field_name[0]: doc_field_value})

				if len(doc_field_name) > 1:
					product_group_defaults[len(product_group_defaults) - 1][doc_field_name[1]] = doc_field_value

			for d in [
				["default_expense_account", "Account"],
				["default_income_account", "Account"],
				["default_cost_center", "Cost Center"],
			]:
				if product_group.get(d[0]):
					company = frappe.get_value(d[1], product_group.get(d[0]), "company", cache=True)
					doc_field_name = product_group_dict.get(d[0])

					insert_into_product_defaults(doc_field_name, product_group.get(d[0]), company)

			doc = frappe.get_doc("Product Group", product_group.get("name"))
			doc.extend("product_group_defaults", product_group_defaults)
			for child_doc in doc.product_group_defaults:
				child_doc.db_insert()
