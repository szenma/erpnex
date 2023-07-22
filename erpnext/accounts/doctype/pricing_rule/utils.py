# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

# For license information, please see license.txt


import copy
import json

import frappe
from frappe import _, bold
from frappe.utils import cint, flt, fmt_money, get_link_to_form, getdate, today

from erpnext.setup.doctype.product_group.product_group import get_child_product_groups
from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses
from erpnext.stock.get_product_details import get_conversion_factor


class MultiplePricingRuleConflict(frappe.ValidationError):
	pass


apply_on_table = {"Product Code": "products", "Product Group": "product_groups", "Brand": "brands"}


def get_pricing_rules(args, doc=None):
	pricing_rules = []
	values = {}

	if not frappe.db.exists("Pricing Rule", {"disable": 0, args.transaction_type: 1}):
		return

	for apply_on in ["Product Code", "Product Group", "Brand"]:
		pricing_rules.extend(_get_pricing_rules(apply_on, args, values))
		if pricing_rules and not apply_multiple_pricing_rules(pricing_rules):
			break

	rules = []

	pricing_rules = filter_pricing_rule_based_on_condition(pricing_rules, doc)

	if not pricing_rules:
		return []

	if apply_multiple_pricing_rules(pricing_rules):
		pricing_rules = sorted_by_priority(pricing_rules, args, doc)
		for pricing_rule in pricing_rules:
			if isinstance(pricing_rule, list):
				rules.extend(pricing_rule)
			else:
				rules.append(pricing_rule)
	else:
		pricing_rule = filter_pricing_rules(args, pricing_rules, doc)
		if pricing_rule:
			rules.append(pricing_rule)

	return rules


def sorted_by_priority(pricing_rules, args, doc=None):
	# If more than one pricing rules, then sort by priority
	pricing_rules_list = []
	pricing_rule_dict = {}

	for pricing_rule in pricing_rules:
		pricing_rule = filter_pricing_rules(args, pricing_rule, doc)
		if pricing_rule:
			if not pricing_rule.get("priority"):
				pricing_rule["priority"] = 1

			if pricing_rule.get("apply_multiple_pricing_rules"):
				pricing_rule_dict.setdefault(cint(pricing_rule.get("priority")), []).append(pricing_rule)

	for key in sorted(pricing_rule_dict):
		pricing_rules_list.extend(pricing_rule_dict.get(key))

	return pricing_rules_list


def filter_pricing_rule_based_on_condition(pricing_rules, doc=None):
	filtered_pricing_rules = []
	if doc:
		for pricing_rule in pricing_rules:
			if pricing_rule.condition:
				try:
					if frappe.safe_eval(pricing_rule.condition, None, doc.as_dict()):
						filtered_pricing_rules.append(pricing_rule)
				except Exception:
					pass
			else:
				filtered_pricing_rules.append(pricing_rule)
	else:
		filtered_pricing_rules = pricing_rules

	return filtered_pricing_rules


