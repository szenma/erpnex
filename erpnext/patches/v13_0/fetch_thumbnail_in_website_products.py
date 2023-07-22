import frappe


def execute():
	if frappe.db.has_column("Product", "thumbnail"):
		website_product = frappe.qb.DocType("Website Product").as_("wi")
		product = frappe.qb.DocType("Product")

		frappe.qb.update(website_product).inner_join(product).on(website_product.product_code == product.product_code).set(
			website_product.thumbnail, product.thumbnail
		).where(website_product.website_image.notnull() & website_product.thumbnail.isnull()).run()
