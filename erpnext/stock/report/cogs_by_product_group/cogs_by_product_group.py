# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import datetime
from collections import OrderedDict
from typing import Dict, List, Tuple, Union

import frappe
from frappe import _
from frappe.utils import date_diff

from erpnext.accounts.report.general_ledger.general_ledger import get_gl_entries

Filters = frappe._dict
Row = frappe._dict
Data = List[Row]
Columns = List[Dict[str, str]]
DateTime = Union[datetime.date, datetime.datetime]
FilteredEntries = List[Dict[str, Union[str, float, DateTime, None]]]
ProductGroupsDict = Dict[Tuple[int, int], Dict[str, Union[str, int]]]
SVDList = List[frappe._dict]


def execute(filters: Filters) -> Tuple[Columns, Data]:
	update_filters_with_account(filters)
	validate_filters(filters)
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def update_filters_with_account(filters: Filters) -> None:
	account = frappe.get_value("Company", filters.get("company"), "default_expense_account")
	filters.update(dict(account=account))


def validate_filters(filters: Filters) -> None:
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date must be before To Date"))


def get_columns() -> Columns:
	return [
		{"label": _("Product Group"), "fieldname": "product_group", "fieldtype": "Data", "width": "200"},
		{"label": _("COGS Debit"), "fieldname": "cogs_debit", "fieldtype": "Currency", "width": "200"},
	]


def get_data(filters: Filters) -> Data:
	filtered_entries = get_filtered_entries(filters)
	svd_list = get_stock_value_difference_list(filtered_entries)
	leveled_dict = get_leveled_dict()

	assign_self_values(leveled_dict, svd_list)
	assign_agg_values(leveled_dict)

	data = []
	for product in leveled_dict.products():
		i = product[1]
		if i["agg_value"] == 0:
			continue
		data.append(get_row(i["name"], i["agg_value"], i["is_group"], i["level"]))
		if i["self_value"] < i["agg_value"] and i["self_value"] > 0:
			data.append(get_row(i["name"], i["self_value"], 0, i["level"] + 1))
	return data


def get_filtered_entries(filters: Filters) -> FilteredEntries:
	gl_entries = get_gl_entries(filters, [])
	filtered_entries = []
	for entry in gl_entries:
		posting_date = entry.get("posting_date")
		from_date = filters.get("from_date")
		if date_diff(from_date, posting_date) > 0:
			continue
		filtered_entries.append(entry)
	return filtered_entries


def get_stock_value_difference_list(filtered_entries: FilteredEntries) -> SVDList:
	voucher_nos = [fe.get("voucher_no") for fe in filtered_entries]
	svd_list = frappe.get_list(
		"Stock Ledger Entry",
		fields=["product_code", "stock_value_difference"],
		filters=[("voucher_no", "in", voucher_nos), ("is_cancelled", "=", 0)],
	)
	assign_product_groups_to_svd_list(svd_list)
	return svd_list


def get_leveled_dict() -> OrderedDict:
	product_groups_dict = get_product_groups_dict()
	lr_list = sorted(product_groups_dict, key=lambda x: int(x[0]))
	leveled_dict = OrderedDict()
	current_level = 0
	nesting_r = []
	for l, r in lr_list:
		while current_level > 0 and nesting_r[-1] < l:
			nesting_r.pop()
			current_level -= 1

		leveled_dict[(l, r)] = {
			"level": current_level,
			"name": product_groups_dict[(l, r)]["name"],
			"is_group": product_groups_dict[(l, r)]["is_group"],
		}

		if int(r) - int(l) > 1:
			current_level += 1
			nesting_r.append(r)

	update_leveled_dict(leveled_dict)
	return leveled_dict


def assign_self_values(leveled_dict: OrderedDict, svd_list: SVDList) -> None:
	key_dict = {v["name"]: k for k, v in leveled_dict.products()}
	for product in svd_list:
		key = key_dict[product.get("product_group")]
		leveled_dict[key]["self_value"] += -product.get("stock_value_difference")


def assign_agg_values(leveled_dict: OrderedDict) -> None:
	keys = list(leveled_dict.keys())[::-1]
	prev_level = leveled_dict[keys[-1]]["level"]
	accu = [0]
	for k in keys[:-1]:
		curr_level = leveled_dict[k]["level"]
		if curr_level == prev_level:
			accu[-1] += leveled_dict[k]["self_value"]
			leveled_dict[k]["agg_value"] = leveled_dict[k]["self_value"]

		elif curr_level > prev_level:
			accu.append(leveled_dict[k]["self_value"])
			leveled_dict[k]["agg_value"] = accu[-1]

		elif curr_level < prev_level:
			accu[-1] += leveled_dict[k]["self_value"]
			leveled_dict[k]["agg_value"] = accu[-1]

		prev_level = curr_level

	# root node
	rk = keys[-1]
	leveled_dict[rk]["agg_value"] = sum(accu) + leveled_dict[rk]["self_value"]


def get_row(name: str, value: float, is_bold: int, indent: int) -> Row:
	product_group = name
	if is_bold:
		product_group = frappe.bold(product_group)
	return frappe._dict(product_group=product_group, cogs_debit=value, indent=indent)


def assign_product_groups_to_svd_list(svd_list: SVDList) -> None:
	ig_map = get_product_groups_map(svd_list)
	for product in svd_list:
		product.product_group = ig_map[product.get("product_code")]


def get_product_groups_map(svd_list: SVDList) -> Dict[str, str]:
	product_codes = set(i["product_code"] for i in svd_list)
	ig_list = frappe.get_list(
		"Product", fields=["product_code", "product_group"], filters=[("product_code", "in", product_codes)]
	)
	return {i["product_code"]: i["product_group"] for i in ig_list}


def get_product_groups_dict() -> ProductGroupsDict:
	product_groups_list = frappe.get_all("Product Group", fields=("name", "is_group", "lft", "rgt"))
	return {
		(i["lft"], i["rgt"]): {"name": i["name"], "is_group": i["is_group"]} for i in product_groups_list
	}


def update_leveled_dict(leveled_dict: OrderedDict) -> None:
	for k in leveled_dict:
		leveled_dict[k].update({"self_value": 0, "agg_value": 0})