def _get_pricing_rules(apply_on, args, values):
	apply_on_field = frappe.scrub(apply_on)

	if not args.get(apply_on_field):
		return []

	child_doc = "`tabPricing Rule {0}`".format(apply_on)

	conditions = product_variant_condition = product_conditions = ""
	values[apply_on_field] = args.get(apply_on_field)
	if apply_on_field in ["product_code", "brand"]:
		product_conditions = "{child_doc}.{apply_on_field}= %({apply_on_field})s".format(
			child_doc=child_doc, apply_on_field=apply_on_field
		)

		if apply_on_field == "product_code":
			if args.get("uom", None):
				product_conditions += (
					" and ({child_doc}.uom='{product_uom}' or IFNULL({child_doc}.uom, '')='')".format(
						child_doc=child_doc, product_uom=args.get("uom")
					)
				)
			if "variant_of" not in args:
				args.variant_of = frappe.get_cached_value("Product", args.product_code, "variant_of")

			if args.variant_of:
				product_variant_condition = " or {child_doc}.product_code=%(variant_of)s ".format(
					child_doc=child_doc
				)
				values["variant_of"] = args.variant_of
	elif apply_on_field == "product_group":
		product_conditions = _get_tree_conditions(args, "Product Group", child_doc, False)

	conditions += get_other_conditions(conditions, values, args)
	warehouse_conditions = _get_tree_conditions(args, "Warehouse", "`tabPricing Rule`")
	if warehouse_conditions:
		warehouse_conditions = " and {0}".format(warehouse_conditions)

	if not args.price_list:
		args.price_list = None

	conditions += " and ifnull(`tabPricing Rule`.for_price_list, '') in (%(price_list)s, '')"
	values["price_list"] = args.get("price_list")

	pricing_rules = (
		frappe.db.sql(
			"""select `tabPricing Rule`.*,
			{child_doc}.{apply_on_field}, {child_doc}.uom
		from `tabPricing Rule`, {child_doc}
		where ({product_conditions} or (`tabPricing Rule`.apply_rule_on_other is not null
			and `tabPricing Rule`.{apply_on_other_field}=%({apply_on_field})s) {product_variant_condition})
			and {child_doc}.parent = `tabPricing Rule`.name
			and `tabPricing Rule`.disable = 0 and
			`tabPricing Rule`.{transaction_type} = 1 {warehouse_cond} {conditions}
		order by `tabPricing Rule`.priority desc,
			`tabPricing Rule`.name desc""".format(
				child_doc=child_doc,
				apply_on_field=apply_on_field,
				product_conditions=product_conditions,
				product_variant_condition=product_variant_condition,
				transaction_type=args.transaction_type,
				warehouse_cond=warehouse_conditions,
				apply_on_other_field="other_{0}".format(apply_on_field),
				conditions=conditions,
			),
			values,
			as_dict=1,
		)
		or []
	)

	return pricing_rules


def apply_multiple_pricing_rules(pricing_rules):
	apply_multiple_rule = [
		d.apply_multiple_pricing_rules for d in pricing_rules if d.apply_multiple_pricing_rules
	]

	if not apply_multiple_rule:
		return False

	return True


def _get_tree_conditions(args, parenttype, table, allow_blank=True):
	field = frappe.scrub(parenttype)
	condition = ""
	if args.get(field):
		if not frappe.flags.tree_conditions:
			frappe.flags.tree_conditions = {}
		key = (parenttype, args.get(field))
		if key in frappe.flags.tree_conditions:
			return frappe.flags.tree_conditions[key]

		try:
			lft, rgt = frappe.db.get_value(parenttype, args.get(field), ["lft", "rgt"])
		except TypeError:
			frappe.throw(_("Invalid {0}").format(args.get(field)))

		parent_groups = frappe.db.sql_list(
			"""select name from `tab%s`
			where lft<=%s and rgt>=%s"""
			% (parenttype, "%s", "%s"),
			(lft, rgt),
		)

		if parenttype in ["Customer Group", "Product Group", "Territory"]:
			parent_field = "parent_{0}".format(frappe.scrub(parenttype))
			root_name = frappe.db.get_list(
				parenttype,
				{"is_group": 1, parent_field: ("is", "not set")},
				"name",
				as_list=1,
				ignore_permissions=True,
			)

			if root_name and root_name[0][0]:
				parent_groups.append(root_name[0][0])

		if parent_groups:
			if allow_blank:
				parent_groups.append("")
			condition = "ifnull({table}.{field}, '') in ({parent_groups})".format(
				table=table, field=field, parent_groups=", ".join(frappe.db.escape(d) for d in parent_groups)
			)

			frappe.flags.tree_conditions[key] = condition
	return condition


def get_other_conditions(conditions, values, args):
	for field in ["company", "customer", "supplier", "campaign", "sales_partner"]:
		if args.get(field):
			conditions += " and ifnull(`tabPricing Rule`.{0}, '') in (%({1})s, '')".format(field, field)
			values[field] = args.get(field)
		else:
			conditions += " and ifnull(`tabPricing Rule`.{0}, '') = ''".format(field)

	for parenttype in ["Customer Group", "Territory", "Supplier Group"]:
		group_condition = _get_tree_conditions(args, parenttype, "`tabPricing Rule`")
		if group_condition:
			conditions += " and " + group_condition

	if args.get("transaction_date"):
		conditions += """ and %(transaction_date)s between ifnull(`tabPricing Rule`.valid_from, '2000-01-01')
			and ifnull(`tabPricing Rule`.valid_upto, '2500-12-31')"""
		values["transaction_date"] = args.get("transaction_date")

	if args.get("doctype") in [
		"Quotation",
		"Quotation Product",
		"Sales Order",
		"Sales Order Product",
		"Delivery Note",
		"Delivery Note Product",
		"Sales Invoice",
		"Sales Invoice Product",
		"POS Invoice",
		"POS Invoice Product",
	]:
		conditions += """ and ifnull(`tabPricing Rule`.selling, 0) = 1"""
	else:
		conditions += """ and ifnull(`tabPricing Rule`.buying, 0) = 1"""

	return conditions


