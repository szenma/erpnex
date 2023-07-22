import frappe
from frappe.search.full_text_search import FullTextSearch
from frappe.utils import strip_html_tags
from whoosh.analysis import StemmingAnalyzer
from whoosh.fields import ID, KEYWORD, TEXT, Schema
from whoosh.qparser import FieldsPlugin, MultifieldParser, WildcardPlugin
from whoosh.query import Prefix

# TODO: Make obsolete
INDEX_NAME = "products"


class ProductSearch(FullTextSearch):
	"""Wrapper for WebsiteSearch"""

	def get_schema(self):
		return Schema(
			title=TEXT(stored=True, field_boost=1.5),
			name=ID(stored=True),
			path=ID(stored=True),
			content=TEXT(stored=True, analyzer=StemmingAnalyzer()),
			keywords=KEYWORD(stored=True, scorable=True, commas=True),
		)

	def get_id(self):
		return "name"

	def get_products_to_index(self):
		"""Get all routes to be indexed, this includes the static pages
		in www/ and routes from published documents

		Returns:
		        self (object): FullTextSearch Instance
		"""
		products = get_all_published_products()
		documents = [self.get_document_to_index(product) for product in products]
		return documents

	def get_document_to_index(self, product):
		try:
			product = frappe.get_doc("Product", product)
			title = product.product_name
			keywords = [product.product_group]

			if product.brand:
				keywords.append(product.brand)

			if product.website_image_alt:
				keywords.append(product.website_image_alt)

			if product.has_variants and product.variant_based_on == "Product Attribute":
				keywords = keywords + [attr.attribute for attr in product.attributes]

			if product.web_long_description:
				content = strip_html_tags(product.web_long_description)
			elif product.description:
				content = strip_html_tags(product.description)

			return frappe._dict(
				title=title,
				name=product.name,
				path=product.route,
				content=content,
				keywords=", ".join(keywords),
			)
		except Exception:
			pass

	def search(self, text, scope=None, limit=20):
		"""Search from the current index

		Args:
		        text (str): String to search for
		        scope (str, optional): Scope to limit the search. Defaults to None.
		        limit (int, optional): Limit number of search results. Defaults to 20.

		Returns:
		        [List(_dict)]: Search results
		"""
		ix = self.get_index()

		results = None
		out = []

		with ix.searcher() as searcher:
			parser = MultifieldParser(["title", "content", "keywords"], ix.schema)
			parser.remove_plugin_class(FieldsPlugin)
			parser.remove_plugin_class(WildcardPlugin)
			query = parser.parse(text)

			filter_scoped = None
			if scope:
				filter_scoped = Prefix(self.id, scope)
			results = searcher.search(query, limit=limit, filter=filter_scoped)

			for r in results:
				out.append(self.parse_result(r))

		return out

	def parse_result(self, result):
		title_highlights = result.highlights("title")
		content_highlights = result.highlights("content")
		keyword_highlights = result.highlights("keywords")

		return frappe._dict(
			title=result["title"],
			path=result["path"],
			keywords=result["keywords"],
			title_highlights=title_highlights,
			content_highlights=content_highlights,
			keyword_highlights=keyword_highlights,
		)


def get_all_published_products():
	return frappe.get_all(
		"Website Product", filters={"variant_of": "", "published": 1}, pluck="product_code"
	)


def update_index_for_path(path):
	search = ProductSearch(INDEX_NAME)
	return search.update_index_by_name(path)


def remove_document_from_index(path):
	search = ProductSearch(INDEX_NAME)
	return search.remove_document_from_index(path)


def build_index_for_all_routes():
	search = ProductSearch(INDEX_NAME)
	return search.build()
