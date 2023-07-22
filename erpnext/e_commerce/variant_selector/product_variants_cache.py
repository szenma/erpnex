import frappe


class ProductVariantsCacheManager:
	def __init__(self, product_code):
		self.product_code = product_code

	def get_product_variants_data(self):
		val = frappe.cache().hget("product_variants_data", self.product_code)

		if not val:
			self.build_cache()

		return frappe.cache().hget("product_variants_data", self.product_code)

	def get_attribute_value_product_map(self):
		val = frappe.cache().hget("attribute_value_product_map", self.product_code)

		if not val:
			self.build_cache()

		return frappe.cache().hget("attribute_value_product_map", self.product_code)

	def get_product_attribute_value_map(self):
		val = frappe.cache().hget("product_attribute_value_map", self.product_code)

		if not val:
			self.build_cache()

		return frappe.cache().hget("product_attribute_value_map", self.product_code)

	def get_optional_attributes(self):
		val = frappe.cache().hget("optional_attributes", self.product_code)

		if not val:
			self.build_cache()

		return frappe.cache().hget("optional_attributes", self.product_code)

	def get_ordered_attribute_values(self):
		val = frappe.cache().get_value("ordered_attribute_values_map")
		if val:
			return val

		all_attribute_values = frappe.get_all(
			"Product Attribute Value", ["attribute_value", "idx", "parent"], order_by="idx asc"
		)

		ordered_attribute_values_map = frappe._dict({})
		for d in all_attribute_values:
			ordered_attribute_values_map.setdefault(d.parent, []).append(d.attribute_value)

		frappe.cache().set_value("ordered_attribute_values_map", ordered_attribute_values_map)
		return ordered_attribute_values_map

	def build_cache(self):
		parent_product_code = self.product_code

		attributes = [
			a.attribute
			for a in frappe.get_all(
				"Product Variant Attribute", {"parent": parent_product_code}, ["attribute"], order_by="idx asc"
			)
		]

		# Get Variants and tehir Attributes that are not disabled
		iva = frappe.qb.DocType("Product Variant Attribute")
		product = frappe.qb.DocType("Product")
		query = (
			frappe.qb.from_(iva)
			.join(product)
			.on(product.name == iva.parent)
			.select(iva.parent, iva.attribute, iva.attribute_value)
			.where((iva.variant_of == parent_product_code) & (product.disabled == 0))
			.orderby(iva.name)
		)
		product_variants_data = query.run()

		attribute_value_product_map = frappe._dict()
		product_attribute_value_map = frappe._dict()

		for row in product_variants_data:
			product_code, attribute, attribute_value = row
			# (attr, value) => [product1, product2]
			attribute_value_product_map.setdefault((attribute, attribute_value), []).append(product_code)
			# product => {attr1: value1, attr2: value2}
			product_attribute_value_map.setdefault(product_code, {})[attribute] = attribute_value

		optional_attributes = set()
		for product_code, attr_dict in product_attribute_value_map.products():
			for attribute in attributes:
				if attribute not in attr_dict:
					optional_attributes.add(attribute)

		frappe.cache().hset("attribute_value_product_map", parent_product_code, attribute_value_product_map)
		frappe.cache().hset("product_attribute_value_map", parent_product_code, product_attribute_value_map)
		frappe.cache().hset("product_variants_data", parent_product_code, product_variants_data)
		frappe.cache().hset("optional_attributes", parent_product_code, optional_attributes)

	def clear_cache(self):
		keys = [
			"attribute_value_product_map",
			"product_attribute_value_map",
			"product_variants_data",
			"optional_attributes",
		]

		for key in keys:
			frappe.cache().hdel(key, self.product_code)

	def rebuild_cache(self):
		self.clear_cache()
		enqueue_build_cache(self.product_code)


def build_cache(product_code):
	frappe.cache().hset("product_cache_build_in_progress", product_code, 1)
	i = ProductVariantsCacheManager(product_code)
	i.build_cache()
	frappe.cache().hset("product_cache_build_in_progress", product_code, 0)


def enqueue_build_cache(product_code):
	if frappe.cache().hget("product_cache_build_in_progress", product_code):
		return
	frappe.enqueue(
		"erpnext.e_commerce.variant_selector.product_variants_cache.build_cache",
		product_code=product_code,
		queue="long",
	)