def filter_pricing_rules(args, pricing_rules, doc=None):
	if not isinstance(pricing_rules, list):
		pricing_rules = [pricing_rules]

	original_pricing_rule = copy.copy(pricing_rules)

	# filter for qty
	if pricing_rules:
		stock_qty = flt(args.get("stock_qty"))
		amount = flt(args.get("price_list_rate")) * flt(args.get("qty"))

		pr_doc = frappe.get_cached_doc("Pricing Rule", pricing_rules[0].name)

		if pricing_rules[0].mixed_conditions and doc:
			stock_qty, amount, products = get_qty_and_rate_for_mixed_conditions(doc, pr_doc, args)
			for pricing_rule_args in pricing_rules:
				pricing_rule_args.apply_rule_on_other_products = products

		elif pricing_rules[0].is_cumulative:
			products = [args.get(frappe.scrub(pr_doc.get("apply_on")))]
			data = get_qty_amount_data_for_cumulative(pr_doc, args, products)

			if data:
				stock_qty += data[0]
				amount += data[1]

		if pricing_rules[0].apply_rule_on_other and not pricing_rules[0].mixed_conditions and doc:
			pricing_rules = get_qty_and_rate_for_other_product(doc, pr_doc, pricing_rules, args) or []
		else:
			pricing_rules = filter_pricing_rules_for_qty_amount(stock_qty, amount, pricing_rules, args)

		if not pricing_rules:
			for d in original_pricing_rule:
				if not d.threshold_percentage:
					continue

				msg = validate_quantity_and_amount_for_suggestion(
					d, stock_qty, amount, args.get("product_code"), args.get("transaction_type")
				)

				if msg:
					return {"suggestion": msg, "product_code": args.get("product_code")}

		# add variant_of property in pricing rule
		for p in pricing_rules:
			if p.product_code and args.variant_of:
				p.variant_of = args.variant_of
			else:
				p.variant_of = None

	if len(pricing_rules) > 1:
		filtered_rules = list(filter(lambda x: x.currency == args.get("currency"), pricing_rules))
		if filtered_rules:
			pricing_rules = filtered_rules

	# find pricing rule with highest priority
	if pricing_rules:
		max_priority = max(cint(p.priority) for p in pricing_rules)
		if max_priority:
			pricing_rules = list(filter(lambda x: cint(x.priority) == max_priority, pricing_rules))

	if pricing_rules and not isinstance(pricing_rules, list):
		pricing_rules = list(pricing_rules)

	if len(pricing_rules) > 1:
		rate_or_discount = list(set(d.rate_or_discount for d in pricing_rules))
		if len(rate_or_discount) == 1 and rate_or_discount[0] == "Discount Percentage":
			pricing_rules = (
				list(filter(lambda x: x.for_price_list == args.price_list, pricing_rules)) or pricing_rules
			)

	if len(pricing_rules) > 1 and not args.for_shopping_cart:
		frappe.throw(
			_(
				"Multiple Price Rules exists with same criteria, please resolve conflict by assigning priority. Price Rules: {0}"
			).format("\n".join(d.name for d in pricing_rules)),
			MultiplePricingRuleConflict,
		)
	elif pricing_rules:
		return pricing_rules[0]


def validate_quantity_and_amount_for_suggestion(args, qty, amount, product_code, transaction_type):
	fieldname, msg = "", ""
	type_of_transaction = "purchase" if transaction_type == "buying" else "sale"

	for field, value in {"min_qty": qty, "min_amt": amount}.products():
		if (
			args.get(field)
			and value < args.get(field)
			and (args.get(field) - cint(args.get(field) * args.threshold_percentage * 0.01)) <= value
		):
			fieldname = field

	for field, value in {"max_qty": qty, "max_amt": amount}.products():
		if (
			args.get(field)
			and value > args.get(field)
			and (args.get(field) + cint(args.get(field) * args.threshold_percentage * 0.01)) >= value
		):
			fieldname = field

	if fieldname:
		msg = _(
			"If you {0} {1} quantities of the product {2}, the scheme {3} will be applied on the product."
		).format(type_of_transaction, args.get(fieldname), bold(product_code), bold(args.title))

		if fieldname in ["min_amt", "max_amt"]:
			msg = _("If you {0} {1} worth product {2}, the scheme {3} will be applied on the product.").format(
				type_of_transaction,
				fmt_money(args.get(fieldname), currency=args.get("currency")),
				bold(product_code),
				bold(args.title),
			)

		frappe.msgprint(msg)

	return msg


