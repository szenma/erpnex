import frappe


def execute():

	# Erase all default product manufacturers that dont exist.
	product = frappe.qb.DocType("Product")
	manufacturer = frappe.qb.DocType("Manufacturer")

	(
		frappe.qb.update(product)
		.set(product.default_product_manufacturer, None)
		.left_join(manufacturer)
		.on(product.default_product_manufacturer == manufacturer.name)
		.where(manufacturer.name.isnull() & product.default_product_manufacturer.isnotnull())
	).run()
