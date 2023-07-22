# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import sys

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import getdate


class VariablePathNotFound(frappe.ValidationError):
	pass


class SupplierScorecardVariable(Document):
	def validate(self):
		self.validate_path_exists()

	def validate_path_exists(self):
		if "." in self.path:
			try:
				from erpnext.buying.doctype.supplier_scorecard_period.supplier_scorecard_period import (
					import_string_path,
				)

				import_string_path(self.path)
			except AttributeError:
				frappe.throw(_("Could not find path for " + self.path), VariablePathNotFound)

		else:
			if not hasattr(sys.modules[__name__], self.path):
				frappe.throw(_("Could not find path for " + self.path), VariablePathNotFound)


def get_total_workdays(scorecard):
	"""Gets the number of days in this period"""
	delta = getdate(scorecard.end_date) - getdate(scorecard.start_date)
	return delta.days


def get_product_workdays(scorecard):
	"""Gets the number of days in this period"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)
	total_product_days = frappe.db.sql(
		"""
			SELECT
				SUM(DATEDIFF( %(end_date)s, po_product.schedule_date) * (po_product.qty))
			FROM
				`tabPurchase Order Product` po_product,
				`tabPurchase Order` po
			WHERE
				po.supplier = %(supplier)s
				AND po_product.received_qty < po_product.qty
				AND po_product.schedule_date BETWEEN %(start_date)s AND %(end_date)s
				AND po_product.parent = po.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not total_product_days:
		total_product_days = 0
	return total_product_days


