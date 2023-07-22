# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import flt

from erpnext.e_commerce.doctype.product_review.product_review import get_customer
from erpnext.e_commerce.shopping_cart.product_info import get_product_info_for_website
from erpnext.utilities.product import get_non_stock_product_status


class ProductQuery:
	"""Query engine for product listing

	Attributes:
	        fields (list): Fields to fetch in query
	        conditions (string): Conditions for query building
	        or_conditions (string): Search conditions
	        page_length (Int): Length of page for the query
	        settings (Document): E Commerce Settings DocType
	"""

	def __init__(self):
		self.settings = frappe.get_doc("E Commerce Settings")
		self.page_length = self.settings.products_per_page or 20

		self.or_filters = []
		self.filters = [["published", "=", 1]]
		self.fields = [
			"web_product_name",
			"name",
			"product_name",
			"product_code",
			"website_image",
			"variant_of",
			"has_variants",
			"product_group",
			"web_long_description",
			"short_description",
			"route",
			"website_warehouse",
			"ranking",
			"on_backorder",
		]

	def query(self, attributes=None, fields=None, search_term=None, start=0, product_group=None):
		"""
		Args:
		        attributes (dict, optional): Product Attribute filters
		        fields (dict, optional): Field level filters
		        search_term (str, optional): Search term to lookup
		        start (int, optional): Page start

		Returns:
		        dict: Dict containing products, product count & discount range
		"""
		# track if discounts included in field filters
		self.filter_with_discount = bool(fields.get("discount"))
		result, discount_list, website_product_groups, cart_products, count = [], [], [], [], 0

		if fields:
			self.build_fields_filters(fields)
		if product_group:
			self.build_product_group_filters(product_group)
		if search_term:
			self.build_search_filters(search_term)
		if self.settings.hide_variants:
			self.filters.append(["variant_of", "is", "not set"])

		# query results
		if attributes:
			result, count = self.query_products_with_attributes(attributes, start)
		else:
			result, count = self.query_products(start=start)

		# sort combined results by ranking
		result = sorted(result, key=lambda x: x.get("ranking"), reverse=True)

		if self.settings.enabled:
			cart_products = self.get_cart_products()

		result, discount_list = self.add_display_details(result, discount_list, cart_products)

		discounts = []
		if discount_list:
			discounts = [min(discount_list), max(discount_list)]

		result = self.filter_results_by_discount(fields, result)

		return {"products": result, "products_count": count, "discounts": discounts}

	def query_products(self, start=0):
		"""Build a query to fetch Website Products based on field filters."""
		# MySQL does not support offset without limit,
		# frappe does not accept two parameters for limit
		# https://dev.mysql.com/doc/refman/8.0/en/select.html#id4651989
		count_products = frappe.db.get_all(
			"Website Product",
			filters=self.filters,
			or_filters=self.or_filters,
			limit_page_length=184467440737095516,
			limit_start=start,  # get all products from this offset for total count ahead
			order_by="ranking desc",
		)
		count = len(count_products)

		# If discounts included, return all rows.
		# Slice after filtering rows with discount (See `filter_results_by_discount`).
		# Slicing before hand will miss discounted products on the 3rd or 4th page.
		# Discounts are fetched on computing Pricing Rules so we cannot query them directly.
		page_length = 184467440737095516 if self.filter_with_discount else self.page_length

		products = frappe.db.get_all(
			"Website Product",
			fields=self.fields,
			filters=self.filters,
			or_filters=self.or_filters,
			limit_page_length=page_length,
			limit_start=start,
			order_by="ranking desc",
		)

		return products, count

	def query_products_with_attributes(self, attributes, start=0):
		"""Build a query to fetch Website Products based on field & attribute filters."""
		product_codes = []

		for attribute, values in attributes.products():
			if not isinstance(values, list):
				values = [values]

			# get products that have selected attribute & value
			product_code_list = frappe.db.get_all(
				"Product",
				fields=["product_code"],
				filters=[
					["published_in_website", "=", 1],
					["Product Variant Attribute", "attribute", "=", attribute],
					["Product Variant Attribute", "attribute_value", "in", values],
				],
			)
			product_codes.append({x.product_code for x in product_code_list})

		if product_codes:
			product_codes = list(set.intersection(*product_codes))
			self.filters.append(["product_code", "in", product_codes])

		products, count = self.query_products(start=start)

		return products, count

	def build_fields_filters(self, filters):
		"""Build filters for field values

		Args:
		        filters (dict): Filters
		"""
		for field, values in filters.products():
			if not values or field == "discount":
				continue

			# handle multiselect fields in filter addition
			meta = frappe.get_meta("Website Product", cached=True)
			df = meta.get_field(field)
			if df.fieldtype == "Table MultiSelect":
				child_doctype = df.options
				child_meta = frappe.get_meta(child_doctype, cached=True)
				fields = child_meta.get("fields")
				if fields:
					self.filters.append([child_doctype, fields[0].fieldname, "IN", values])
			elif isinstance(values, list):
				# If value is a list use `IN` query
				self.filters.append([field, "in", values])
			else:
				# `=` will be faster than `IN` for most cases
				self.filters.append([field, "=", values])

	def build_product_group_filters(self, product_group):
		"Add filters for Product group page and include Website Product Groups."
		from erpnext.setup.doctype.product_group.product_group import get_child_groups_for_website

		product_group_filters = []

		product_group_filters.append(["Website Product", "product_group", "=", product_group])
		# Consider Website Product Groups
		product_group_filters.append(["Website Product Group", "product_group", "=", product_group])

		if frappe.db.get_value("Product Group", product_group, "include_descendants"):
			# include child product group's products as well
			# eg. Group Node A, will show products of child 1 and child 2 as well
			# on it's web page
			include_groups = get_child_groups_for_website(product_group, include_self=True)
			include_groups = [x.name for x in include_groups]
			product_group_filters.append(["Website Product", "product_group", "in", include_groups])

		self.or_filters.extend(product_group_filters)

	def build_search_filters(self, search_term):
		"""Query search term in specified fields

		Args:
		        search_term (str): Search candidate
		"""
		# Default fields to search from
		default_fields = {"product_code", "product_name", "web_long_description", "product_group"}

		# Get meta search fields
		meta = frappe.get_meta("Website Product")
		meta_fields = set(meta.get_search_fields())

		# Join the meta fields and default fields set
		search_fields = default_fields.union(meta_fields)
		if frappe.db.count("Website Product", cache=True) > 50000:
			search_fields.discard("web_long_description")

		# Build or filters for query
		search = "%{}%".format(search_term)
		for field in search_fields:
			self.or_filters.append([field, "like", search])

	def add_display_details(self, result, discount_list, cart_products):
		"""Add price and availability details in result."""
		for product in result:
			product_info = get_product_info_for_website(product.product_code, skip_quotation_creation=True).get(
				"product_info"
			)

			if product_info and product_info["price"]:
				# update/mutate product and discount_list objects
				self.get_price_discount_info(product, product_info["price"], discount_list)

			if self.settings.show_stock_availability:
				self.get_stock_availability(product)

			product.in_cart = product.product_code in cart_products

			product.wished = False
			if frappe.db.exists(
				"Wishlist Product", {"product_code": product.product_code, "parent": frappe.session.user}
			):
				product.wished = True

		return result, discount_list

	def get_price_discount_info(self, product, price_object, discount_list):
		"""Modify product object and add price details."""
		fields = ["formatted_mrp", "formatted_price", "price_list_rate"]
		for field in fields:
			product[field] = price_object.get(field)

		if price_object.get("discount_percent"):
			product.discount_percent = flt(price_object.discount_percent)
			discount_list.append(price_object.discount_percent)

		if product.formatted_mrp:
			product.discount = price_object.get("formatted_discount_percent") or price_object.get(
				"formatted_discount_rate"
			)

	def get_stock_availability(self, product):
		"""Modify product object and add stock details."""
		product.in_stock = False
		warehouse = product.get("website_warehouse")
		is_stock_product = frappe.get_cached_value("Product", product.product_code, "is_stock_product")

		if product.get("on_backorder"):
			return

		if not is_stock_product:
			if warehouse:
				# product bundle case
				product.in_stock = get_non_stock_product_status(product.product_code, "website_warehouse")
			else:
				product.in_stock = True
		elif warehouse:
			# stock product and has warehouse
			actual_qty = frappe.db.get_value(
				"Bin", {"product_code": product.product_code, "warehouse": product.get("website_warehouse")}, "actual_qty"
			)
			product.in_stock = bool(flt(actual_qty))

	def get_cart_products(self):
		customer = get_customer(silent=True)
		if customer:
			quotation = frappe.get_all(
				"Quotation",
				fields=["name"],
				filters={
					"party_name": customer,
					"contact_email": frappe.session.user,
					"order_type": "Shopping Cart",
					"docstatus": 0,
				},
				order_by="modified desc",
				limit_page_length=1,
			)
			if quotation:
				products = frappe.get_all(
					"Quotation Product", fields=["product_code"], filters={"parent": quotation[0].get("name")}
				)
				products = [row.product_code for row in products]
				return products

		return []

	def filter_results_by_discount(self, fields, result):
		if fields and fields.get("discount"):
			discount_percent = frappe.utils.flt(fields["discount"][0])
			result = [
				row
				for row in result
				if row.get("discount_percent") and row.discount_percent <= discount_percent
			]

		if self.filter_with_discount:
			# no limit was added to results while querying
			# slice results manually
			result[: self.page_length]

		return result
