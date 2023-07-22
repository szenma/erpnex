# -*- coding: utf-8 -*-
# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
from typing import TYPE_CHECKING, List, Union

if TYPE_CHECKING:
	from erpnext.stock.doctype.product.product import Product

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, random_string
from frappe.website.doctype.website_slideshow.website_slideshow import get_slideshow
from frappe.website.website_generator import WebsiteGenerator

from erpnext.e_commerce.doctype.product_review.product_review import get_product_reviews
from erpnext.e_commerce.redisearch_utils import (
	delete_product_from_index,
	insert_product_to_index,
	update_index_for_product,
)
from erpnext.e_commerce.shopping_cart.cart import _set_price_list
from erpnext.setup.doctype.product_group.product_group import (
	get_parent_product_groups,
	invalidate_cache_for,
)
from erpnext.utilities.product import get_price


class WebsiteProduct(WebsiteGenerator):
	website = frappe._dict(
		page_title_field="web_product_name",
		condition_field="published",
		template="templates/generators/product/product.html",
		no_cache=1,
	)

	def autoname(self):
		# use naming series to accomodate products with same name (different product code)
		from frappe.model.naming import get_default_naming_series, make_autoname

		naming_series = get_default_naming_series("Website Product")
		if not self.name and naming_series:
			self.name = make_autoname(naming_series, doc=self)

	def onload(self):
		super(WebsiteProduct, self).onload()

	def validate(self):
		super(WebsiteProduct, self).validate()

		if not self.product_code:
			frappe.throw(_("Product Code is required"), title=_("Mandatory"))

		self.validate_duplicate_website_product()
		self.validate_website_image()
		self.make_thumbnail()
		self.publish_unpublish_desk_product(publish=True)

		if not self.get("__islocal"):
			wig = frappe.qb.DocType("Website Product Group")
			query = (
				frappe.qb.from_(wig)
				.select(wig.product_group)
				.where(
					(wig.parentfield == "website_product_groups")
					& (wig.parenttype == "Website Product")
					& (wig.parent == self.name)
				)
			)
			result = query.run(as_list=True)

			self.old_website_product_groups = [x[0] for x in result]

	def on_update(self):
		invalidate_cache_for_web_product(self)
		self.update_template_product()

	def on_trash(self):
		super(WebsiteProduct, self).on_trash()
		delete_product_from_index(self)
		self.publish_unpublish_desk_product(publish=False)

	def validate_duplicate_website_product(self):
		existing_web_product = frappe.db.exists("Website Product", {"product_code": self.product_code})
		if existing_web_product and existing_web_product != self.name:
			message = _("Website Product already exists against Product {0}").format(frappe.bold(self.product_code))
			frappe.throw(message, title=_("Already Published"))

	def publish_unpublish_desk_product(self, publish=True):
		if frappe.db.get_value("Product", self.product_code, "published_in_website") and publish:
			return  # if already published don't publish again
		frappe.db.set_value("Product", self.product_code, "published_in_website", publish)

	def make_route(self):
		"""Called from set_route in WebsiteGenerator."""
		if not self.route:
			return (
				cstr(frappe.db.get_value("Product Group", self.product_group, "route"))
				+ "/"
				+ self.scrub((self.product_name if self.product_name else self.product_code) + "-" + random_string(5))
			)

	def update_template_product(self):
		"""Publish Template Product if Variant is published."""
		if self.variant_of:
			if self.published:
				# show template
				template_product = frappe.get_doc("Product", self.variant_of)

				if not template_product.published_in_website:
					template_product.flags.ignore_permissions = True
					make_website_product(template_product)

	def validate_website_image(self):
		if frappe.flags.in_import:
			return

		"""Validate if the website image is a public file"""
		if not self.website_image:
			return

		# find if website image url exists as public
		file_doc = frappe.get_all(
			"File",
			filters={"file_url": self.website_image},
			fields=["name", "is_private"],
			order_by="is_private asc",
			limit_page_length=1,
		)

		if file_doc:
			file_doc = file_doc[0]

		if not file_doc:
			frappe.msgprint(
				_("Website Image {0} attached to Product {1} cannot be found").format(
					self.website_image, self.name
				)
			)

			self.website_image = None

		elif file_doc.is_private:
			frappe.msgprint(_("Website Image should be a public file or website URL"))

			self.website_image = None

	def make_thumbnail(self):
		"""Make a thumbnail of `website_image`"""
		if frappe.flags.in_import or frappe.flags.in_migrate:
			return

		import requests.exceptions

		db_website_image = frappe.db.get_value(self.doctype, self.name, "website_image")
		if not self.is_new() and self.website_image != db_website_image:
			self.thumbnail = None

		if self.website_image and not self.thumbnail:
			file_doc = None

			try:
				file_doc = frappe.get_doc(
					"File",
					{
						"file_url": self.website_image,
						"attached_to_doctype": "Website Product",
						"attached_to_name": self.name,
					},
				)
			except frappe.DoesNotExistError:
				pass
				# cleanup
				frappe.local.message_log.pop()

			except requests.exceptions.HTTPError:
				frappe.msgprint(_("Warning: Invalid attachment {0}").format(self.website_image))
				self.website_image = None

			except requests.exceptions.SSLError:
				frappe.msgprint(
					_("Warning: Invalid SSL certificate on attachment {0}").format(self.website_image)
				)
				self.website_image = None

			# for CSV import
			if self.website_image and not file_doc:
				try:
					file_doc = frappe.get_doc(
						{
							"doctype": "File",
							"file_url": self.website_image,
							"attached_to_doctype": "Website Product",
							"attached_to_name": self.name,
						}
					).save()

				except IOError:
					self.website_image = None

			if file_doc:
				if not file_doc.thumbnail_url:
					file_doc.make_thumbnail()

				self.thumbnail = file_doc.thumbnail_url

	def get_context(self, context):
		context.show_search = True
		context.search_link = "/search"
		context.body_class = "product-page"

		context.parents = get_parent_product_groups(self.product_group, from_product=True)  # breadcumbs
		self.attributes = frappe.get_all(
			"Product Variant Attribute",
			fields=["attribute", "attribute_value"],
			filters={"parent": self.product_code},
		)

		if self.slideshow:
			context.update(get_slideshow(self))

		self.set_metatags(context)
		self.set_shopping_cart_data(context)

		settings = context.shopping_cart.cart_settings

		self.get_product_details_section(context)

		if settings.get("enable_reviews"):
			reviews_data = get_product_reviews(self.name)
			context.update(reviews_data)
			context.reviews = context.reviews[:4]

		context.wished = False
		if frappe.db.exists(
			"Wishlist Product", {"product_code": self.product_code, "parent": frappe.session.user}
		):
			context.wished = True

		context.user_is_customer = check_if_user_is_customer()

		context.recommended_products = None
		if settings and settings.enable_recommendations:
			context.recommended_products = self.get_recommended_products(settings)

		return context

	def set_selected_attributes(self, variants, context, attribute_values_available):
		for variant in variants:
			variant.attributes = frappe.get_all(
				"Product Variant Attribute",
				filters={"parent": variant.name},
				fields=["attribute", "attribute_value as value"],
			)

			# make an attribute-value map for easier access in templates
			variant.attribute_map = frappe._dict(
				{attr.attribute: attr.value for attr in variant.attributes}
			)

			for attr in variant.attributes:
				values = attribute_values_available.setdefault(attr.attribute, [])
				if attr.value not in values:
					values.append(attr.value)

				if variant.name == context.variant.name:
					context.selected_attributes[attr.attribute] = attr.value

	def set_attribute_values(self, attributes, context, attribute_values_available):
		for attr in attributes:
			values = context.attribute_values.setdefault(attr.attribute, [])

			if cint(frappe.db.get_value("Product Attribute", attr.attribute, "numeric_values")):
				for val in sorted(attribute_values_available.get(attr.attribute, []), key=flt):
					values.append(val)
			else:
				# get list of values defined (for sequence)
				for attr_value in frappe.db.get_all(
					"Product Attribute Value",
					fields=["attribute_value"],
					filters={"parent": attr.attribute},
					order_by="idx asc",
				):

					if attr_value.attribute_value in attribute_values_available.get(attr.attribute, []):
						values.append(attr_value.attribute_value)

	def set_metatags(self, context):
		context.metatags = frappe._dict({})

		safe_description = frappe.utils.to_markdown(self.description)

		context.metatags.url = frappe.utils.get_url() + "/" + context.route

		if context.website_image:
			if context.website_image.startswith("http"):
				url = context.website_image
			else:
				url = frappe.utils.get_url() + context.website_image
			context.metatags.image = url

		context.metatags.description = safe_description[:300]

		context.metatags.title = self.web_product_name or self.product_name or self.product_code

		context.metatags["og:type"] = "product"
		context.metatags["og:site_name"] = "ERPNext"

	def set_shopping_cart_data(self, context):
		from erpnext.e_commerce.shopping_cart.product_info import get_product_info_for_website

		context.shopping_cart = get_product_info_for_website(
			self.product_code, skip_quotation_creation=True
		)

	@frappe.whitelist()
	def copy_specification_from_product_group(self):
		self.set("website_specifications", [])
		if self.product_group:
			for label, desc in frappe.db.get_values(
				"Product Website Specification", {"parent": self.product_group}, ["label", "description"]
			):
				row = self.append("website_specifications")
				row.label = label
				row.description = desc

	def get_product_details_section(self, context):
		"""Get section with tabs or website specifications."""
		context.show_tabs = self.show_tabbed_section
		if self.show_tabbed_section and (self.tabs or self.website_specifications):
			context.tabs = self.get_tabs()
		else:
			context.website_specifications = self.website_specifications

	def get_tabs(self):
		tab_values = {}
		tab_values["tab_1_title"] = "Product Details"
		tab_values["tab_1_content"] = frappe.render_template(
			"templates/generators/product/product_specifications.html",
			{"website_specifications": self.website_specifications, "show_tabs": self.show_tabbed_section},
		)

		for row in self.tabs:
			tab_values[f"tab_{row.idx + 1}_title"] = _(row.label)
			tab_values[f"tab_{row.idx + 1}_content"] = row.content

		return tab_values

	def get_recommended_products(self, settings):
		ri = frappe.qb.DocType("Recommended Products")
		wi = frappe.qb.DocType("Website Product")

		query = (
			frappe.qb.from_(ri)
			.join(wi)
			.on(ri.product_code == wi.product_code)
			.select(ri.product_code, ri.route, ri.website_product_name, ri.website_product_thumbnail)
			.where((ri.parent == self.name) & (wi.published == 1))
			.orderby(ri.idx)
		)
		products = query.run(as_dict=True)

		if settings.show_price:
			is_guest = frappe.session.user == "Guest"
			# Show Price if logged in.
			# If not logged in and price is hidden for guest, skip price fetch.
			if is_guest and settings.hide_price_for_guest:
				return products

			selling_price_list = _set_price_list(settings, None)
			for product in products:
				product.price_info = get_price(
					product.product_code, selling_price_list, settings.default_customer_group, settings.company
				)

		return products


