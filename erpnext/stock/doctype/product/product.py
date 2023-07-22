# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import copy
import json
from typing import Dict, List, Optional

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import (
	cint,
	cstr,
	flt,
	formatdate,
	get_link_to_form,
	getdate,
	now_datetime,
	nowtime,
	strip,
	strip_html,
)
from frappe.utils.html_utils import clean_html

import erpnext
from erpnext.controllers.product_variant import (
	ProductVariantExistsError,
	copy_attributes_to_variant,
	get_variant,
	make_variant_product_code,
	validate_product_variant_attributes,
)
from erpnext.setup.doctype.product_group.product_group import invalidate_cache_for
from erpnext.stock.doctype.product_default.product_default import ProductDefault


class DuplicateReorderRows(frappe.ValidationError):
	pass


class StockExistsForTemplate(frappe.ValidationError):
	pass


class InvalidBarcode(frappe.ValidationError):
	pass


class DataValidationError(frappe.ValidationError):
	pass


class Product(Document):
	def onload(self):
		self.set_onload("stock_exists", self.stock_ledger_created())
		self.set_onload("asset_naming_series", get_asset_naming_series())

	def autoname(self):
		if frappe.db.get_default("product_naming_by") == "Naming Series":
			if self.variant_of:
				if not self.product_code:
					template_product_name = frappe.db.get_value("Product", self.variant_of, "product_name")
					make_variant_product_code(self.variant_of, template_product_name, self)
			else:
				from frappe.model.naming import set_name_by_naming_series

				set_name_by_naming_series(self)
				self.product_code = self.name

		self.product_code = strip(self.product_code)
		self.name = self.product_code

	def after_insert(self):
		"""set opening stock and product price"""
		if self.standard_rate:
			for default in self.product_defaults or [frappe._dict()]:
				self.add_price(default.default_price_list)

		if self.opening_stock:
			self.set_opening_stock()

	def validate(self):
		if not self.product_name:
			self.product_name = self.product_code

		if not strip_html(cstr(self.description)).strip():
			self.description = self.product_name

		self.validate_uom()
		self.validate_description()
		self.add_default_uom_in_conversion_factor_table()
		self.validate_conversion_factor()
		self.validate_product_type()
		self.validate_naming_series()
		self.check_for_active_boms()
		self.fill_customer_code()
		self.check_product_tax()
		self.validate_barcode()
		self.validate_warehouse_for_reorder()
		self.update_bom_product_desc()

		self.validate_has_variants()
		self.validate_attributes_in_variants()
		self.validate_stock_exists_for_template_product()
		self.validate_attributes()
		self.validate_variant_attributes()
		self.validate_variant_based_on_change()
		self.validate_fixed_asset()
		self.clear_retain_sample()
		self.validate_retain_sample()
		self.validate_uom_conversion_factor()
		self.validate_customer_provided_part()
		self.update_defaults_from_product_group()
		self.validate_product_defaults()
		self.validate_auto_reorder_enabled_in_stock_settings()
		self.cant_change()
		self.validate_product_tax_net_rate_range()
		set_product_tax_from_hsn_code(self)

		if not self.is_new():
			self.old_product_group = frappe.db.get_value(self.doctype, self.name, "product_group")

	def on_update(self):
		invalidate_cache_for_product(self)
		self.update_variants()
		self.update_product_price()
		self.update_website_product()

	def validate_description(self):
		"""Clean HTML description if set"""
		if cint(frappe.db.get_single_value("Stock Settings", "clean_description_html")):
			self.description = clean_html(self.description)

	def validate_customer_provided_part(self):
		if self.is_customer_provided_product:
			if self.is_purchase_product:
				frappe.throw(_('"Customer Provided Product" cannot be Purchase Product also'))
			if self.valuation_rate:
				frappe.throw(_('"Customer Provided Product" cannot have Valuation Rate'))
			self.default_material_request_type = "Customer Provided"

	def add_price(self, price_list=None):
		"""Add a new price"""
		if not price_list:
			price_list = frappe.db.get_single_value(
				"Selling Settings", "selling_price_list"
			) or frappe.db.get_value("Price List", _("Standard Selling"))
		if price_list:
			product_price = frappe.get_doc(
				{
					"doctype": "Product Price",
					"price_list": price_list,
					"product_code": self.name,
					"uom": self.stock_uom,
					"brand": self.brand,
					"currency": erpnext.get_default_currency(),
					"price_list_rate": self.standard_rate,
				}
			)
			product_price.insert()

	def set_opening_stock(self):
		"""set opening stock"""
		if not self.is_stock_product or self.has_serial_no or self.has_batch_no:
			return

		if not self.valuation_rate and not self.standard_rate and not self.is_customer_provided_product:
			frappe.throw(_("Valuation Rate is mandatory if Opening Stock entered"))

		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		# default warehouse, or Stores
		for default in self.product_defaults or [
			frappe._dict({"company": frappe.defaults.get_defaults().company})
		]:
			default_warehouse = default.default_warehouse or frappe.db.get_single_value(
				"Stock Settings", "default_warehouse"
			)
			if default_warehouse:
				warehouse_company = frappe.db.get_value("Warehouse", default_warehouse, "company")

			if not default_warehouse or warehouse_company != default.company:
				default_warehouse = frappe.db.get_value(
					"Warehouse", {"warehouse_name": _("Stores"), "company": default.company}
				)

			if default_warehouse:
				stock_entry = make_stock_entry(
					product_code=self.name,
					target=default_warehouse,
					qty=self.opening_stock,
					rate=self.valuation_rate or self.standard_rate,
					company=default.company,
					posting_date=getdate(),
					posting_time=nowtime(),
				)

				stock_entry.add_comment("Comment", _("Opening Stock"))

	def validate_fixed_asset(self):
		if self.is_fixed_asset:
			if self.is_stock_product:
				frappe.throw(_("Fixed Asset Product must be a non-stock product."))

			if not self.asset_category:
				frappe.throw(_("Asset Category is mandatory for Fixed Asset product"))

			if self.stock_ledger_created():
				frappe.throw(_("Cannot be a fixed asset product as Stock Ledger is created."))

		if not self.is_fixed_asset:
			asset = frappe.db.get_all("Asset", filters={"product_code": self.name, "docstatus": 1}, limit=1)
			if asset:
				frappe.throw(
					_('"Is Fixed Asset" cannot be unchecked, as Asset record exists against the product')
				)

	def validate_retain_sample(self):
		if self.retain_sample and not frappe.db.get_single_value(
			"Stock Settings", "sample_retention_warehouse"
		):
			frappe.throw(_("Please select Sample Retention Warehouse in Stock Settings first"))
		if self.retain_sample and not self.has_batch_no:
			frappe.throw(
				_(
					"{0} Retain Sample is based on batch, please check Has Batch No to retain sample of product"
				).format(self.product_code)
			)

	def clear_retain_sample(self):
		if not self.has_batch_no:
			self.retain_sample = False

		if not self.retain_sample:
			self.sample_quantity = 0

	def add_default_uom_in_conversion_factor_table(self):
		if not self.is_new() and self.has_value_changed("stock_uom"):
			self.uoms = []
			frappe.msgprint(
				_("Successfully changed Stock UOM, please redefine conversion factors for new UOM."),
				alert=True,
			)

		uoms_list = [d.uom for d in self.get("uoms")]

		if self.stock_uom not in uoms_list:
			self.append("uoms", {"uom": self.stock_uom, "conversion_factor": 1})

	def update_website_product(self):
		"""Update Website Product if change in Product impacts it."""
		web_product = frappe.db.exists("Website Product", {"product_code": self.product_code})

		if web_product:
			changed = {}
			editable_fields = ["product_name", "product_group", "stock_uom", "brand", "description", "disabled"]
			doc_before_save = self.get_doc_before_save()

			for field in editable_fields:
				if doc_before_save.get(field) != self.get(field):
					if field == "disabled":
						changed["published"] = not self.get(field)
					else:
						changed[field] = self.get(field)

			if not changed:
				return

			web_product_doc = frappe.get_doc("Website Product", web_product)
			web_product_doc.update(changed)
			web_product_doc.save()

	def validate_product_tax_net_rate_range(self):
		for tax in self.get("taxes"):
			if flt(tax.maximum_net_rate) < flt(tax.minimum_net_rate):
				frappe.throw(_("Row #{0}: Maximum Net Rate cannot be greater than Minimum Net Rate"))

	def update_template_tables(self):
		template = frappe.get_cached_doc("Product", self.variant_of)

		# add product taxes from template
		for d in template.get("taxes"):
			self.append("taxes", {"product_tax_template": d.product_tax_template})

		# copy re-order table if empty
		if not self.get("reorder_levels"):
			for d in template.get("reorder_levels"):
				n = {}
				for k in (
					"warehouse",
					"warehouse_reorder_level",
					"warehouse_reorder_qty",
					"material_request_type",
				):
					n[k] = d.get(k)
				self.append("reorder_levels", n)

	def validate_conversion_factor(self):
		check_list = []
		for d in self.get("uoms"):
			if cstr(d.uom) in check_list:
				frappe.throw(
					_("Unit of Measure {0} has been entered more than once in Conversion Factor Table").format(
						d.uom
					)
				)
			else:
				check_list.append(cstr(d.uom))

			if d.uom and cstr(d.uom) == cstr(self.stock_uom) and flt(d.conversion_factor) != 1:
				frappe.throw(
					_("Conversion factor for default Unit of Measure must be 1 in row {0}").format(d.idx)
				)

	def validate_product_type(self):
		if self.has_serial_no == 1 and self.is_stock_product == 0 and not self.is_fixed_asset:
			frappe.throw(_("'Has Serial No' can not be 'Yes' for non-stock product"))

		if self.has_serial_no == 0 and self.serial_no_series:
			self.serial_no_series = None

	def validate_naming_series(self):
		for field in ["serial_no_series", "batch_number_series"]:
			series = self.get(field)
			if series and "#" in series and "." not in series:
				frappe.throw(
					_("Invalid naming series (. missing) for {0}").format(
						frappe.bold(self.meta.get_field(field).label)
					)
				)

	def check_for_active_boms(self):
		if self.default_bom:
			bom_product = frappe.db.get_value("BOM", self.default_bom, "product")
			if bom_product not in (self.name, self.variant_of):
				frappe.throw(
					_("Default BOM ({0}) must be active for this product or its template").format(bom_product)
				)

	def fill_customer_code(self):
		"""
		Append all the customer codes and insert into "customer_code" field of product table.
		Used to search Product by customer code.
		"""
		customer_codes = set(d.ref_code for d in self.get("customer_products", []))
		self.customer_code = ",".join(customer_codes)

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

	def validate_barcode(self):
		from stdnum import ean

		if len(self.barcodes) > 0:
			for product_barcode in self.barcodes:
				options = frappe.get_meta("Product Barcode").get_options("barcode_type").split("\n")
				if product_barcode.barcode:
					duplicate = frappe.db.sql(
						"""select parent from `tabProduct Barcode` where barcode = %s and parent != %s""",
						(product_barcode.barcode, self.name),
					)
					if duplicate:
						frappe.throw(
							_("Barcode {0} already used in Product {1}").format(product_barcode.barcode, duplicate[0][0])
						)

					product_barcode.barcode_type = (
						"" if product_barcode.barcode_type not in options else product_barcode.barcode_type
					)
					if product_barcode.barcode_type and product_barcode.barcode_type.upper() in (
						"EAN",
						"UPC-A",
						"EAN-13",
						"EAN-8",
					):
						if not ean.is_valid(product_barcode.barcode):
							frappe.throw(
								_("Barcode {0} is not a valid {1} code").format(
									product_barcode.barcode, product_barcode.barcode_type
								),
								InvalidBarcode,
							)

	def validate_warehouse_for_reorder(self):
		"""Validate Reorder level table for duplicate and conditional mandatory"""
		warehouse = []
		for d in self.get("reorder_levels"):
			if not d.warehouse_group:
				d.warehouse_group = d.warehouse
			if d.get("warehouse") and d.get("warehouse") not in warehouse:
				warehouse += [d.get("warehouse")]
			else:
				frappe.throw(
					_("Row {0}: An Reorder entry already exists for this warehouse {1}").format(
						d.idx, d.warehouse
					),
					DuplicateReorderRows,
				)

			if d.warehouse_reorder_level and not d.warehouse_reorder_qty:
				frappe.throw(_("Row #{0}: Please set reorder quantity").format(d.idx))

	def stock_ledger_created(self):
		if not hasattr(self, "_stock_ledger_created"):
			self._stock_ledger_created = len(
				frappe.db.sql(
					"""select name from `tabStock Ledger Entry`
				where product_code = %s and is_cancelled = 0 limit 1""",
					self.name,
				)
			)
		return self._stock_ledger_created

	def update_product_price(self):
		frappe.db.sql(
			"""
				UPDATE `tabProduct Price`
				SET
					product_name=%(product_name)s,
					product_description=%(product_description)s,
					brand=%(brand)s
				WHERE product_code=%(product_code)s
			""",
			dict(
				product_name=self.product_name,
				product_description=self.description,
				brand=self.brand,
				product_code=self.name,
			),
		)

	def on_trash(self):
		frappe.db.sql("""delete from tabBin where product_code=%s""", self.name)
		frappe.db.sql("delete from `tabProduct Price` where product_code=%s", self.name)
		for variant_of in frappe.get_all("Product", filters={"variant_of": self.name}):
			frappe.delete_doc("Product", variant_of.name)

	def before_rename(self, old_name, new_name, merge=False):
		if self.product_name == old_name:
			frappe.db.set_value("Product", old_name, "product_name", new_name)

		if merge:
			self.validate_properties_before_merge(new_name)
			self.validate_duplicate_product_bundles_before_merge(old_name, new_name)
			self.validate_duplicate_website_product_before_merge(old_name, new_name)
			self.delete_old_bins(old_name)

	def after_rename(self, old_name, new_name, merge):
		if merge:
			self.validate_duplicate_product_in_stock_reconciliation(old_name, new_name)
			frappe.msgprint(
				_("It can take upto few hours for accurate stock values to be visible after merging products."),
				indicator="orange",
				title=_("Note"),
			)

		if self.published_in_website:
			invalidate_cache_for_product(self)

		frappe.db.set_value("Product", new_name, "product_code", new_name)

		if merge:
			self.set_last_purchase_rate(new_name)
			self.recalculate_bin_qty(new_name)

		for dt in ("Sales Taxes and Charges", "Purchase Taxes and Charges"):
			for d in frappe.db.sql(
				"""select name, product_wise_tax_detail from `tab{0}`
					where ifnull(product_wise_tax_detail, '') != ''""".format(
					dt
				),
				as_dict=1,
			):

				product_wise_tax_detail = json.loads(d.product_wise_tax_detail)
				if isinstance(product_wise_tax_detail, dict) and old_name in product_wise_tax_detail:
					product_wise_tax_detail[new_name] = product_wise_tax_detail[old_name]
					product_wise_tax_detail.pop(old_name)

					frappe.db.set_value(
						dt, d.name, "product_wise_tax_detail", json.dumps(product_wise_tax_detail), update_modified=False
					)

	def delete_old_bins(self, old_name):
		frappe.db.delete("Bin", {"product_code": old_name})

	def validate_duplicate_product_in_stock_reconciliation(self, old_name, new_name):
		records = frappe.db.sql(
			""" SELECT parent, COUNT(*) as records
			FROM `tabStock Reconciliation Product`
			WHERE product_code = %s and docstatus = 1
			GROUP By product_code, warehouse, parent
			HAVING records > 1
		""",
			new_name,
			as_dict=1,
		)

		if not records:
			return
		document = _("Stock Reconciliation") if len(records) == 1 else _("Stock Reconciliations")

		msg = _("The products {0} and {1} are present in the following {2} :").format(
			frappe.bold(old_name), frappe.bold(new_name), document
		)

		msg += " <br>"
		msg += (
			", ".join([get_link_to_form("Stock Reconciliation", d.parent) for d in records]) + "<br><br>"
		)

		msg += _(
			"Note: To merge the products, create a separate Stock Reconciliation for the old product {0}"
		).format(frappe.bold(old_name))

		frappe.throw(_(msg), title=_("Cannot Merge"), exc=DataValidationError)

	def validate_properties_before_merge(self, new_name):
		# Validate properties before merging
		if not frappe.db.exists("Product", new_name):
			frappe.throw(_("Product {0} does not exist").format(new_name))

		field_list = ["stock_uom", "is_stock_product", "has_serial_no", "has_batch_no"]
		new_properties = [cstr(d) for d in frappe.db.get_value("Product", new_name, field_list)]

		if new_properties != [cstr(self.get(field)) for field in field_list]:
			msg = _("To merge, following properties must be same for both products")
			msg += ": \n" + ", ".join([self.meta.get_label(fld) for fld in field_list])
			frappe.throw(msg, title=_("Cannot Merge"), exc=DataValidationError)

	def validate_duplicate_product_bundles_before_merge(self, old_name, new_name):
		"Block merge if both old and new products have product bundles."
		old_bundle = frappe.get_value("Product Bundle", filters={"new_product_code": old_name})
		new_bundle = frappe.get_value("Product Bundle", filters={"new_product_code": new_name})

		if old_bundle and new_bundle:
			bundle_link = get_link_to_form("Product Bundle", old_bundle)
			old_name, new_name = frappe.bold(old_name), frappe.bold(new_name)

			msg = _("Please delete Product Bundle {0}, before merging {1} into {2}").format(
				bundle_link, old_name, new_name
			)
			frappe.throw(msg, title=_("Cannot Merge"), exc=DataValidationError)

	def validate_duplicate_website_product_before_merge(self, old_name, new_name):
		"""
		Block merge if both old and new products have website products against them.
		This is to avoid duplicate website products after merging.
		"""
		web_products = frappe.get_all(
			"Website Product",
			filters={"product_code": ["in", [old_name, new_name]]},
			fields=["product_code", "name"],
		)

		if len(web_products) <= 1:
			return

		old_web_product = [d.get("name") for d in web_products if d.get("product_code") == old_name][0]
		web_product_link = get_link_to_form("Website Product", old_web_product)
		old_name, new_name = frappe.bold(old_name), frappe.bold(new_name)

		msg = f"Please delete linked Website Product {frappe.bold(web_product_link)} before merging {old_name} into {new_name}"
		frappe.throw(_(msg), title=_("Cannot Merge"), exc=DataValidationError)

	def set_last_purchase_rate(self, new_name):
		last_purchase_rate = get_last_purchase_details(new_name).get("base_net_rate", 0)
		frappe.db.set_value("Product", new_name, "last_purchase_rate", last_purchase_rate)

	def recalculate_bin_qty(self, new_name):
		from erpnext.stock.stock_balance import repost_stock

		existing_allow_negative_stock = frappe.db.get_value(
			"Stock Settings", None, "allow_negative_stock"
		)
		frappe.db.set_value("Stock Settings", None, "allow_negative_stock", 1)

		repost_stock_for_warehouses = frappe.get_all(
			"Stock Ledger Entry",
			"warehouse",
			filters={"product_code": new_name},
			pluck="warehouse",
			distinct=True,
		)

		# Delete all existing bins to avoid duplicate bins for the same product and warehouse
		frappe.db.delete("Bin", {"product_code": new_name})

		for warehouse in repost_stock_for_warehouses:
			repost_stock(new_name, warehouse)

		frappe.db.set_value(
			"Stock Settings", None, "allow_negative_stock", existing_allow_negative_stock
		)

	def update_bom_product_desc(self):
		if self.is_new():
			return

		if self.db_get("description") != self.description:
			frappe.db.sql(
				"""
				update `tabBOM`
				set description = %s
				where product = %s and docstatus < 2
			""",
				(self.description, self.name),
			)

			frappe.db.sql(
				"""
				update `tabBOM Product`
				set description = %s
				where product_code = %s and docstatus < 2
			""",
				(self.description, self.name),
			)

			frappe.db.sql(
				"""
				update `tabBOM Explosion Product`
				set description = %s
				where product_code = %s and docstatus < 2
			""",
				(self.description, self.name),
			)

	def validate_product_defaults(self):
		companies = {row.company for row in self.product_defaults}

		if len(companies) != len(self.product_defaults):
			frappe.throw(_("Cannot set multiple Product Defaults for a company."))

		validate_product_default_company_links(self.product_defaults)

	def update_defaults_from_product_group(self):
		"""Get defaults from Product Group"""
		if self.product_defaults or not self.product_group:
			return

		product_defaults = frappe.db.get_values(
			"Product Default",
			{"parent": self.product_group},
			[
				"company",
				"default_warehouse",
				"default_price_list",
				"buying_cost_center",
				"default_supplier",
				"expense_account",
				"selling_cost_center",
				"income_account",
			],
			as_dict=1,
		)
		if product_defaults:
			for product in product_defaults:
				self.append(
					"product_defaults",
					{
						"company": product.company,
						"default_warehouse": product.default_warehouse,
						"default_price_list": product.default_price_list,
						"buying_cost_center": product.buying_cost_center,
						"default_supplier": product.default_supplier,
						"expense_account": product.expense_account,
						"selling_cost_center": product.selling_cost_center,
						"income_account": product.income_account,
					},
				)
		else:
			defaults = frappe.defaults.get_defaults() or {}

			# To check default warehouse is belong to the default company
			if (
				defaults.get("default_warehouse")
				and defaults.company
				and frappe.db.exists(
					"Warehouse", {"name": defaults.default_warehouse, "company": defaults.company}
				)
			):
				self.append(
					"product_defaults",
					{"company": defaults.get("company"), "default_warehouse": defaults.default_warehouse},
				)

	def update_variants(self):
		if self.flags.dont_update_variants or frappe.db.get_single_value(
			"Product Variant Settings", "do_not_update_variants"
		):
			return
		if self.has_variants:
			variants = frappe.db.get_all("Product", fields=["product_code"], filters={"variant_of": self.name})
			if variants:
				if len(variants) <= 30:
					update_variants(variants, self, publish_progress=False)
					frappe.msgprint(_("Product Variants updated"))
				else:
					frappe.enqueue(
						"erpnext.stock.doctype.product.product.update_variants",
						variants=variants,
						template=self,
						now=frappe.flags.in_test,
						timeout=600,
						enqueue_after_commit=True,
					)

	def validate_has_variants(self):
		if not self.has_variants and frappe.db.get_value("Product", self.name, "has_variants"):
			if frappe.db.exists("Product", {"variant_of": self.name}):
				frappe.throw(_("Product has variants."))

	def validate_attributes_in_variants(self):
		if not self.has_variants or self.is_new():
			return

		old_doc = self.get_doc_before_save()
		old_doc_attributes = set([attr.attribute for attr in old_doc.attributes])
		own_attributes = [attr.attribute for attr in self.attributes]

		# Check if old attributes were removed from the list
		# Is old_attrs is a subset of new ones
		# that means we need not check any changes
		if old_doc_attributes.issubset(set(own_attributes)):
			return

		from collections import defaultdict

		# get all product variants
		products = [product["name"] for product in frappe.get_all("Product", {"variant_of": self.name})]

		# get all deleted attributes
		deleted_attribute = list(old_doc_attributes.difference(set(own_attributes)))

		# fetch all attributes of these products
		product_attributes = frappe.get_all(
			"Product Variant Attribute",
			filters={"parent": ["in", products], "attribute": ["in", deleted_attribute]},
			fields=["attribute", "parent"],
		)
		not_included = defaultdict(list)

		for attr in product_attributes:
			if attr["attribute"] not in own_attributes:
				not_included[attr["parent"]].append(attr["attribute"])

		if not len(not_included):
			return

		def body(docnames):
			docnames.sort()
			return "<br>".join(docnames)

		def table_row(title, body):
			return """<tr>
				<td>{0}</td>
				<td>{1}</td>
			</tr>""".format(
				title, body
			)

		rows = ""
		for docname, attr_list in not_included.products():
			link = "<a href='/app/Form/Product/{0}'>{0}</a>".format(frappe.bold(_(docname)))
			rows += table_row(link, body(attr_list))

		error_description = _(
			"The following deleted attributes exist in Variants but not in the Template. You can either delete the Variants or keep the attribute(s) in template."
		)

		message = """
			<div>{0}</div><br>
			<table class="table">
				<thead>
					<td>{1}</td>
					<td>{2}</td>
				</thead>
				{3}
			</table>
		""".format(
			error_description, _("Variant Products"), _("Attributes"), rows
		)

		frappe.throw(message, title=_("Variant Attribute Error"), is_minimizable=True, wide=True)

	def validate_stock_exists_for_template_product(self):
		if self.stock_ledger_created() and self._doc_before_save:
			if (
				cint(self._doc_before_save.has_variants) != cint(self.has_variants)
				or self._doc_before_save.variant_of != self.variant_of
			):
				frappe.throw(
					_(
						"Cannot change Variant properties after stock transaction. You will have to make a new Product to do this."
					).format(self.name),
					StockExistsForTemplate,
				)

			if self.has_variants or self.variant_of:
				if not self.is_child_table_same("attributes"):
					frappe.throw(
						_(
							"Cannot change Attributes after stock transaction. Make a new Product and transfer stock to the new Product"
						)
					)

	def validate_variant_based_on_change(self):
		if not self.is_new() and (
			self.variant_of or (self.has_variants and frappe.get_all("Product", {"variant_of": self.name}))
		):
			if self.variant_based_on != frappe.db.get_value("Product", self.name, "variant_based_on"):
				frappe.throw(_("Variant Based On cannot be changed"))

	def validate_uom(self):
		if not self.is_new():
			check_stock_uom_with_bin(self.name, self.stock_uom)
		if self.has_variants:
			for d in frappe.db.get_all("Product", filters={"variant_of": self.name}):
				check_stock_uom_with_bin(d.name, self.stock_uom)
		if self.variant_of:
			template_uom = frappe.db.get_value("Product", self.variant_of, "stock_uom")
			if template_uom != self.stock_uom:
				frappe.throw(
					_("Default Unit of Measure for Variant '{0}' must be same as in Template '{1}'").format(
						self.stock_uom, template_uom
					)
				)

	def validate_uom_conversion_factor(self):
		if self.uoms:
			for d in self.uoms:
				value = get_uom_conv_factor(d.uom, self.stock_uom)
				if value:
					d.conversion_factor = value

	def validate_attributes(self):
		if not (self.has_variants or self.variant_of):
			return

		if not self.variant_based_on:
			self.variant_based_on = "Product Attribute"

		if self.variant_based_on == "Product Attribute":
			attributes = []
			if not self.attributes:
				frappe.throw(_("Attribute table is mandatory"))
			for d in self.attributes:
				if d.attribute in attributes:
					frappe.throw(
						_("Attribute {0} selected multiple times in Attributes Table").format(d.attribute)
					)
				else:
					attributes.append(d.attribute)

	def validate_variant_attributes(self):
		if self.is_new() and self.variant_of and self.variant_based_on == "Product Attribute":
			# remove attributes with no attribute_value set
			self.attributes = [d for d in self.attributes if cstr(d.attribute_value).strip()]

			args = {}
			for i, d in enumerate(self.attributes):
				d.idx = i + 1
				args[d.attribute] = d.attribute_value

			variant = get_variant(self.variant_of, args, self.name)
			if variant:
				frappe.throw(
					_("Product variant {0} exists with same attributes").format(variant), ProductVariantExistsError
				)

			validate_product_variant_attributes(self, args)

			# copy variant_of value for each attribute row
			for d in self.attributes:
				d.variant_of = self.variant_of

	def cant_change(self):
		if self.is_new():
			return

		restricted_fields = ("has_serial_no", "is_stock_product", "valuation_method", "has_batch_no")

		values = frappe.db.get_value("Product", self.name, restricted_fields, as_dict=True)
		if not values:
			return

		if not values.get("valuation_method") and self.get("valuation_method"):
			values["valuation_method"] = (
				frappe.db.get_single_value("Stock Settings", "valuation_method") or "FIFO"
			)

		changed_fields = [
			field for field in restricted_fields if cstr(self.get(field)) != cstr(values.get(field))
		]
		if not changed_fields:
			return

		if linked_doc := self._get_linked_submitted_documents(changed_fields):
			changed_field_labels = [frappe.bold(self.meta.get_label(f)) for f in changed_fields]
			msg = _(
				"As there are existing submitted transactions against product {0}, you can not change the value of {1}."
			).format(self.name, ", ".join(changed_field_labels))

			if linked_doc and isinstance(linked_doc, dict):
				msg += "<br>"
				msg += _("Example of a linked document: {0}").format(
					frappe.get_desk_link(linked_doc.doctype, linked_doc.docname)
				)

			frappe.throw(msg, title=_("Linked with submitted documents"))

	def _get_linked_submitted_documents(self, changed_fields: List[str]) -> Optional[Dict[str, str]]:
		linked_doctypes = [
			"Delivery Note Product",
			"Sales Invoice Product",
			"POS Invoice Product",
			"Purchase Receipt Product",
			"Purchase Invoice Product",
			"Stock Entry Detail",
			"Stock Reconciliation Product",
		]

		# For "Is Stock Product", following doctypes is important
		# because reserved_qty, ordered_qty and requested_qty updated from these doctypes
		if "is_stock_product" in changed_fields:
			linked_doctypes += [
				"Sales Order Product",
				"Purchase Order Product",
				"Material Request Product",
				"Product Bundle",
				"BOM",
			]

		for doctype in linked_doctypes:
			filters = {"product_code": self.name, "docstatus": 1}

			if doctype in ("Product Bundle", "BOM"):
				if doctype == "Product Bundle":
					filters = {"new_product_code": self.name}
					fieldname = "new_product_code as docname"
				else:
					filters = {"product": self.name, "docstatus": 1}
					fieldname = "name as docname"

				if linked_doc := frappe.db.get_value(doctype, filters, fieldname, as_dict=True):
					return linked_doc.update({"doctype": doctype})

			elif doctype in (
				"Purchase Invoice Product",
				"Sales Invoice Product",
			):
				# If Invoice has Stock impact, only then consider it.
				if linked_doc := frappe.db.get_value(
					"Stock Ledger Entry",
					{"product_code": self.name, "is_cancelled": 0},
					["voucher_no as docname", "voucher_type as doctype"],
					as_dict=True,
				):
					return linked_doc

			elif linked_doc := frappe.db.get_value(
				doctype,
				filters,
				["parent as docname", "parenttype as doctype"],
				as_dict=True,
			):
				return linked_doc

	def validate_auto_reorder_enabled_in_stock_settings(self):
		if self.reorder_levels:
			enabled = frappe.db.get_single_value("Stock Settings", "auto_indent")
			if not enabled:
				frappe.msgprint(
					msg=_("You have to enable auto re-order in Stock Settings to maintain re-order levels."),
					title=_("Enable Auto Re-Order"),
					indicator="orange",
				)


