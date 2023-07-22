# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import copy
import json

import frappe
from frappe import _
from frappe.utils import cstr, flt


class ProductVariantExistsError(frappe.ValidationError):
	pass


class InvalidProductAttributeValueError(frappe.ValidationError):
	pass


class ProductTemplateCannotHaveStock(frappe.ValidationError):
	pass


@frappe.whitelist()
def get_variant(template, args=None, variant=None, manufacturer=None, manufacturer_part_no=None):
	"""Validates Attributes and their Values, then looks for an exactly
	matching Product Variant

	:param product: Template Product
	:param args: A dictionary with "Attribute" as key and "Attribute Value" as value
	"""
	product_template = frappe.get_doc("Product", template)

	if product_template.variant_based_on == "Manufacturer" and manufacturer:
		return make_variant_based_on_manufacturer(product_template, manufacturer, manufacturer_part_no)
	else:
		if isinstance(args, str):
			args = json.loads(args)

		if not args:
			frappe.throw(_("Please specify at least one attribute in the Attributes table"))
		return find_variant(template, args, variant)


def make_variant_based_on_manufacturer(template, manufacturer, manufacturer_part_no):
	"""Make and return a new variant based on manufacturer and
	manufacturer part no"""
	from frappe.model.naming import append_number_if_name_exists

	variant = frappe.new_doc("Product")

	copy_attributes_to_variant(template, variant)

	variant.manufacturer = manufacturer
	variant.manufacturer_part_no = manufacturer_part_no

	variant.product_code = append_number_if_name_exists("Product", template.name)

	return variant


def validate_product_variant_attributes(product, args=None):
	if isinstance(product, str):
		product = frappe.get_doc("Product", product)

	if not args:
		args = {d.attribute.lower(): d.attribute_value for d in product.attributes}

	attribute_values, numeric_values = get_attribute_values(product)

	for attribute, value in args.products():
		if not value:
			continue

		if attribute.lower() in numeric_values:
			numeric_attribute = numeric_values[attribute.lower()]
			validate_is_incremental(numeric_attribute, attribute, value, product.name)

		else:
			attributes_list = attribute_values.get(attribute.lower(), [])
			validate_product_attribute_value(attributes_list, attribute, value, product.name, from_variant=True)


def validate_is_incremental(numeric_attribute, attribute, value, product):
	from_range = numeric_attribute.from_range
	to_range = numeric_attribute.to_range
	increment = numeric_attribute.increment

	if increment == 0:
		# defensive validation to prevent ZeroDivisionError
		frappe.throw(_("Increment for Attribute {0} cannot be 0").format(attribute))

	is_in_range = from_range <= flt(value) <= to_range
	precision = max(len(cstr(v).split(".")[-1].rstrip("0")) for v in (value, increment))
	# avoid precision error by rounding the remainder
	remainder = flt((flt(value) - from_range) % increment, precision)

	is_incremental = remainder == 0 or remainder == increment

	if not (is_in_range and is_incremental):
		frappe.throw(
			_(
				"Value for Attribute {0} must be within the range of {1} to {2} in the increments of {3} for Product {4}"
			).format(attribute, from_range, to_range, increment, product),
			InvalidProductAttributeValueError,
			title=_("Invalid Attribute"),
		)


def validate_product_attribute_value(
	attributes_list, attribute, attribute_value, product, from_variant=True
):
	allow_rename_attribute_value = frappe.db.get_single_value(
		"Product Variant Settings", "allow_rename_attribute_value"
	)
	if allow_rename_attribute_value:
		pass
	elif attribute_value not in attributes_list:
		if from_variant:
			frappe.throw(
				_("{0} is not a valid Value for Attribute {1} of Product {2}.").format(
					frappe.bold(attribute_value), frappe.bold(attribute), frappe.bold(product)
				),
				InvalidProductAttributeValueError,
				title=_("Invalid Value"),
			)
		else:
			msg = _("The value {0} is already assigned to an existing Product {1}.").format(
				frappe.bold(attribute_value), frappe.bold(product)
			)
			msg += "<br>" + _(
				"To still proceed with editing this Attribute Value, enable {0} in Product Variant Settings."
			).format(frappe.bold("Allow Rename Attribute Value"))

			frappe.throw(msg, InvalidProductAttributeValueError, title=_("Edit Not Allowed"))