def get_total_cost_of_shipments(scorecard):
	"""Gets the total cost of all shipments in the period (based on Purchase Orders)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				SUM(po_product.base_amount)
			FROM
				`tabPurchase Order Product` po_product,
				`tabPurchase Order` po
			WHERE
				po.supplier = %(supplier)s
				AND po_product.schedule_date BETWEEN %(start_date)s AND %(end_date)s
				AND po_product.docstatus = 1
				AND po_product.parent = po.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if data:
		return data
	else:
		return 0


def get_cost_of_delayed_shipments(scorecard):
	"""Gets the total cost of all delayed shipments in the period (based on Purchase Receipts - POs)"""
	return get_total_cost_of_shipments(scorecard) - get_cost_of_on_time_shipments(scorecard)


def get_cost_of_on_time_shipments(scorecard):
	"""Gets the total cost of all on_time shipments in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates

	total_delivered_on_time_costs = frappe.db.sql(
		"""
			SELECT
				SUM(pr_product.base_amount)
			FROM
				`tabPurchase Order Product` po_product,
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Order` po,
				`tabPurchase Receipt` pr
			WHERE
				po.supplier = %(supplier)s
				AND po_product.schedule_date BETWEEN %(start_date)s AND %(end_date)s
				AND po_product.schedule_date >= pr.posting_date
				AND pr_product.docstatus = 1
				AND pr_product.purchase_order_product = po_product.name
				AND po_product.parent = po.name
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if total_delivered_on_time_costs:
		return total_delivered_on_time_costs
	else:
		return 0


def get_total_days_late(scorecard):
	"""Gets the number of product days late in the period (based on Purchase Receipts vs POs)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)
	total_delivered_late_days = frappe.db.sql(
		"""
			SELECT
				SUM(DATEDIFF(pr.posting_date,po_product.schedule_date)* pr_product.qty)
			FROM
				`tabPurchase Order Product` po_product,
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Order` po,
				`tabPurchase Receipt` pr
			WHERE
				po.supplier = %(supplier)s
				AND po_product.schedule_date BETWEEN %(start_date)s AND %(end_date)s
				AND po_product.schedule_date < pr.posting_date
				AND pr_product.docstatus = 1
				AND pr_product.purchase_order_product = po_product.name
				AND po_product.parent = po.name
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]
	if not total_delivered_late_days:
		total_delivered_late_days = 0

	total_missed_late_days = frappe.db.sql(
		"""
			SELECT
				SUM(DATEDIFF( %(end_date)s, po_product.schedule_date) * (po_product.qty - po_product.received_qty))
			FROM
				`tabPurchase Order Product` po_product,
				`tabPurchase Order` po
			WHERE
				po.supplier = %(supplier)s
				AND po_product.received_qty < po_product.qty
				AND po_product.schedule_date BETWEEN %(start_date)s AND %(end_date)s
				AND po_product.parent = po.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not total_missed_late_days:
		total_missed_late_days = 0
	return total_missed_late_days + total_delivered_late_days


def get_on_time_shipments(scorecard):
	"""Gets the number of late shipments (counting each product) in the period (based on Purchase Receipts vs POs)"""

	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	total_products_delivered_on_time = frappe.db.sql(
		"""
			SELECT
				COUNT(pr_product.qty)
			FROM
				`tabPurchase Order Product` po_product,
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Order` po,
				`tabPurchase Receipt` pr
			WHERE
				po.supplier = %(supplier)s
				AND po_product.schedule_date BETWEEN %(start_date)s AND %(end_date)s
				AND po_product.schedule_date <= pr.posting_date
				AND po_product.qty = pr_product.qty
				AND pr_product.docstatus = 1
				AND pr_product.purchase_order_product = po_product.name
				AND po_product.parent = po.name
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not total_products_delivered_on_time:
		total_products_delivered_on_time = 0
	return total_products_delivered_on_time


def get_late_shipments(scorecard):
	"""Gets the number of late shipments (counting each product) in the period (based on Purchase Receipts vs POs)"""
	return get_total_shipments(scorecard) - get_on_time_shipments(scorecard)


def get_total_received(scorecard):
	"""Gets the total number of received shipments in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				COUNT(pr_product.base_amount)
			FROM
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Receipt` pr
			WHERE
				pr.supplier = %(supplier)s
				AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
				AND pr_product.docstatus = 1
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_total_received_amount(scorecard):
	"""Gets the total amount (in company currency) received in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				SUM(pr_product.received_qty * pr_product.base_rate)
			FROM
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Receipt` pr
			WHERE
				pr.supplier = %(supplier)s
				AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
				AND pr_product.docstatus = 1
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_total_received_products(scorecard):
	"""Gets the total number of received shipments in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				SUM(pr_product.received_qty)
			FROM
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Receipt` pr
			WHERE
				pr.supplier = %(supplier)s
				AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
				AND pr_product.docstatus = 1
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_total_rejected_amount(scorecard):
	"""Gets the total amount (in company currency) rejected in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				SUM(pr_product.rejected_qty * pr_product.base_rate)
			FROM
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Receipt` pr
			WHERE
				pr.supplier = %(supplier)s
				AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
				AND pr_product.docstatus = 1
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_total_rejected_products(scorecard):
	"""Gets the total number of rejected products in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				SUM(pr_product.rejected_qty)
			FROM
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Receipt` pr
			WHERE
				pr.supplier = %(supplier)s
				AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
				AND pr_product.docstatus = 1
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_total_accepted_amount(scorecard):
	"""Gets the total amount (in company currency) accepted in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				SUM(pr_product.qty * pr_product.base_rate)
			FROM
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Receipt` pr
			WHERE
				pr.supplier = %(supplier)s
				AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
				AND pr_product.docstatus = 1
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_total_accepted_products(scorecard):
	"""Gets the total number of rejected products in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				SUM(pr_product.qty)
			FROM
				`tabPurchase Receipt Product` pr_product,
				`tabPurchase Receipt` pr
			WHERE
				pr.supplier = %(supplier)s
				AND pr.posting_date BETWEEN %(start_date)s AND %(end_date)s
				AND pr_product.docstatus = 1
				AND pr_product.parent = pr.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_total_shipments(scorecard):
	"""Gets the total number of ordered shipments to arrive in the period (based on Purchase Receipts)"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				COUNT(po_product.base_amount)
			FROM
				`tabPurchase Order Product` po_product,
				`tabPurchase Order` po
			WHERE
				po.supplier = %(supplier)s
				AND po_product.schedule_date BETWEEN %(start_date)s AND %(end_date)s
				AND po_product.docstatus = 1
				AND po_product.parent = po.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_ordered_qty(scorecard):
	"""Returns the total number of ordered quantity (based on Purchase Orders)"""

	po = frappe.qb.DocType("Purchase Order")

	return (
		frappe.qb.from_(po)
		.select(Sum(po.total_qty))
		.where(
			(po.supplier == scorecard.supplier)
			& (po.docstatus == 1)
			& (po.transaction_date >= scorecard.get("start_date"))
			& (po.transaction_date <= scorecard.get("end_date"))
		)
	).run(as_list=True)[0][0] or 0


def get_rfq_total_number(scorecard):
	"""Gets the total number of RFQs sent to supplier"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				COUNT(rfq.name) as total_rfqs
			FROM
				`tabRequest for Quotation Product` rfq_product,
				`tabRequest for Quotation Supplier` rfq_sup,
				`tabRequest for Quotation` rfq
			WHERE
				rfq_sup.supplier = %(supplier)s
				AND rfq.transaction_date BETWEEN %(start_date)s AND %(end_date)s
				AND rfq_product.docstatus = 1
				AND rfq_product.parent = rfq.name
				AND rfq_sup.parent = rfq.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]

	if not data:
		data = 0
	return data


def get_rfq_total_products(scorecard):
	"""Gets the total number of RFQ products sent to supplier"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				COUNT(rfq_product.name) as total_rfqs
			FROM
				`tabRequest for Quotation Product` rfq_product,
				`tabRequest for Quotation Supplier` rfq_sup,
				`tabRequest for Quotation` rfq
			WHERE
				rfq_sup.supplier = %(supplier)s
				AND rfq.transaction_date BETWEEN %(start_date)s AND %(end_date)s
				AND rfq_product.docstatus = 1
				AND rfq_product.parent = rfq.name
				AND rfq_sup.parent = rfq.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]
	if not data:
		data = 0
	return data


def get_sq_total_number(scorecard):
	"""Gets the total number of RFQ products sent to supplier"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				COUNT(sq.name) as total_sqs
			FROM
				`tabRequest for Quotation Product` rfq_product,
				`tabSupplier Quotation Product` sq_product,
				`tabRequest for Quotation Supplier` rfq_sup,
				`tabRequest for Quotation` rfq,
				`tabSupplier Quotation` sq
			WHERE
				rfq_sup.supplier = %(supplier)s
				AND rfq.transaction_date BETWEEN %(start_date)s AND %(end_date)s
				AND sq_product.request_for_quotation_product = rfq_product.name
				AND sq_product.docstatus = 1
				AND rfq_product.docstatus = 1
				AND sq.supplier = %(supplier)s
				AND sq_product.parent = sq.name
				AND rfq_product.parent = rfq.name
				AND rfq_sup.parent = rfq.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]
	if not data:
		data = 0
	return data


