import frappe

from erpnext.e_commerce.doctype.website_product.website_product import make_website_product


def execute():
	frappe.reload_doc("e_commerce", "doctype", "website_product")
	frappe.reload_doc("e_commerce", "doctype", "website_product_tabbed_section")
	frappe.reload_doc("e_commerce", "doctype", "website_offer")
	frappe.reload_doc("e_commerce", "doctype", "recommended_products")
	frappe.reload_doc("e_commerce", "doctype", "e_commerce_settings")
	frappe.reload_doc("stock", "doctype", "product")

	product_fields = [
		"product_code",
		"product_name",
		"product_group",
		"stock_uom",
		"brand",
		"has_variants",
		"variant_of",
		"description",
		"weightage",
	]
	web_fields_to_map = [
		"route",
		"slideshow",
		"website_image_alt",
		"website_warehouse",
		"web_long_description",
		"website_content",
		"website_image",
		"thumbnail",
	]

	# get all valid columns (fields) from Product master DB schema
	product_table_fields = frappe.db.sql("desc `tabProduct`", as_dict=1)  # nosemgrep
	product_table_fields = [d.get("Field") for d in product_table_fields]

	# prepare fields to query from Product, check if the web field exists in Product master
	web_query_fields = []
	for web_field in web_fields_to_map:
		if web_field in product_table_fields:
			web_query_fields.append(web_field)
			product_fields.append(web_field)

	# check if the filter fields exist in Product master
	or_filters = {}
	for field in ["show_in_website", "show_variant_in_website"]:
		if field in product_table_fields:
			or_filters[field] = 1

	if not web_query_fields or not or_filters:
		# web fields to map are not present in Product master schema
		# most likely a fresh installation that doesnt need this patch
		return

	products = frappe.db.get_all("Product", fields=product_fields, or_filters=or_filters)
	total_count = len(products)

	for count, product in enumerate(products, start=1):
		if frappe.db.exists("Website Product", {"product_code": product.product_code}):
			continue

		# make new website product from product (publish product)
		website_product = make_website_product(product, save=False)
		website_product.ranking = product.get("weightage")

		for field in web_fields_to_map:
			website_product.update({field: product.get(field)})

		website_product.save()

		# move Website Product Group & Website Specification table to Website Product
		for doctype in ("Website Product Group", "Product Website Specification"):
			frappe.db.set_value(
				doctype,
				{"parenttype": "Product", "parent": product.product_code},  # filters
				{"parenttype": "Website Product", "parent": website_product.name},  # value dict
			)

		if count % 20 == 0:  # commit after every 20 products
			frappe.db.commit()

		frappe.utils.update_progress_bar("Creating Website Products", count, total_count)