def filter_pricing_rules_for_qty_amount(qty, rate, pricing_rules, args=None):
	rules = []

	for rule in pricing_rules:
		status = False
		conversion_factor = 1

		if rule.get("uom"):
			conversion_factor = get_conversion_factor(rule.product_code, rule.uom).get("conversion_factor", 1)

		if flt(qty) >= (flt(rule.min_qty) * conversion_factor) and (
			flt(qty) <= (rule.max_qty * conversion_factor) if rule.max_qty else True
		):
			status = True

		# if user has created product price against the transaction UOM
		if args and rule.get("uom") == args.get("uom"):
			conversion_factor = 1.0

		if status and (
			flt(rate) >= (flt(rule.min_amt) * conversion_factor)
			and (flt(rate) <= (rule.max_amt * conversion_factor) if rule.max_amt else True)
		):
			status = True
		else:
			status = False

		if status:
			rules.append(rule)

	return rules


def if_all_rules_same(pricing_rules, fields):
	all_rules_same = True
	val = [pricing_rules[0].get(k) for k in fields]
	for p in pricing_rules[1:]:
		if val != [p.get(k) for k in fields]:
			all_rules_same = False
			break

	return all_rules_same


def apply_internal_priority(pricing_rules, field_set, args):
	filtered_rules = []
	for field in field_set:
		if args.get(field):
			# filter function always returns a filter object even if empty
			# list conversion is necessary to check for an empty result
			filtered_rules = list(filter(lambda x: x.get(field) == args.get(field), pricing_rules))
			if filtered_rules:
				break

	return filtered_rules or pricing_rules


def get_qty_and_rate_for_mixed_conditions(doc, pr_doc, args):
	sum_qty, sum_amt = [0, 0]
	products = get_pricing_rule_products(pr_doc) or []
	apply_on = frappe.scrub(pr_doc.get("apply_on"))

	if products and doc.get("products"):
		for row in doc.get("products"):
			if (row.get(apply_on) or args.get(apply_on)) not in products:
				continue

			if pr_doc.mixed_conditions:
				amt = args.get("qty") * args.get("price_list_rate")
				if args.get("product_code") != row.get("product_code"):
					amt = flt(row.get("qty")) * flt(row.get("price_list_rate") or args.get("rate"))

				sum_qty += flt(row.get("stock_qty")) or flt(args.get("stock_qty")) or flt(args.get("qty"))
				sum_amt += amt

		if pr_doc.is_cumulative:
			data = get_qty_amount_data_for_cumulative(pr_doc, doc, products)

			if data and data[0]:
				sum_qty += data[0]
				sum_amt += data[1]

	return sum_qty, sum_amt, products


def get_qty_and_rate_for_other_product(doc, pr_doc, pricing_rules, row_product):
	other_products = get_pricing_rule_products(pr_doc, other_products=True)
	pricing_rule_apply_on = apply_on_table.get(pr_doc.get("apply_on"))
	apply_on = frappe.scrub(pr_doc.get("apply_on"))

	products = []
	for d in pr_doc.get(pricing_rule_apply_on):
		if apply_on == "product_group":
			products.extend(get_child_product_groups(d.get(apply_on)))
		else:
			products.append(d.get(apply_on))

	for row in doc.products:
		if row.get(apply_on) in products:
			if not row.get("qty"):
				continue

			stock_qty = row.get("qty") * (row.get("conversion_factor") or 1.0)
			amount = stock_qty * (row.get("price_list_rate") or row.get("rate"))
			pricing_rules = filter_pricing_rules_for_qty_amount(stock_qty, amount, pricing_rules, row)

			if pricing_rules and pricing_rules[0]:
				pricing_rules[0].apply_rule_on_other_products = other_products
				return pricing_rules