def invalidate_cache_for_web_product(doc):
	"""Invalidate Website Product Group cache and rebuild ProductVariantsCacheManager."""
	from erpnext.stock.doctype.product.product import invalidate_product_variants_cache_for_website

	invalidate_cache_for(doc, doc.product_group)

	website_product_groups = list(
		set(
			(doc.get("old_website_product_groups") or [])
			+ [d.product_group for d in doc.get({"doctype": "Website Product Group"}) if d.product_group]
		)
	)

	for product_group in website_product_groups:
		invalidate_cache_for(doc, product_group)

	# Update Search Cache
	update_index_for_product(doc)

	invalidate_product_variants_cache_for_website(doc)


def on_doctype_update():
	# since route is a Text column, it needs a length for indexing
	frappe.db.add_index("Website Product", ["route(500)"])


def check_if_user_is_customer(user=None):
	from frappe.contacts.doctype.contact.contact import get_contact_name

	if not user:
		user = frappe.session.user

	contact_name = get_contact_name(user)
	customer = None

	if contact_name:
		contact = frappe.get_doc("Contact", contact_name)
		for link in contact.links:
			if link.link_doctype == "Customer":
				customer = link.link_name
				break

	return True if customer else False


@frappe.whitelist()
def make_website_product(doc: "Product", save: bool = True) -> Union["WebsiteProduct", List[str]]:
	"Make Website Product from Product. Used via Form UI or patch."

	if not doc:
		return

	if isinstance(doc, str):
		doc = json.loads(doc)

	if frappe.db.exists("Website Product", {"product_code": doc.get("product_code")}):
		message = _("Website Product already exists against {0}").format(frappe.bold(doc.get("product_code")))
		frappe.throw(message, title=_("Already Published"))

	website_product = frappe.new_doc("Website Product")
	website_product.web_product_name = doc.get("product_name")

	fields_to_map = [
		"product_code",
		"product_name",
		"product_group",
		"stock_uom",
		"brand",
		"has_variants",
		"variant_of",
		"description",
	]
	for field in fields_to_map:
		website_product.update({field: doc.get(field)})

	# Needed for publishing/mapping via Form UI only
	if not frappe.flags.in_migrate and (doc.get("image") and not website_product.website_image):
		website_product.website_image = doc.get("image")

	if not save:
		return website_product

	website_product.save()

	# Add to search cache
	insert_product_to_index(website_product)

	return [website_product.name, website_product.web_product_name]