def make_product_price(product, price_list_name, product_price):
	frappe.get_doc(
		{
			"doctype": "Product Price",
			"price_list": price_list_name,
			"product_code": product,
			"price_list_rate": product_price,
		}
	).insert()


def get_timeline_data(doctype, name):
	"""get timeline data based on Stock Ledger Entry. This is displayed as heatmap on the product page."""

	products = frappe.db.sql(
		"""select unix_timestamp(posting_date), count(*)
							from `tabStock Ledger Entry`
							where product_code=%s and posting_date > date_sub(curdate(), interval 1 year)
							group by posting_date""",
		name,
	)

	return dict(products)


def validate_end_of_life(product_code, end_of_life=None, disabled=None):
	if (not end_of_life) or (disabled is None):
		end_of_life, disabled = frappe.db.get_value("Product", product_code, ["end_of_life", "disabled"])

	if end_of_life and end_of_life != "0000-00-00" and getdate(end_of_life) <= now_datetime().date():
		frappe.throw(
			_("Product {0} has reached its end of life on {1}").format(product_code, formatdate(end_of_life))
		)

	if disabled:
		frappe.throw(_("Product {0} is disabled").format(product_code))


def validate_is_stock_product(product_code, is_stock_product=None):
	if not is_stock_product:
		is_stock_product = frappe.db.get_value("Product", product_code, "is_stock_product")

	if is_stock_product != 1:
		frappe.throw(_("Product {0} is not a stock Product").format(product_code))