def get_qty_amount_data_for_cumulative(pr_doc, doc, products=None):
	if products is None:
		products = []
	sum_qty, sum_amt = [0, 0]
	doctype = doc.get("parenttype") or doc.doctype

	date_field = (
		"transaction_date" if frappe.get_meta(doctype).has_field("transaction_date") else "posting_date"
	)

	child_doctype = "{0} Product".format(doctype)
	apply_on = frappe.scrub(pr_doc.get("apply_on"))

	values = [pr_doc.valid_from, pr_doc.valid_upto]
	condition = ""

	if pr_doc.warehouse:
		warehouses = get_child_warehouses(pr_doc.warehouse)

		condition += """ and `tab{child_doc}`.warehouse in ({warehouses})
			""".format(
			child_doc=child_doctype, warehouses=",".join(["%s"] * len(warehouses))
		)

		values.extend(warehouses)

	if products:
		condition = " and `tab{child_doc}`.{apply_on} in ({products})".format(
			child_doc=child_doctype, apply_on=apply_on, products=",".join(["%s"] * len(products))
		)

		values.extend(products)

	data_set = frappe.db.sql(
		""" SELECT `tab{child_doc}`.stock_qty,
			`tab{child_doc}`.amount
		FROM `tab{child_doc}`, `tab{parent_doc}`
		WHERE
			`tab{child_doc}`.parent = `tab{parent_doc}`.name and `tab{parent_doc}`.{date_field}
			between %s and %s and `tab{parent_doc}`.docstatus = 1
			{condition} group by `tab{child_doc}`.name
	""".format(
			parent_doc=doctype, child_doc=child_doctype, condition=condition, date_field=date_field
		),
		tuple(values),
		as_dict=1,
	)

	for data in data_set:
		sum_qty += data.get("stock_qty")
		sum_amt += data.get("amount")

	return [sum_qty, sum_amt]


def apply_pricing_rule_on_transaction(doc):
	conditions = "apply_on = 'Transaction'"

	values = {}
	conditions = get_other_conditions(conditions, values, doc)

	pricing_rules = frappe.db.sql(
		""" Select `tabPricing Rule`.* from `tabPricing Rule`
		where  {conditions} and `tabPricing Rule`.disable = 0
	""".format(
			conditions=conditions
		),
		values,
		as_dict=1,
	)

	if pricing_rules:
		pricing_rules = filter_pricing_rules_for_qty_amount(doc.total_qty, doc.total, pricing_rules)

		if not pricing_rules:
			remove_free_product(doc)

		for d in pricing_rules:
			if d.price_or_product_discount == "Price":
				if d.apply_discount_on:
					doc.set("apply_discount_on", d.apply_discount_on)

				for field in ["additional_discount_percentage", "discount_amount"]:
					pr_field = "discount_percentage" if field == "additional_discount_percentage" else field

					if not d.get(pr_field):
						continue

					if (
						d.validate_applied_rule and doc.get(field) is not None and doc.get(field) < d.get(pr_field)
					):
						frappe.msgprint(_("User has not applied rule on the invoice {0}").format(doc.name))
					else:
						if not d.coupon_code_based:
							doc.set(field, d.get(pr_field))
						elif doc.get("coupon_code"):
							# coupon code based pricing rule
							coupon_code_pricing_rule = frappe.db.get_value(
								"Coupon Code", doc.get("coupon_code"), "pricing_rule"
							)
							if coupon_code_pricing_rule == d.name:
								# if selected coupon code is linked with pricing rule
								doc.set(field, d.get(pr_field))
							else:
								# reset discount if not linked
								doc.set(field, 0)
						else:
							# if coupon code based but no coupon code selected
							doc.set(field, 0)

				doc.calculate_taxes_and_totals()
			elif d.price_or_product_discount == "Product":
				product_details = frappe._dict({"parenttype": doc.doctype, "free_product_data": []})
				get_product_discount_rule(d, product_details, doc=doc)
				apply_pricing_rule_for_free_products(doc, product_details.free_product_data)
				doc.set_missing_values()
				doc.calculate_taxes_and_totals()


def remove_free_product(doc):
	for d in doc.products:
		if d.is_free_product:
			doc.remove(d)


def get_applied_pricing_rules(pricing_rules):
	if pricing_rules:
		if pricing_rules.startswith("["):
			return json.loads(pricing_rules)
		else:
			return pricing_rules.split(",")

	return []


