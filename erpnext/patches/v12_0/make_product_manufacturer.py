# Copyright (c) 2017, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	frappe.reload_doc("stock", "doctype", "product_manufacturer")

	product_manufacturer = []
	for d in frappe.db.sql(
		""" SELECT name, manufacturer, manufacturer_part_no, creation, owner
		FROM `tabProduct` WHERE manufacturer is not null and manufacturer != ''""",
		as_dict=1,
	):
		product_manufacturer.append(
			(
				frappe.generate_hash("", 10),
				d.name,
				d.manufacturer,
				d.manufacturer_part_no,
				d.creation,
				d.owner,
			)
		)

	if product_manufacturer:
		frappe.db.sql(
			"""
			INSERT INTO `tabProduct Manufacturer`
			(`name`, `product_code`, `manufacturer`, `manufacturer_part_no`, `creation`, `owner`)
			VALUES {}""".format(
				", ".join(["%s"] * len(product_manufacturer))
			),
			tuple(product_manufacturer),
		)