def validate_cancelled_product(product_code, docstatus=None):
	if docstatus is None:
		docstatus = frappe.db.get_value("Product", product_code, "docstatus")

	if docstatus == 2:
		frappe.throw(_("Product {0} is cancelled").format(product_code))


def get_last_purchase_details(product_code, doc_name=None, conversion_rate=1.0):
	"""returns last purchase details in stock uom"""
	# get last purchase order product details

	last_purchase_order = frappe.db.sql(
		"""\
		select po.name, po.transaction_date, po.conversion_rate,
			po_product.conversion_factor, po_product.base_price_list_rate,
			po_product.discount_percentage, po_product.base_rate, po_product.base_net_rate
		from `tabPurchase Order` po, `tabPurchase Order Product` po_product
		where po.docstatus = 1 and po_product.product_code = %s and po.name != %s and
			po.name = po_product.parent
		order by po.transaction_date desc, po.name desc
		limit 1""",
		(product_code, cstr(doc_name)),
		as_dict=1,
	)

	# get last purchase receipt product details
	last_purchase_receipt = frappe.db.sql(
		"""\
		select pr.name, pr.posting_date, pr.posting_time, pr.conversion_rate,
			pr_product.conversion_factor, pr_product.base_price_list_rate, pr_product.discount_percentage,
			pr_product.base_rate, pr_product.base_net_rate
		from `tabPurchase Receipt` pr, `tabPurchase Receipt Product` pr_product
		where pr.docstatus = 1 and pr_product.product_code = %s and pr.name != %s and
			pr.name = pr_product.parent
		order by pr.posting_date desc, pr.posting_time desc, pr.name desc
		limit 1""",
		(product_code, cstr(doc_name)),
		as_dict=1,
	)

	purchase_order_date = getdate(
		last_purchase_order and last_purchase_order[0].transaction_date or "1900-01-01"
	)
	purchase_receipt_date = getdate(
		last_purchase_receipt and last_purchase_receipt[0].posting_date or "1900-01-01"
	)

	if last_purchase_order and (
		purchase_order_date >= purchase_receipt_date or not last_purchase_receipt
	):
		# use purchase order

		last_purchase = last_purchase_order[0]
		purchase_date = purchase_order_date

	elif last_purchase_receipt and (
		purchase_receipt_date > purchase_order_date or not last_purchase_order
	):
		# use purchase receipt
		last_purchase = last_purchase_receipt[0]
		purchase_date = purchase_receipt_date

	else:
		return frappe._dict()

	conversion_factor = flt(last_purchase.conversion_factor)
	out = frappe._dict(
		{
			"base_price_list_rate": flt(last_purchase.base_price_list_rate) / conversion_factor,
			"base_rate": flt(last_purchase.base_rate) / conversion_factor,
			"base_net_rate": flt(last_purchase.base_net_rate) / conversion_factor,
			"discount_percentage": flt(last_purchase.discount_percentage),
			"purchase_date": purchase_date,
		}
	)

	conversion_rate = flt(conversion_rate) or 1.0
	out.update(
		{
			"price_list_rate": out.base_price_list_rate / conversion_rate,
			"rate": out.base_rate / conversion_rate,
			"base_rate": out.base_rate,
			"base_net_rate": out.base_net_rate,
		}
	)

	return out


