# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import json

import frappe
from frappe import _
from frappe.utils.redis_wrapper import RedisWrapper
from redis import ResponseError
from redisearch import AutoCompleter, Client, IndexDefinition, Suggestion, TagField, TextField

WEBSITE_PRODUCT_INDEX = "website_products_index"
WEBSITE_PRODUCT_KEY_PREFIX = "website_product:"
WEBSITE_PRODUCT_NAME_AUTOCOMPLETE = "website_products_name_dict"
WEBSITE_PRODUCT_CATEGORY_AUTOCOMPLETE = "website_products_category_dict"


def get_indexable_web_fields():
	"Return valid fields from Website Product that can be searched for."
	web_product_meta = frappe.get_meta("Website Product", cached=True)
	valid_fields = filter(
		lambda df: df.fieldtype in ("Link", "Table MultiSelect", "Data", "Small Text", "Text Editor"),
		web_product_meta.fields,
	)

	return [df.fieldname for df in valid_fields]


def is_redisearch_enabled():
	"Return True only if redisearch is loaded and enabled."
	is_redisearch_enabled = frappe.db.get_single_value("E Commerce Settings", "is_redisearch_enabled")
	return is_search_module_loaded() and is_redisearch_enabled


def is_search_module_loaded():
	try:
		cache = frappe.cache()
		out = cache.execute_command("MODULE LIST")

		parsed_output = " ".join(
			(" ".join([frappe.as_unicode(s) for s in o if not isinstance(s, int)]) for o in out)
		)
		return "search" in parsed_output
	except Exception:
		return False  # handling older redis versions


def if_redisearch_enabled(function):
	"Decorator to check if Redisearch is enabled."

	def wrapper(*args, **kwargs):
		if is_redisearch_enabled():
			func = function(*args, **kwargs)
			return func
		return

	return wrapper


def make_key(key):
	return "{0}|{1}".format(frappe.conf.db_name, key).encode("utf-8")


@if_redisearch_enabled
def create_website_products_index():
	"Creates Index Definition."

	# CREATE index
	client = Client(make_key(WEBSITE_PRODUCT_INDEX), conn=frappe.cache())

	try:
		client.drop_index()  # drop if already exists
	except ResponseError:
		# will most likely raise a ResponseError if index does not exist
		# ignore and create index
		pass
	except Exception:
		raise_redisearch_error()

	idx_def = IndexDefinition([make_key(WEBSITE_PRODUCT_KEY_PREFIX)])

	# Index fields mentioned in e-commerce settings
	idx_fields = frappe.db.get_single_value("E Commerce Settings", "search_index_fields")
	idx_fields = idx_fields.split(",") if idx_fields else []

	if "web_product_name" in idx_fields:
		idx_fields.remove("web_product_name")

	idx_fields = list(map(to_search_field, idx_fields))

	client.create_index(
		[TextField("web_product_name", sortable=True)] + idx_fields,
		definition=idx_def,
	)

	reindex_all_web_products()
	define_autocomplete_dictionary()


def to_search_field(field):
	if field == "tags":
		return TagField("tags", separator=",")

	return TextField(field)


@if_redisearch_enabled
def insert_product_to_index(website_product_doc):
	# Insert product to index
	key = get_cache_key(website_product_doc.name)
	cache = frappe.cache()
	web_product = create_web_product_map(website_product_doc)

	for field, value in web_product.products():
		super(RedisWrapper, cache).hset(make_key(key), field, value)

	insert_to_name_ac(website_product_doc.web_product_name, website_product_doc.name)


@if_redisearch_enabled
def insert_to_name_ac(web_name, doc_name):
	ac = AutoCompleter(make_key(WEBSITE_PRODUCT_NAME_AUTOCOMPLETE), conn=frappe.cache())
	ac.add_suggestions(Suggestion(web_name, payload=doc_name))


def create_web_product_map(website_product_doc):
	fields_to_index = get_fields_indexed()
	web_product = {}

	for field in fields_to_index:
		web_product[field] = website_product_doc.get(field) or ""

	return web_product


