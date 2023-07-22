# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	frappe.db.sql(
		"""update tabProduct set variant_based_on = 'Product Attribute'
		where ifnull(variant_based_on, '') = ''
		and (has_variants=1 or ifnull(variant_of, '') != '')
	"""
	)
