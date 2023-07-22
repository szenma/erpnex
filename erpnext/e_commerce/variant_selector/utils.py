import frappe
from frappe.utils import cint, flt

from erpnext.e_commerce.doctype.e_commerce_settings.e_commerce_settings import (
	get_shopping_cart_settings,
)
from erpnext.e_commerce.shopping_cart.cart import _set_price_list
from erpnext.e_commerce.variant_selector.product_variants_cache import ProductVariantsCacheManager
from erpnext.utilities.product import get_price


def get_product_codes_by_attributes(attribute_filters, template_product_code=None):
	products = []

	for attribute, values in attribute_filters.products():
		attribute_values = values

		if not isinstance(attribute_values, list):
			attribute_values = [attribute_values]

		if not attribute_values:
			continue

		wheres = []
		query_values = []
		for attribute_value in attribute_values:
			wheres.append("( attribute = %s and attribute_value = %s )")
			query_values += [attribute, attribute_value]

		attribute_query = " or ".join(wheres)

		if template_product_code:
			variant_of_query = "AND t2.variant_of = %s"
			query_values.append(template_product_code)
		else:
			variant_of_query = ""

		query = """
			SELECT
				t1.parent
			FROM
				`tabProduct Variant Attribute` t1
			WHERE
				1 = 1
				AND (
					{attribute_query}
				)
				AND EXISTS (
					SELECT
						1
					FROM
						`tabProduct` t2
					WHERE
						t2.name = t1.parent
						{variant_of_query}
				)
			GROUP BY
				t1.parent
			ORDER BY
				NULL
		""".format(
			attribute_query=attribute_query, variant_of_query=variant_of_query
		)

		product_codes = set([r[0] for r in frappe.db.sql(query, query_values)])  # nosemgrep
		products.append(product_codes)

	res = list(set.intersection(*products))

	return res


@frappe.whitelist(allow_guest=True)
def get_attributes_and_values(product_code):
	"""Build a list of attributes and their possible values.
	This will ignore the values upon selection of which there cannot exist one product.
	"""
	product_cache = ProductVariantsCacheManager(product_code)
	product_variants_data = product_cache.get_product_variants_data()

	attributes = get_product_attributes(product_code)
	attribute_list = [a.attribute for a in attributes]

	valid_options = {}
	for product_code, attribute, attribute_value in product_variants_data:
		if attribute in attribute_list:
			valid_options.setdefault(attribute, set()).add(attribute_value)

	product_attribute_values = frappe.db.get_all(
		"Product Attribute Value", ["parent", "attribute_value", "idx"], order_by="parent asc, idx asc"
	)
	ordered_attribute_value_map = frappe._dict()
	for iv in product_attribute_values:
		ordered_attribute_value_map.setdefault(iv.parent, []).append(iv.attribute_value)

	# build attribute values in idx order
	for attr in attributes:
		valid_attribute_values = valid_options.get(attr.attribute, [])
		ordered_values = ordered_attribute_value_map.get(attr.attribute, [])
		attr["values"] = [v for v in ordered_values if v in valid_attribute_values]

	return attributes


