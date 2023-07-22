# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import IfNull, Sum
from frappe.utils import flt


def execute(filters=None):
	if not filters:
		filters = {}

	columns = get_columns(filters)
	product_map = get_product_details(filters)
	pl = get_price_list()
	last_purchase_rate = get_last_purchase_rate()
	bom_rate = get_product_bom_rate()
	val_rate_map = get_valuation_rate()

	from erpnext.accounts.utils import get_currency_precision

	precision = get_currency_precision() or 2
	data = []
	for product in sorted(product_map):
		data.append(
			[
				product,
				product_map[product]["product_name"],
				product_map[product]["product_group"],
				product_map[product]["brand"],
				product_map[product]["description"],
				product_map[product]["stock_uom"],
				flt(last_purchase_rate.get(product, 0), precision),
				flt(val_rate_map.get(product, 0), precision),
				pl.get(product, {}).get("Selling"),
				pl.get(product, {}).get("Buying"),
				flt(bom_rate.get(product, 0), precision),
			]
		)

	return columns, data


def get_columns(filters):
	"""return columns based on filters"""

	columns = [
		_("Product") + ":Link/Product:100",
		_("Product Name") + "::150",
		_("Product Group") + ":Link/Product Group:125",
		_("Brand") + "::100",
		_("Description") + "::150",
		_("UOM") + ":Link/UOM:80",
		_("Last Purchase Rate") + ":Currency:90",
		_("Valuation Rate") + ":Currency:80",
		_("Sales Price List") + "::180",
		_("Purchase Price List") + "::180",
		_("BOM Rate") + ":Currency:90",
	]

	return columns


def get_product_details(filters):
	"""returns all products details"""

	product_map = {}

	product = frappe.qb.DocType("Product")
	query = (
		frappe.qb.from_(product)
		.select(product.name, product.product_group, product.product_name, product.description, product.brand, product.stock_uom)
		.orderby(product.product_code, product.product_group)
	)

	if filters.get("products") == "Enabled Products only":
		query = query.where(product.disabled == 0)
	elif filters.get("products") == "Disabled Products only":
		query = query.where(product.disabled == 1)

	for i in query.run(as_dict=True):
		product_map.setdefault(i.name, i)

	return product_map


def get_price_list():
	"""Get selling & buying price list of every product"""

	rate = {}

	ip = frappe.qb.DocType("Product Price")
	pl = frappe.qb.DocType("Price List")
	cu = frappe.qb.DocType("Currency")

	price_list = (
		frappe.qb.from_(ip)
		.from_(pl)
		.from_(cu)
		.select(
			ip.product_code,
			ip.buying,
			ip.selling,
			(IfNull(cu.symbol, ip.currency)).as_("currency"),
			ip.price_list_rate,
			ip.price_list,
		)
		.where((ip.price_list == pl.name) & (pl.currency == cu.name) & (pl.enabled == 1))
	).run(as_dict=True)

	for d in price_list:
		d.update(
			{"price": "{0} {1} - {2}".format(d.currency, round(d.price_list_rate, 2), d.price_list)}
		)
		d.pop("currency")
		d.pop("price_list_rate")
		d.pop("price_list")

		if d.price:
			rate.setdefault(d.product_code, {}).setdefault("Buying" if d.buying else "Selling", []).append(
				d.price
			)

	product_rate_map = {}

	for product in rate:
		for buying_or_selling in rate[product]:
			product_rate_map.setdefault(product, {}).setdefault(
				buying_or_selling, ", ".join(rate[product].get(buying_or_selling, []))
			)

	return product_rate_map


def get_last_purchase_rate():
	product_last_purchase_rate_map = {}

	po = frappe.qb.DocType("Purchase Order")
	pr = frappe.qb.DocType("Purchase Receipt")
	pi = frappe.qb.DocType("Purchase Invoice")
	po_product = frappe.qb.DocType("Purchase Order Product")
	pr_product = frappe.qb.DocType("Purchase Receipt Product")
	pi_product = frappe.qb.DocType("Purchase Invoice Product")

	query = (
		frappe.qb.from_(
			(
				frappe.qb.from_(po)
				.from_(po_product)
				.select(po_product.product_code, po.transaction_date.as_("posting_date"), po_product.base_rate)
				.where((po.name == po_product.parent) & (po.docstatus == 1))
			)
			+ (
				frappe.qb.from_(pr)
				.from_(pr_product)
				.select(pr_product.product_code, pr.posting_date, pr_product.base_rate)
				.where((pr.name == pr_product.parent) & (pr.docstatus == 1))
			)
			+ (
				frappe.qb.from_(pi)
				.from_(pi_product)
				.select(pi_product.product_code, pi.posting_date, pi_product.base_rate)
				.where((pi.name == pi_product.parent) & (pi.docstatus == 1) & (pi.update_stock == 1))
			)
		)
		.select("*")
		.orderby("product_code", "posting_date")
	)

	for d in query.run(as_dict=True):
		product_last_purchase_rate_map[d.product_code] = d.base_rate

	return product_last_purchase_rate_map


def get_product_bom_rate():
	"""Get BOM rate of an product from BOM"""

	product_bom_map = {}

	bom = frappe.qb.DocType("BOM")
	bom_data = (
		frappe.qb.from_(bom)
		.select(bom.product, (bom.total_cost / bom.quantity).as_("bom_rate"))
		.where((bom.is_active == 1) & (bom.is_default == 1))
	).run(as_dict=True)

	for d in bom_data:
		product_bom_map.setdefault(d.product, flt(d.bom_rate))

	return product_bom_map


def get_valuation_rate():
	"""Get an average valuation rate of an product from all warehouses"""

	product_val_rate_map = {}

	bin = frappe.qb.DocType("Bin")
	bin_data = (
		frappe.qb.from_(bin)
		.select(
			bin.product_code, Sum(bin.actual_qty * bin.valuation_rate) / Sum(bin.actual_qty).as_("val_rate")
		)
		.where(bin.actual_qty > 0)
		.groupby(bin.product_code)
	).run(as_dict=True)

	for d in bin_data:
		product_val_rate_map.setdefault(d.product_code, d.val_rate)

	return product_val_rate_map