def get_product_discount_rule(pricing_rule, product_details, args=None, doc=None):
	free_product = pricing_rule.free_product
	if pricing_rule.same_product and pricing_rule.get("apply_on") != "Transaction":
		free_product = product_details.product_code or args.product_code

	if not free_product:
		frappe.throw(
			_("Free product not set in the pricing rule {0}").format(
				get_link_to_form("Pricing Rule", pricing_rule.name)
			)
		)

	qty = pricing_rule.free_qty or 1
	if pricing_rule.is_recursive:
		transaction_qty = (
			args.get("qty") if args else doc.total_qty
		) - pricing_rule.apply_recursion_over
		if transaction_qty:
			qty = flt(transaction_qty) * qty / pricing_rule.recurse_for
			if pricing_rule.round_free_qty:
				qty = round(qty)

	free_product_data_args = {
		"product_code": free_product,
		"qty": qty,
		"pricing_rules": pricing_rule.name,
		"rate": pricing_rule.free_product_rate or 0,
		"price_list_rate": pricing_rule.free_product_rate or 0,
		"is_free_product": 1,
	}

	product_data = frappe.get_cached_value(
		"Product", free_product, ["product_name", "description", "stock_uom"], as_dict=1
	)

	free_product_data_args.update(product_data)
	free_product_data_args["uom"] = pricing_rule.free_product_uom or product_data.stock_uom
	free_product_data_args["conversion_factor"] = get_conversion_factor(
		free_product, free_product_data_args["uom"]
	).get("conversion_factor", 1)

	if product_details.get("parenttype") == "Purchase Order":
		free_product_data_args["schedule_date"] = doc.schedule_date if doc else today()

	if product_details.get("parenttype") == "Sales Order":
		free_product_data_args["delivery_date"] = doc.delivery_date if doc else today()

	product_details.free_product_data.append(free_product_data_args)


def apply_pricing_rule_for_free_products(doc, pricing_rule_args):
	if pricing_rule_args:
		args = {(d["product_code"], d["pricing_rules"]): d for d in pricing_rule_args}

		for product in doc.products:
			if not product.is_free_product:
				continue

			free_product_data = args.get((product.product_code, product.pricing_rules))
			if free_product_data:
				free_product_data.pop("product_name")
				free_product_data.pop("description")
				product.update(free_product_data)
				args.pop((product.product_code, product.pricing_rules))

		for free_product in args.values():
			doc.append("products", free_product)


def get_pricing_rule_products(pr_doc, other_products=False) -> list:
	apply_on_data = []
	apply_on = frappe.scrub(pr_doc.get("apply_on"))

	pricing_rule_apply_on = apply_on_table.get(pr_doc.get("apply_on"))

	if pr_doc.apply_rule_on_other and other_products:
		apply_on = frappe.scrub(pr_doc.apply_rule_on_other)
		apply_on_data.append(pr_doc.get("other_" + apply_on))
	else:
		for d in pr_doc.get(pricing_rule_apply_on):
			if apply_on == "product_group":
				apply_on_data.extend(get_child_product_groups(d.get(apply_on)))
			else:
				apply_on_data.append(d.get(apply_on))

	return list(set(apply_on_data))


def validate_coupon_code(coupon_name):
	coupon = frappe.get_doc("Coupon Code", coupon_name)

	if coupon.valid_from:
		if coupon.valid_from > getdate(today()):
			frappe.throw(_("Sorry, this coupon code's validity has not started"))
	elif coupon.valid_upto:
		if coupon.valid_upto < getdate(today()):
			frappe.throw(_("Sorry, this coupon code's validity has expired"))
	elif coupon.used >= coupon.maximum_use:
		frappe.throw(_("Sorry, this coupon code is no longer valid"))


def update_coupon_code_count(coupon_name, transaction_type):
	coupon = frappe.get_doc("Coupon Code", coupon_name)
	if coupon:
		if transaction_type == "used":
			if coupon.used < coupon.maximum_use:
				coupon.used = coupon.used + 1
				coupon.save(ignore_permissions=True)
			else:
				frappe.throw(
					_("{0} Coupon used are {1}. Allowed quantity is exhausted").format(
						coupon.coupon_code, coupon.used
					)
				)
		elif transaction_type == "cancelled":
			if coupon.used > 0:
				coupon.used = coupon.used - 1
				coupon.save(ignore_permissions=True)