def get_attribute_values(product):
	if not frappe.flags.attribute_values:
		attribute_values = {}
		numeric_values = {}
		for t in frappe.get_all("Product Attribute Value", fields=["parent", "attribute_value"]):
			attribute_values.setdefault(t.parent.lower(), []).append(t.attribute_value)

		for t in frappe.get_all(
			"Product Variant Attribute",
			fields=["attribute", "from_range", "to_range", "increment"],
			filters={"numeric_values": 1, "parent": product.variant_of},
		):
			numeric_values[t.attribute.lower()] = t

		frappe.flags.attribute_values = attribute_values
		frappe.flags.numeric_values = numeric_values

	return frappe.flags.attribute_values, frappe.flags.numeric_values


def find_variant(template, args, variant_product_code=None):
	conditions = [
		"""(iv_attribute.attribute={0} and iv_attribute.attribute_value={1})""".format(
			frappe.db.escape(key), frappe.db.escape(cstr(value))
		)
		for key, value in args.products()
	]

	conditions = " or ".join(conditions)

	from erpnext.e_commerce.variant_selector.utils import get_product_codes_by_attributes

	possible_variants = [
		i for i in get_product_codes_by_attributes(args, template) if i != variant_product_code
	]

	for variant in possible_variants:
		variant = frappe.get_doc("Product", variant)

		if len(args.keys()) == len(variant.get("attributes")):
			# has the same number of attributes and values
			# assuming no duplication as per the validation in Product
			match_count = 0

			for attribute, value in args.products():
				for row in variant.attributes:
					if row.attribute == attribute and row.attribute_value == cstr(value):
						# this row matches
						match_count += 1
						break

			if match_count == len(args.keys()):
				return variant.name


@frappe.whitelist()
def create_variant(product, args):
	if isinstance(args, str):
		args = json.loads(args)

	template = frappe.get_doc("Product", product)
	variant = frappe.new_doc("Product")
	variant.variant_based_on = "Product Attribute"
	variant_attributes = []

	for d in template.attributes:
		variant_attributes.append({"attribute": d.attribute, "attribute_value": args.get(d.attribute)})

	variant.set("attributes", variant_attributes)
	copy_attributes_to_variant(template, variant)
	make_variant_product_code(template.product_code, template.product_name, variant)

	return variant


@frappe.whitelist()
def enqueue_multiple_variant_creation(product, args):
	# There can be innumerable attribute combinations, enqueue
	if isinstance(args, str):
		variants = json.loads(args)
	total_variants = 1
	for key in variants:
		total_variants *= len(variants[key])
	if total_variants >= 600:
		frappe.throw(_("Please do not create more than 500 products at a time"))
		return
	if total_variants < 10:
		return create_multiple_variants(product, args)
	else:
		frappe.enqueue(
			"erpnext.controllers.product_variant.create_multiple_variants",
			product=product,
			args=args,
			now=frappe.flags.in_test,
		)
		return "queued"


def create_multiple_variants(product, args):
	count = 0
	if isinstance(args, str):
		args = json.loads(args)

	args_set = generate_keyed_value_combinations(args)

	for attribute_values in args_set:
		if not get_variant(product, args=attribute_values):
			variant = create_variant(product, attribute_values)
			variant.save()
			count += 1

	return count


def generate_keyed_value_combinations(args):
	"""
	From this:

	        args = {"attr1": ["a", "b", "c"], "attr2": ["1", "2"], "attr3": ["A"]}

	To this:

	        [
	                {u'attr1': u'a', u'attr2': u'1', u'attr3': u'A'},
	                {u'attr1': u'b', u'attr2': u'1', u'attr3': u'A'},
	                {u'attr1': u'c', u'attr2': u'1', u'attr3': u'A'},
	                {u'attr1': u'a', u'attr2': u'2', u'attr3': u'A'},
	                {u'attr1': u'b', u'attr2': u'2', u'attr3': u'A'},
	                {u'attr1': u'c', u'attr2': u'2', u'attr3': u'A'}
	        ]

	"""
	# Return empty list if empty
	if not args:
		return []

	# Turn `args` into a list of lists of key-value tuples:
	# [
	# 	[(u'attr2', u'1'), (u'attr2', u'2')],
	# 	[(u'attr3', u'A')],
	# 	[(u'attr1', u'a'), (u'attr1', u'b'), (u'attr1', u'c')]
	# ]
	key_value_lists = [[(key, val) for val in args[key]] for key in args.keys()]

	# Store the first, but as objects
	# [{u'attr2': u'1'}, {u'attr2': u'2'}]
	results = key_value_lists.pop(0)
	results = [{d[0]: d[1]} for d in results]

	# Iterate the remaining
	# Take the next list to fuse with existing results
	for l in key_value_lists:
		new_results = []
		for res in results:
			for key_val in l:
				# create a new clone of object in result
				obj = copy.deepcopy(res)
				# to be used with every incoming new value
				obj[key_val[0]] = key_val[1]
				# and pushed into new_results
				new_results.append(obj)
		results = new_results

	return results