@if_redisearch_enabled
def update_index_for_product(website_product_doc):
	# Reinsert to Cache
	insert_product_to_index(website_product_doc)
	define_autocomplete_dictionary()


@if_redisearch_enabled
def delete_product_from_index(website_product_doc):
	cache = frappe.cache()
	key = get_cache_key(website_product_doc.name)

	try:
		cache.delete(key)
	except Exception:
		raise_redisearch_error()

	delete_from_ac_dict(website_product_doc)
	return True


@if_redisearch_enabled
def delete_from_ac_dict(website_product_doc):
	"""Removes this products's name from autocomplete dictionary"""
	cache = frappe.cache()
	name_ac = AutoCompleter(make_key(WEBSITE_PRODUCT_NAME_AUTOCOMPLETE), conn=cache)
	name_ac.delete(website_product_doc.web_product_name)


@if_redisearch_enabled
def define_autocomplete_dictionary():
	"""
	Defines/Redefines an autocomplete search dictionary for Website Product Name.
	Also creats autocomplete dictionary for Published Product Groups.
	"""

	cache = frappe.cache()
	product_ac = AutoCompleter(make_key(WEBSITE_PRODUCT_NAME_AUTOCOMPLETE), conn=cache)
	product_group_ac = AutoCompleter(make_key(WEBSITE_PRODUCT_CATEGORY_AUTOCOMPLETE), conn=cache)

	# Delete both autocomplete dicts
	try:
		cache.delete(make_key(WEBSITE_PRODUCT_NAME_AUTOCOMPLETE))
		cache.delete(make_key(WEBSITE_PRODUCT_CATEGORY_AUTOCOMPLETE))
	except Exception:
		raise_redisearch_error()

	create_products_autocomplete_dict(autocompleter=product_ac)
	create_product_groups_autocomplete_dict(autocompleter=product_group_ac)


@if_redisearch_enabled
def create_products_autocomplete_dict(autocompleter):
	"Add products as suggestions in Autocompleter."
	products = frappe.get_all(
		"Website Product", fields=["web_product_name", "product_group"], filters={"published": 1}
	)

	for product in products:
		autocompleter.add_suggestions(Suggestion(product.web_product_name))


@if_redisearch_enabled
def create_product_groups_autocomplete_dict(autocompleter):
	"Add product groups with weightage as suggestions in Autocompleter."
	published_product_groups = frappe.get_all(
		"Product Group", fields=["name", "route", "weightage"], filters={"show_in_website": 1}
	)
	if not published_product_groups:
		return

	for product_group in published_product_groups:
		payload = json.dumps({"name": product_group.name, "route": product_group.route})
		autocompleter.add_suggestions(
			Suggestion(
				string=product_group.name,
				score=frappe.utils.flt(product_group.weightage) or 1.0,
				payload=payload,  # additional info that can be retrieved later
			)
		)


@if_redisearch_enabled
def reindex_all_web_products():
	products = frappe.get_all("Website Product", fields=get_fields_indexed(), filters={"published": True})

	cache = frappe.cache()
	for product in products:
		web_product = create_web_product_map(product)
		key = make_key(get_cache_key(product.name))

		for field, value in web_product.products():
			super(RedisWrapper, cache).hset(key, field, value)


def get_cache_key(name):
	name = frappe.scrub(name)
	return f"{WEBSITE_PRODUCT_KEY_PREFIX}{name}"


def get_fields_indexed():
	fields_to_index = frappe.db.get_single_value("E Commerce Settings", "search_index_fields")
	fields_to_index = fields_to_index.split(",") if fields_to_index else []

	mandatory_fields = ["name", "web_product_name", "route", "thumbnail", "ranking"]
	fields_to_index = fields_to_index + mandatory_fields

	return fields_to_index


def raise_redisearch_error():
	"Create an Error Log and raise error."
	log = frappe.log_error("Redisearch Error")
	log_link = frappe.utils.get_link_to_form("Error Log", log.name)

	frappe.throw(
		msg=_("Something went wrong. Check {0}").format(log_link), title=_("Redisearch Error")
	)
