# Copyright (c) 2018, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	"""

	Fields to move from the product to product defaults child table
	[ default_warehouse, buying_cost_center, expense_account, selling_cost_center, income_account ]

	"""
	if not frappe.db.has_column("Product", "default_warehouse"):
		return

	frappe.reload_doc("stock", "doctype", "product_default")
	frappe.reload_doc("stock", "doctype", "product")

	companies = frappe.get_all("Company")
	if len(companies) == 1 and not frappe.get_all("Product Default", limit=1):
		try:
			frappe.db.sql(
				"""
					INSERT INTO `tabProduct Default`
						(name, parent, parenttype, parentfield, idx, company, default_warehouse,
						buying_cost_center, selling_cost_center, expense_account, income_account, default_supplier)
					SELECT
						SUBSTRING(SHA2(name,224), 1, 10) as name, name as parent, 'Product' as parenttype,
						'product_defaults' as parentfield, 1 as idx, %s as company, default_warehouse,
						buying_cost_center, selling_cost_center, expense_account, income_account, default_supplier
					FROM `tabProduct`;
			""",
				companies[0].name,
			)
		except Exception:
			pass
	else:
		product_details = frappe.db.sql(
			""" SELECT name, default_warehouse,
				buying_cost_center, expense_account, selling_cost_center, income_account
			FROM tabProduct
			WHERE
				name not in (select distinct parent from `tabProduct Default`) and ifnull(disabled, 0) = 0""",
			as_dict=1,
		)

		products_default_data = {}
		for product_data in product_details:
			for d in [
				["default_warehouse", "Warehouse"],
				["expense_account", "Account"],
				["income_account", "Account"],
				["buying_cost_center", "Cost Center"],
				["selling_cost_center", "Cost Center"],
			]:
				if product_data.get(d[0]):
					company = frappe.get_value(d[1], product_data.get(d[0]), "company", cache=True)

					if product_data.name not in products_default_data:
						products_default_data[product_data.name] = {}

					company_wise_data = products_default_data[product_data.name]

					if company not in company_wise_data:
						company_wise_data[company] = {}

					default_data = company_wise_data[company]
					default_data[d[0]] = product_data.get(d[0])

		to_insert_data = []

		# products_default_data data structure will be as follow
		# {
		# 	'product_code 1': {'company 1': {'default_warehouse': 'Test Warehouse 1'}},
		# 	'product_code 2': {
		# 		'company 1': {'default_warehouse': 'Test Warehouse 1'},
		# 		'company 2': {'default_warehouse': 'Test Warehouse 1'}
		# 	}
		# }

		for product_code, companywise_product_data in products_default_data.products():
			for company, product_default_data in companywise_product_data.products():
				to_insert_data.append(
					(
						frappe.generate_hash("", 10),
						product_code,
						"Product",
						"product_defaults",
						company,
						product_default_data.get("default_warehouse"),
						product_default_data.get("expense_account"),
						product_default_data.get("income_account"),
						product_default_data.get("buying_cost_center"),
						product_default_data.get("selling_cost_center"),
					)
				)

		if to_insert_data:
			frappe.db.sql(
				"""
				INSERT INTO `tabProduct Default`
				(
					`name`, `parent`, `parenttype`, `parentfield`, `company`, `default_warehouse`,
					`expense_account`, `income_account`, `buying_cost_center`, `selling_cost_center`
				)
				VALUES {}
			""".format(
					", ".join(["%s"] * len(to_insert_data))
				),
				tuple(to_insert_data),
			)