def invalidate_cache_for_product(doc):
	"""Invalidate Product Group cache and rebuild ProductVariantsCacheManager."""
	invalidate_cache_for(doc, doc.product_group)

	if doc.get("old_product_group") and doc.get("old_product_group") != doc.product_group:
		invalidate_cache_for(doc, doc.old_product_group)

	invalidate_product_variants_cache_for_website(doc)


def invalidate_product_variants_cache_for_website(doc):
	"""Rebuild ProductVariantsCacheManager via Product or Website Product."""
	from erpnext.e_commerce.variant_selector.product_variants_cache import ProductVariantsCacheManager

	product_code = None
	is_web_product = doc.get("published_in_website") or doc.get("published")
	if doc.has_variants and is_web_product:
		product_code = doc.product_code
	elif doc.variant_of and frappe.db.get_value("Product", doc.variant_of, "published_in_website"):
		product_code = doc.variant_of

	if product_code:
		product_cache = ProductVariantsCacheManager(product_code)
		product_cache.rebuild_cache()


def check_stock_uom_with_bin(product, stock_uom):
	if stock_uom == frappe.db.get_value("Product", product, "stock_uom"):
		return

	ref_uom = frappe.db.get_value("Stock Ledger Entry", {"product_code": product}, "stock_uom")

	if ref_uom:
		if cstr(ref_uom) != cstr(stock_uom):
			frappe.throw(
				_(
					"Default Unit of Measure for Product {0} cannot be changed directly because you have already made some transaction(s) with another UOM. You will need to create a new Product to use a different Default UOM."
				).format(product)
			)

	bin_list = frappe.db.sql(
		"""
			select * from `tabBin` where product_code = %s
				and (reserved_qty > 0 or ordered_qty > 0 or indented_qty > 0 or planned_qty > 0)
				and stock_uom != %s
			""",
		(product, stock_uom),
		as_dict=1,
	)

	if bin_list:
		frappe.throw(
			_(
				"Default Unit of Measure for Product {0} cannot be changed directly because you have already made some transaction(s) with another UOM. You need to either cancel the linked documents or create a new Product."
			).format(product)
		)

	# No SLE or documents against product. Bin UOM can be changed safely.
	frappe.db.sql("""update `tabBin` set stock_uom=%s where product_code=%s""", (stock_uom, product))


