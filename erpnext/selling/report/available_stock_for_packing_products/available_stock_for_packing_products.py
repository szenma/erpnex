# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe.utils import flt


def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns()
	iwq_map = get_product_warehouse_quantity_map()
	product_map = get_product_details()
	data = []
	for sbom, warehouse in iwq_map.products():
		total = 0
		total_qty = 0

		for wh, product_qty in warehouse.products():
			total += 1
			if product_map.get(sbom):
				row = [
					sbom,
					product_map.get(sbom).product_name,
					product_map.get(sbom).description,
					product_map.get(sbom).stock_uom,
					wh,
				]
				available_qty = product_qty
				total_qty += flt(available_qty)
				row += [available_qty]

				if available_qty:
					data.append(row)
					if total == len(warehouse):
						row = ["", "", "Total", "", "", total_qty]
						data.append(row)
	return columns, data


def get_columns():
	columns = [
		"Product Code:Link/Product:100",
		"Product Name::100",
		"Description::120",
		"UOM:Link/UOM:80",
		"Warehouse:Link/Warehouse:100",
		"Quantity::100",
	]

	return columns


def get_product_details():
	product_map = {}
	for product in frappe.db.sql(
		"""SELECT name, product_name, description, stock_uom
								from `tabProduct`""",
		as_dict=1,
	):
		product_map.setdefault(product.name, product)
	return product_map


def get_product_warehouse_quantity_map():
	query = """SELECT parent, warehouse, MIN(qty) AS qty
			   FROM (SELECT b.parent, bi.product_code, bi.warehouse,
							sum(bi.projected_qty) / b.qty AS qty
					 FROM tabBin AS bi, (SELECT pb.new_product_code as parent, b.product_code, b.qty, w.name
										 FROM `tabProduct Bundle Product` b, `tabWarehouse` w,
											  `tabProduct Bundle` pb
										 where b.parent = pb.name) AS b
					 WHERE bi.product_code = b.product_code
						   AND bi.warehouse = b.name
					 GROUP BY b.parent, b.product_code, bi.warehouse
					 UNION ALL
					 SELECT b.parent, b.product_code, b.name, 0 AS qty
					 FROM (SELECT pb.new_product_code as parent, b.product_code, b.qty, w.name
						   FROM `tabProduct Bundle Product` b, `tabWarehouse` w,
								`tabProduct Bundle` pb
						   where b.parent = pb.name) AS b
					 WHERE NOT EXISTS(SELECT *
									  FROM `tabBin` AS bi
									  WHERE bi.product_code = b.product_code
											AND bi.warehouse = b.name)) AS r
			   GROUP BY parent, warehouse
			   HAVING MIN(qty) != 0"""
	result = frappe.db.sql(query, as_dict=1)
	last_sbom = ""
	sbom_map = {}
	for line in result:
		if line.get("parent") != last_sbom:
			last_sbom = line.get("parent")
			actual_dict = sbom_map.setdefault(last_sbom, {})
		actual_dict.setdefault(line.get("warehouse"), line.get("qty"))
	return sbom_map
