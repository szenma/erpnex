# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import cint, flt, fmt_money, getdate, nowdate

from erpnext.accounts.doctype.pricing_rule.pricing_rule import get_pricing_rule_for_product
from erpnext.stock.doctype.batch.batch import get_batch_qty


def get_web_product_qty_in_stock(product_code, product_warehouse_field, warehouse=None):
	in_stock, stock_qty = 0, ""
	template_product_code, is_stock_product = frappe.db.get_value(
		"Product", product_code, ["variant_of", "is_stock_product"]
	)

	if not warehouse:
		warehouse = frappe.db.get_value("Website Product", {"product_code": product_code}, product_warehouse_field)

	if not warehouse and template_product_code and template_product_code != product_code:
		warehouse = frappe.db.get_value(
			"Website Product", {"product_code": template_product_code}, product_warehouse_field
		)

	if warehouse:
		stock_qty = frappe.db.sql(
			"""
			select GREATEST(S.actual_qty - S.reserved_qty - S.reserved_qty_for_production - S.reserved_qty_for_sub_contract, 0) / IFNULL(C.conversion_factor, 1)
			from tabBin S
			inner join `tabProduct` I on S.product_code = I.Product_code
			left join `tabUOM Conversion Detail` C on I.sales_uom = C.uom and C.parent = I.Product_code
			where S.product_code=%s and S.warehouse=%s""",
			(product_code, warehouse),
		)

		if stock_qty:
			stock_qty = adjust_qty_for_expired_products(product_code, stock_qty, warehouse)
			in_stock = stock_qty[0][0] > 0 and 1 or 0

	return frappe._dict(
		{"in_stock": in_stock, "stock_qty": stock_qty, "is_stock_product": is_stock_product}
	)


def adjust_qty_for_expired_products(product_code, stock_qty, warehouse):
	batches = frappe.get_all("Batch", filters=[{"product": product_code}], fields=["expiry_date", "name"])
	expired_batches = get_expired_batches(batches)
	stock_qty = [list(product) for product in stock_qty]

	for batch in expired_batches:
		if warehouse:
			stock_qty[0][0] = max(0, stock_qty[0][0] - get_batch_qty(batch, warehouse))
		else:
			stock_qty[0][0] = max(0, stock_qty[0][0] - qty_from_all_warehouses(get_batch_qty(batch)))

		if not stock_qty[0][0]:
			break

	return stock_qty


def get_expired_batches(batches):
	"""
	:param batches: A list of dict in the form [{'expiry_date': datetime.date(20XX, 1, 1), 'name': 'batch_id'}, ...]
	"""
	return [b.name for b in batches if b.expiry_date and b.expiry_date <= getdate(nowdate())]


def qty_from_all_warehouses(batch_info):
	"""
	:param batch_info: A list of dict in the form [{u'warehouse': u'Stores - I', u'qty': 0.8}, ...]
	"""
	qty = 0
	for batch in batch_info:
		qty = qty + batch.qty

	return qty


def get_price(product_code, price_list, customer_group, company, qty=1):
	from erpnext.e_commerce.shopping_cart.cart import get_party

	template_product_code = frappe.db.get_value("Product", product_code, "variant_of")

	if price_list:
		price = frappe.get_all(
			"Product Price",
			fields=["price_list_rate", "currency"],
			filters={"price_list": price_list, "product_code": product_code},
		)

		if template_product_code and not price:
			price = frappe.get_all(
				"Product Price",
				fields=["price_list_rate", "currency"],
				filters={"price_list": price_list, "product_code": template_product_code},
			)

		if price:
			party = get_party()
			pricing_rule_dict = frappe._dict(
				{
					"product_code": product_code,
					"qty": qty,
					"stock_qty": qty,
					"transaction_type": "selling",
					"price_list": price_list,
					"customer_group": customer_group,
					"company": company,
					"conversion_rate": 1,
					"for_shopping_cart": True,
					"currency": frappe.db.get_value("Price List", price_list, "currency"),
					"doctype": "Quotation",
				}
			)

			if party and party.doctype == "Customer":
				pricing_rule_dict.update({"customer": party.name})

			pricing_rule = get_pricing_rule_for_product(pricing_rule_dict)
			price_obj = price[0]

			if pricing_rule:
				# price without any rules applied
				mrp = price_obj.price_list_rate or 0

				if pricing_rule.pricing_rule_for == "Discount Percentage":
					price_obj.discount_percent = pricing_rule.discount_percentage
					price_obj.formatted_discount_percent = str(flt(pricing_rule.discount_percentage, 0)) + "%"
					price_obj.price_list_rate = flt(
						price_obj.price_list_rate * (1.0 - (flt(pricing_rule.discount_percentage) / 100.0))
					)

				if pricing_rule.pricing_rule_for == "Rate":
					rate_discount = flt(mrp) - flt(pricing_rule.price_list_rate)
					if rate_discount > 0:
						price_obj.formatted_discount_rate = fmt_money(rate_discount, currency=price_obj["currency"])
					price_obj.price_list_rate = pricing_rule.price_list_rate or 0

			if price_obj:
				price_obj["formatted_price"] = fmt_money(
					price_obj["price_list_rate"], currency=price_obj["currency"]
				)
				if mrp != price_obj["price_list_rate"]:
					price_obj["formatted_mrp"] = fmt_money(mrp, currency=price_obj["currency"])

				price_obj["currency_symbol"] = (
					not cint(frappe.db.get_default("hide_currency_symbol"))
					and (
						frappe.db.get_value("Currency", price_obj.currency, "symbol", cache=True)
						or price_obj.currency
					)
					or ""
				)

				uom_conversion_factor = frappe.db.sql(
					"""select	C.conversion_factor
					from `tabUOM Conversion Detail` C
					inner join `tabProduct` I on C.parent = I.name and C.uom = I.sales_uom
					where I.name = %s""",
					product_code,
				)

				uom_conversion_factor = uom_conversion_factor[0][0] if uom_conversion_factor else 1
				price_obj["formatted_price_sales_uom"] = fmt_money(
					price_obj["price_list_rate"] * uom_conversion_factor, currency=price_obj["currency"]
				)

				if not price_obj["price_list_rate"]:
					price_obj["price_list_rate"] = 0

				if not price_obj["currency"]:
					price_obj["currency"] = ""

				if not price_obj["formatted_price"]:
					price_obj["formatted_price"], price_obj["formatted_mrp"] = "", ""

			return price_obj


def get_non_stock_product_status(product_code, product_warehouse_field):
	# if product is a product bundle, check if its bundle products are in stock
	if frappe.db.exists("Product Bundle", product_code):
		products = frappe.get_doc("Product Bundle", product_code).get_all_children()
		bundle_warehouse = frappe.db.get_value(
			"Website Product", {"product_code": product_code}, product_warehouse_field
		)
		return all(
			get_web_product_qty_in_stock(d.product_code, product_warehouse_field, bundle_warehouse).in_stock
			for d in products
		)
	else:
		return 1