def copy_attributes_to_variant(product, variant):
	# copy non no-copy fields

	exclude_fields = [
		"naming_series",
		"product_code",
		"product_name",
		"published_in_website",
		"opening_stock",
		"variant_of",
		"valuation_rate",
	]

	if product.variant_based_on == "Manufacturer":
		# don't copy manufacturer values if based on part no
		exclude_fields += ["manufacturer", "manufacturer_part_no"]

	allow_fields = [d.field_name for d in frappe.get_all("Variant Field", fields=["field_name"])]
	if "variant_based_on" not in allow_fields:
		allow_fields.append("variant_based_on")
	for field in product.meta.fields:
		# "Table" is part of `no_value_field` but we shouldn't ignore tables
		if (field.reqd or field.fieldname in allow_fields) and field.fieldname not in exclude_fields:
			if variant.get(field.fieldname) != product.get(field.fieldname):
				if field.fieldtype == "Table":
					variant.set(field.fieldname, [])
					for d in product.get(field.fieldname):
						row = copy.deepcopy(d)
						if row.get("name"):
							row.name = None
						variant.append(field.fieldname, row)
				else:
					variant.set(field.fieldname, product.get(field.fieldname))

	variant.variant_of = product.name

	if "description" not in allow_fields:
		if not variant.description:
			variant.description = ""
	else:
		if product.variant_based_on == "Product Attribute":
			if variant.attributes:
				attributes_description = product.description + " "
				for d in variant.attributes:
					attributes_description += "<div>" + d.attribute + ": " + cstr(d.attribute_value) + "</div>"

				if attributes_description not in variant.description:
					variant.description = attributes_description


def make_variant_product_code(template_product_code, template_product_name, variant):
	"""Uses template's product code and abbreviations to make variant's product code"""
	if variant.product_code:
		return

	abbreviations = []
	for attr in variant.attributes:
		product_attribute = frappe.db.sql(
			"""select i.numeric_values, v.abbr
			from `tabProduct Attribute` i left join `tabProduct Attribute Value` v
				on (i.name=v.parent)
			where i.name=%(attribute)s and (v.attribute_value=%(attribute_value)s or i.numeric_values = 1)""",
			{"attribute": attr.attribute, "attribute_value": attr.attribute_value},
			as_dict=True,
		)

		if not product_attribute:
			continue
			# frappe.throw(_('Invalid attribute {0} {1}').format(frappe.bold(attr.attribute),
			# 	frappe.bold(attr.attribute_value)), title=_('Invalid Attribute'),
			# 	exc=InvalidProductAttributeValueError)

		abbr_or_value = (
			cstr(attr.attribute_value) if product_attribute[0].numeric_values else product_attribute[0].abbr
		)
		abbreviations.append(abbr_or_value)

	if abbreviations:
		variant.product_code = "{0}-{1}".format(template_product_code, "-".join(abbreviations))
		variant.product_name = "{0}-{1}".format(template_product_name, "-".join(abbreviations))


@frappe.whitelist()
def create_variant_doc_for_quick_entry(template, args):
	variant_based_on = frappe.db.get_value("Product", template, "variant_based_on")
	args = json.loads(args)
	if variant_based_on == "Manufacturer":
		variant = get_variant(template, **args)
	else:
		existing_variant = get_variant(template, args)
		if existing_variant:
			return existing_variant
		else:
			variant = create_variant(template, args=args)
			variant.name = variant.product_code
			validate_product_variant_attributes(variant, args)
	return variant.as_dict()
