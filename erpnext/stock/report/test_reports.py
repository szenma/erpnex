import unittest
from typing import List, Tuple

import frappe

from erpnext.tests.utils import ReportFilters, ReportName, execute_script_report

DEFAULT_FILTERS = {
	"company": "_Test Company",
	"from_date": "2010-01-01",
	"to_date": "2030-01-01",
}


batch = frappe.db.get_value("Batch", fieldname=["name"], as_dict=True, order_by="creation desc")

REPORT_FILTER_TEST_CASES: List[Tuple[ReportName, ReportFilters]] = [
	("Stock Ledger", {"_optional": True}),
	("Stock Ledger", {"batch_no": batch}),
	("Stock Ledger", {"product_code": "_Test Product", "warehouse": "_Test Warehouse - _TC"}),
	("Stock Balance", {"_optional": True}),
	("Stock Projected Qty", {"_optional": True}),
	("Batch-Wise Balance History", {}),
	("Productwise Recommended Reorder Level", {"product_group": "All Product Groups"}),
	("COGS By Product Group", {}),
	("Stock Qty vs Serial No Count", {"warehouse": "_Test Warehouse - _TC"}),
	(
		"Stock and Account Value Comparison",
		{
			"company": "_Test Company with perpetual inventory",
			"account": "Stock In Hand - TCP1",
			"as_on_date": "2021-01-01",
		},
	),
	("Product Bundle Balance", {"date": "2022-01-01", "_optional": True}),
	(
		"Stock Analytics",
		{
			"from_date": "2021-01-01",
			"to_date": "2021-12-31",
			"value_quantity": "Quantity",
			"_optional": True,
		},
	),
	("Warehouse wise Product Balance Age and Value", {"_optional": True}),
	(
		"Product Variant Details",
		{
			"product": "_Test Variant Product",
		},
	),
	(
		"Total Stock Summary",
		{
			"group_by": "warehouse",
		},
	),
	("Batch Product Expiry Status", {}),
	("Incorrect Stock Value Report", {"company": "_Test Company with perpetual inventory"}),
	("Incorrect Serial No Valuation", {}),
	("Incorrect Balance Qty After Transaction", {}),
	("Supplier-Wise Sales Analytics", {}),
	("Product Prices", {"products": "Enabled Products only"}),
	("Delayed Product Report", {"based_on": "Sales Invoice"}),
	("Delayed Product Report", {"based_on": "Delivery Note"}),
	("Stock Ageing", {"range1": 30, "range2": 60, "range3": 90, "_optional": True}),
	("Stock Ledger Invariant Check", {"warehouse": "_Test Warehouse - _TC", "product": "_Test Product"}),
	("FIFO Queue vs Qty After Transaction Comparison", {"warehouse": "_Test Warehouse - _TC"}),
	("FIFO Queue vs Qty After Transaction Comparison", {"product_group": "All Product Groups"}),
]

OPTIONAL_FILTERS = {
	"warehouse": "_Test Warehouse - _TC",
	"product": "_Test Product",
	"product_group": "_Test Product Group",
}


class TestReports(unittest.TestCase):
	def test_execute_all_stock_reports(self):
		"""Test that all script report in stock modules are executable with supported filters"""
		for report, filter in REPORT_FILTER_TEST_CASES:
			with self.subTest(report=report):
				execute_script_report(
					report_name=report,
					module="Stock",
					filters=filter,
					default_filters=DEFAULT_FILTERS,
					optional_filters=OPTIONAL_FILTERS if filter.get("_optional") else None,
				)
