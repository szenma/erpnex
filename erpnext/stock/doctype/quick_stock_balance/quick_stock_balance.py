# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document

from erpnext.stock.utils import get_stock_balance, get_stock_value_on


class QuickStockBalance(Document):
	pass


@frappe.whitelist()
def get_stock_product_details(warehouse, date, product=None, barcode=None):
	out = {}
	if barcode:
		out["product"] = frappe.db.get_value(
			"Product Barcode", filters={"barcode": barcode}, fieldname=["parent"]
		)
		if not out["product"]:
			frappe.throw(_("Invalid Barcode. There is no Product attached to this barcode."))
	else:
		out["product"] = product

	barcodes = frappe.db.get_values(
		"Product Barcode", filters={"parent": out["product"]}, fieldname=["barcode"]
	)

	out["barcodes"] = [x[0] for x in barcodes]
	out["qty"] = get_stock_balance(out["product"], warehouse, date)
	out["value"] = get_stock_value_on(warehouse, date, out["product"])
	out["image"] = frappe.db.get_value("Product", filters={"name": out["product"]}, fieldname=["image"])
	return out
