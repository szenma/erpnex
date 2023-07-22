# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import copy
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.nestedset import NestedSet
from frappe.website.utils import clear_cache
from frappe.website.website_generator import WebsiteGenerator

from erpnext.e_commerce.doctype.e_commerce_settings.e_commerce_settings import ECommerceSettings
from erpnext.e_commerce.product_data_engine.filters import ProductFiltersBuilder


class ProductGroup(NestedSet, WebsiteGenerator):
	nsm_parent_field = "parent_product_group"
	website = frappe._dict(
		condition_field="show_in_website",
		template="templates/generators/product_group.html",
		no_cache=1,
		no_breadcrumbs=1,
	)

	def autoname(self):
		self.name = self.product_group_name

	def validate(self):
		super(ProductGroup, self).validate()

		if not self.parent_product_group and not frappe.flags.in_test:
			if frappe.db.exists("Product Group", _("All Product Groups")):
				self.parent_product_group = _("All Product Groups")

		self.make_route()
		self.validate_product_group_defaults()
		self.check_product_tax()
		ECommerceSettings.validate_field_filters(self.filter_fields, enable_field_filters=True)

	def check_product_tax(self):
		"""Check whether Tax Rate is not entered twice for same Tax Type"""
		check_list = []
		for d in self.get("taxes"):
			if d.product_tax_template:
				if (d.product_tax_template, d.tax_category) in check_list:
					frappe.throw(
						_("{0} entered twice {1} in Product Taxes").format(
							frappe.bold(d.product_tax_template),
							"for tax category {0}".format(frappe.bold(d.tax_category)) if d.tax_category else "",
						)
					)
				else:
					check_list.append((d.product_tax_template, d.tax_category))

	def on_update(self):
		NestedSet.on_update(self)
		invalidate_cache_for(self)
		self.validate_one_root()
		self.delete_child_product_groups_key()

	def make_route(self):
		"""Make website route"""
		if not self.route:
			self.route = ""
			if self.parent_product_group:
				parent_product_group = frappe.get_doc("Product Group", self.parent_product_group)

				# make parent route only if not root
				if parent_product_group.parent_product_group and parent_product_group.route:
					self.route = parent_product_group.route + "/"

			self.route += self.scrub(self.product_group_name)

			return self.route

	def on_trash(self):
		NestedSet.on_trash(self)
		WebsiteGenerator.on_trash(self)
		self.delete_child_product_groups_key()

	def get_context(self, context):
		context.show_search = True
		context.body_class = "product-page"
		context.page_length = (
			cint(frappe.db.get_single_value("E Commerce Settings", "products_per_page")) or 6
		)
		context.search_link = "/product_search"

		filter_engine = ProductFiltersBuilder(self.name)

		context.field_filters = filter_engine.get_field_filters()
		context.attribute_filters = filter_engine.get_attribute_filters()

		context.update({"parents": get_parent_product_groups(self.parent_product_group), "title": self.name})

		if self.slideshow:
			values = {"show_indicators": 1, "show_controls": 0, "rounded": 1, "slider_name": self.slideshow}
			slideshow = frappe.get_doc("Website Slideshow", self.slideshow)
			slides = slideshow.get({"doctype": "Website Slideshow Product"})
			for index, slide in enumerate(slides):
				values[f"slide_{index + 1}_image"] = slide.image
				values[f"slide_{index + 1}_title"] = slide.heading
				values[f"slide_{index + 1}_subtitle"] = slide.description
				values[f"slide_{index + 1}_theme"] = slide.get("theme") or "Light"
				values[f"slide_{index + 1}_content_align"] = slide.get("content_align") or "Centre"
				values[f"slide_{index + 1}_primary_action"] = slide.url

			context.slideshow = values

		context.no_breadcrumbs = False
		context.title = self.website_title or self.name
		context.name = self.name
		context.product_group_name = self.product_group_name

		return context

	def delete_child_product_groups_key(self):
		frappe.cache().hdel("child_product_groups", self.name)

	def validate_product_group_defaults(self):
		from erpnext.stock.doctype.product.product import validate_product_default_company_links

		validate_product_default_company_links(self.product_group_defaults)


def get_child_groups_for_website(product_group_name, immediate=False, include_self=False):
	"""Returns child product groups *excluding* passed group."""
	product_group = frappe.get_cached_value("Product Group", product_group_name, ["lft", "rgt"], as_dict=1)
	filters = {"lft": [">", product_group.lft], "rgt": ["<", product_group.rgt], "show_in_website": 1}

	if immediate:
		filters["parent_product_group"] = product_group_name

	if include_self:
		filters.update({"lft": [">=", product_group.lft], "rgt": ["<=", product_group.rgt]})

	return frappe.get_all("Product Group", filters=filters, fields=["name", "route"], order_by="name")


def get_child_product_groups(product_group_name):
	product_group = frappe.get_cached_value("Product Group", product_group_name, ["lft", "rgt"], as_dict=1)

	child_product_groups = [
		d.name
		for d in frappe.get_all(
			"Product Group", filters={"lft": (">=", product_group.lft), "rgt": ("<=", product_group.rgt)}
		)
	]

	return child_product_groups or {}


def get_product_for_list_in_html(context):
	# add missing absolute link in files
	# user may forget it during upload
	if (context.get("website_image") or "").startswith("files/"):
		context["website_image"] = "/" + quote(context["website_image"])

	products_template = "templates/includes/products_as_list.html"

	return frappe.get_template(products_template).render(context)


def get_parent_product_groups(product_group_name, from_product=False):
	settings = frappe.get_cached_doc("E Commerce Settings")

	if settings.enable_field_filters:
		base_nav_page = {"name": _("Shop by Category"), "route": "/shop-by-category"}
	else:
		base_nav_page = {"name": _("All Products"), "route": "/all-products"}

	if from_product and frappe.request.environ.get("HTTP_REFERER"):
		# base page after 'Home' will vary on Product page
		last_page = frappe.request.environ["HTTP_REFERER"].split("/")[-1].split("?")[0]
		if last_page and last_page in ("shop-by-category", "all-products"):
			base_nav_page_title = " ".join(last_page.split("-")).title()
			base_nav_page = {"name": _(base_nav_page_title), "route": "/" + last_page}

	base_parents = [
		{"name": _("Home"), "route": "/"},
		base_nav_page,
	]

	if not product_group_name:
		return base_parents

	product_group = frappe.db.get_value("Product Group", product_group_name, ["lft", "rgt"], as_dict=1)
	parent_groups = frappe.db.sql(
		"""select name, route from `tabProduct Group`
		where lft <= %s and rgt >= %s
		and show_in_website=1
		order by lft asc""",
		(product_group.lft, product_group.rgt),
		as_dict=True,
	)

	return base_parents + parent_groups


def invalidate_cache_for(doc, product_group=None):
	if not product_group:
		product_group = doc.name

	for d in get_parent_product_groups(product_group):
		product_group_name = frappe.db.get_value("Product Group", d.get("name"))
		if product_group_name:
			clear_cache(frappe.db.get_value("Product Group", product_group_name, "route"))


def get_product_group_defaults(product, company):
	product = frappe.get_cached_doc("Product", product)
	product_group = frappe.get_cached_doc("Product Group", product.product_group)

	for d in product_group.product_group_defaults or []:
		if d.company == company:
			row = copy.deepcopy(d.as_dict())
			row.pop("name")
			return row

	return frappe._dict()