def get_sq_total_products(scorecard):
	"""Gets the total number of RFQ products sent to supplier"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)

	# Look up all PO Products with delivery dates between our dates
	data = frappe.db.sql(
		"""
			SELECT
				COUNT(sq_product.name) as total_sqs
			FROM
				`tabRequest for Quotation Product` rfq_product,
				`tabSupplier Quotation Product` sq_product,
				`tabSupplier Quotation` sq,
				`tabRequest for Quotation Supplier` rfq_sup,
				`tabRequest for Quotation` rfq
			WHERE
				rfq_sup.supplier = %(supplier)s
				AND rfq.transaction_date BETWEEN %(start_date)s AND %(end_date)s
				AND sq_product.request_for_quotation_product = rfq_product.name
				AND sq_product.docstatus = 1
				AND sq.supplier = %(supplier)s
				AND sq_product.parent = sq.name
				AND rfq_product.docstatus = 1
				AND rfq_product.parent = rfq.name
				AND rfq_sup.parent = rfq.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]
	if not data:
		data = 0
	return data


def get_rfq_response_days(scorecard):
	"""Gets the total number of days it has taken a supplier to respond to rfqs in the period"""
	supplier = frappe.get_doc("Supplier", scorecard.supplier)
	total_sq_days = frappe.db.sql(
		"""
			SELECT
				SUM(DATEDIFF(sq.transaction_date, rfq.transaction_date))
			FROM
				`tabRequest for Quotation Product` rfq_product,
				`tabSupplier Quotation Product` sq_product,
				`tabSupplier Quotation` sq,
				`tabRequest for Quotation Supplier` rfq_sup,
				`tabRequest for Quotation` rfq
			WHERE
				rfq_sup.supplier = %(supplier)s
				AND rfq.transaction_date BETWEEN %(start_date)s AND %(end_date)s
				AND sq_product.request_for_quotation_product = rfq_product.name
				AND sq_product.docstatus = 1
				AND sq.supplier = %(supplier)s
				AND sq_product.parent = sq.name
				AND rfq_product.docstatus = 1
				AND rfq_product.parent = rfq.name
				AND rfq_sup.parent = rfq.name""",
		{"supplier": supplier.name, "start_date": scorecard.start_date, "end_date": scorecard.end_date},
		as_dict=0,
	)[0][0]
	if not total_sq_days:
		total_sq_days = 0

	return total_sq_days