@frappe.whitelist(allow_guest=True)
def get_next_attribute_and_values(product_code, selected_attributes):
	"""Find the count of Products that match the selected attributes.
	Also, find the attribute values that are not applicable for further searching.
	If less than equal to 10 products are found, return product_codes of those products.
	If one product is matched exactly, return product_code of that product.
	"""
	selected_attributes = frappe.parse_json(selected_attributes)

	product_cache = ProductVariantsCacheManager(product_code)
	product_variants_data = product_cache.get_product_variants_data()

	attributes = get_product_attributes(product_code)
	attribute_list = [a.attribute for a in attributes]
	filtered_products = get_products_with_selected_attributes(product_code, selected_attributes)

	next_attribute = None

	for attribute in attribute_list:
		if attribute not in selected_attributes:
			next_attribute = attribute
			break

	valid_options_for_attributes = frappe._dict()

	for a in attribute_list:
		valid_options_for_attributes[a] = set()

		selected_attribute = selected_attributes.get(a, None)
		if selected_attribute:
			# already selected attribute values are valid options
			valid_options_for_attributes[a].add(selected_attribute)

	for row in product_variants_data:
		product_code, attribute, attribute_value = row
		if (
			product_code in filtered_products
			and attribute not in selected_attributes
			and attribute in attribute_list
		):
			valid_options_for_attributes[attribute].add(attribute_value)

	optional_attributes = product_cache.get_optional_attributes()
	exact_match = []
	# search for exact match if all selected attributes are required attributes
	if len(selected_attributes.keys()) >= (len(attribute_list) - len(optional_attributes)):
		product_attribute_value_map = product_cache.get_product_attribute_value_map()
		for product_code, attr_dict in product_attribute_value_map.products():
			if product_code in filtered_products and set(attr_dict.keys()) == set(selected_attributes.keys()):
				exact_match.append(product_code)

	filtered_products_count = len(filtered_products)

	# get product info if exact match
	# from erpnext.e_commerce.shopping_cart.product_info import get_product_info_for_website
	if exact_match:
		cart_settings = get_shopping_cart_settings()
		product_info = get_product_variant_price_dict(exact_match[0], cart_settings)

		if product_info:
			product_info["is_stock_product"] = frappe.get_cached_value("Product", exact_match[0], "is_stock_product")
			product_info["allow_products_not_in_stock"] = cint(cart_settings.allow_products_not_in_stock)
	else:
		product_info = None

	product_id = ""
	website_warehouse = ""
	if exact_match or filtered_products:
		if exact_match and len(exact_match) == 1:
			product_id = exact_match[0]
		elif filtered_products_count == 1:
			product_id = list(filtered_products)[0]

	if product_id:
		website_warehouse = frappe.get_cached_value(
			"Website Product", {"product_code": product_id}, "website_warehouse"
		)

	available_qty = 0.0
	if website_warehouse:
		available_qty = flt(
			frappe.db.get_value(
				"Bin", {"product_code": product_id, "warehouse": website_warehouse}, "actual_qty"
			)
		)

	return {
		"next_attribute": next_attribute,
		"valid_options_for_attributes": valid_options_for_attributes,
		"filtered_products_count": filtered_products_count,
		"filtered_products": filtered_products if filtered_products_count < 10 else [],
		"exact_match": exact_match,
		"product_info": product_info,
		"available_qty": available_qty,
	}


def get_products_with_selected_attributes(product_code, selected_attributes):
	product_cache = ProductVariantsCacheManager(product_code)
	attribute_value_product_map = product_cache.get_attribute_value_product_map()

	products = []
	for attribute, value in selected_attributes.products():
		filtered_products = attribute_value_product_map.get((attribute, value), [])
		products.append(set(filtered_products))

	return set.intersection(*products)


# utilities


def get_product_attributes(product_code):
	attributes = frappe.db.get_all(
		"Product Variant Attribute",
		fields=["attribute"],
		filters={"parenttype": "Product", "parent": product_code},
		order_by="idx asc",
	)

	optional_attributes = ProductVariantsCacheManager(product_code).get_optional_attributes()

	for a in attributes:
		if a.attribute in optional_attributes:
			a.optional = True

	return attributes


def get_product_variant_price_dict(product_code, cart_settings):
	if cart_settings.enabled and cart_settings.show_price:
		is_guest = frappe.session.user == "Guest"
		# Show Price if logged in.
		# If not logged in, check if price is hidden for guest.
		if not is_guest or not cart_settings.hide_price_for_guest:
			price_list = _set_price_list(cart_settings, None)
			price = get_price(
				product_code, price_list, cart_settings.default_customer_group, cart_settings.company
			)
			return {"price": price}

	return None