def get_product_defaults(product_code, company):
	product = frappe.get_cached_doc("Product", product_code)

	out = product.as_dict()

	for d in product.product_defaults:
		if d.company == company:
			row = copy.deepcopy(d.as_dict())
			row.pop("name")
			out.update(row)
	return out


def set_product_default(product_code, company, fieldname, value):
	product = frappe.get_cached_doc("Product", product_code)

	for d in product.product_defaults:
		if d.company == company:
			if not d.get(fieldname):
				frappe.db.set_value(d.doctype, d.name, fieldname, value)
			return

	# no row found, add a new row for the company
	d = product.append("product_defaults", {fieldname: value, "company": company})
	d.db_insert()
	product.clear_cache()


@frappe.whitelist()
def get_product_details(product_code, company=None):
	out = frappe._dict()
	if company:
		out = get_product_defaults(product_code, company) or frappe._dict()

	doc = frappe.get_cached_doc("Product", product_code)
	out.update(doc.as_dict())

	return out


@frappe.whitelist()
def get_uom_conv_factor(uom, stock_uom):
	"""Get UOM conversion factor from uom to stock_uom
	e.g. uom = "Kg", stock_uom = "Gram" then returns 1000.0
	"""
	if uom == stock_uom:
		return 1.0

	from_uom, to_uom = uom, stock_uom  # renaming for readability

	exact_match = frappe.db.get_value(
		"UOM Conversion Factor", {"to_uom": to_uom, "from_uom": from_uom}, ["value"], as_dict=1
	)
	if exact_match:
		return exact_match.value

	inverse_match = frappe.db.get_value(
		"UOM Conversion Factor", {"to_uom": from_uom, "from_uom": to_uom}, ["value"], as_dict=1
	)
	if inverse_match:
		return 1 / inverse_match.value

	# This attempts to try and get conversion from intermediate UOM.
	# case:
	# 			 g -> mg = 1000
	# 			 g -> kg = 0.001
	# therefore	 kg -> mg = 1000  / 0.001 = 1,000,000
	intermediate_match = frappe.db.sql(
		"""
			select (first.value / second.value) as value
			from `tabUOM Conversion Factor` first
			join `tabUOM Conversion Factor` second
				on first.from_uom = second.from_uom
			where
				first.to_uom = %(to_uom)s
				and second.to_uom = %(from_uom)s
			limit 1
			""",
		{"to_uom": to_uom, "from_uom": from_uom},
		as_dict=1,
	)

	if intermediate_match:
		return intermediate_match[0].value


@frappe.whitelist()
def get_product_attribute(parent, attribute_value=""):
	"""Used for providing auto-completions in child table."""
	if not frappe.has_permission("Product"):
		frappe.throw(_("No Permission"))

	return frappe.get_all(
		"Product Attribute Value",
		fields=["attribute_value"],
		filters={"parent": parent, "attribute_value": ("like", f"%{attribute_value}%")},
	)


def update_variants(variants, template, publish_progress=True):
	total = len(variants)
	for count, d in enumerate(variants, start=1):
		variant = frappe.get_doc("Product", d)
		copy_attributes_to_variant(template, variant)
		variant.save()
		if publish_progress:
			frappe.publish_progress(count / total * 100, title=_("Updating Variants..."))


@erpnext.allow_regional
def set_product_tax_from_hsn_code(product):
	pass


def validate_product_default_company_links(product_defaults: List[ProductDefault]) -> None:
	for product_default in product_defaults:
		for doctype, field in [
			["Warehouse", "default_warehouse"],
			["Cost Center", "buying_cost_center"],
			["Cost Center", "selling_cost_center"],
			["Account", "expense_account"],
			["Account", "income_account"],
		]:
			if product_default.get(field):
				company = frappe.db.get_value(doctype, product_default.get(field), "company", cache=True)
				if company and company != product_default.company:
					frappe.throw(
						_("Row #{}: {} {} doesn't belong to Company {}. Please select valid {}.").format(
							product_default.idx,
							doctype,
							frappe.bold(product_default.get(field)),
							frappe.bold(product_default.company),
							frappe.bold(frappe.unscrub(field)),
						),
						title=_("Invalid Product Defaults"),
					)


@frappe.whitelist()
def get_asset_naming_series():
	from erpnext.assets.doctype.asset.asset import get_asset_naming_series

	return get_asset_naming_series()
