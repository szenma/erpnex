# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import unittest

import frappe
from frappe.utils import format_date
from frappe.utils.data import add_days, formatdate, today

from erpnext.maintenance.doctype.maintenance_schedule.maintenance_schedule import (
	get_serial_nos_from_schedule,
	make_maintenance_visit,
)
from erpnext.stock.doctype.product.test_product import create_product
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_serialized_product

# test_records = frappe.get_test_records('Maintenance Schedule')


class TestMaintenanceSchedule(unittest.TestCase):
	def test_events_should_be_created_and_deleted(self):
		ms = make_maintenance_schedule()
		ms.generate_schedule()
		ms.submit()

		all_events = get_events(ms)
		self.assertTrue(len(all_events) > 0)

		ms.cancel()
		events_after_cancel = get_events(ms)
		self.assertTrue(len(events_after_cancel) == 0)

	def test_make_schedule(self):
		ms = make_maintenance_schedule()
		ms.save()
		i = ms.products[0]
		expected_dates = []
		expected_end_date = add_days(i.start_date, i.no_of_visits * 7)
		self.assertEqual(i.end_date, expected_end_date)

		i.no_of_visits = 2
		ms.save()
		expected_end_date = add_days(i.start_date, i.no_of_visits * 7)
		self.assertEqual(i.end_date, expected_end_date)

		products = ms.get_pending_data(data_type="products")
		products = products.split("\n")
		products.pop(0)
		expected_products = ["_Test Product"]
		self.assertTrue(products, expected_products)

		# "dates" contains all generated schedule dates
		dates = ms.get_pending_data(data_type="date", product_name=i.product_name)
		dates = dates.split("\n")
		dates.pop(0)
		expected_dates.append(formatdate(add_days(i.start_date, 7), "dd-MM-yyyy"))
		expected_dates.append(formatdate(add_days(i.start_date, 14), "dd-MM-yyyy"))

		# test for generated schedule dates
		self.assertEqual(dates, expected_dates)

		ms.submit()
		s_id = ms.get_pending_data(data_type="id", product_name=i.product_name, s_date=expected_dates[1])

		# Check if product is mapped in visit.
		test_map_visit = make_maintenance_visit(source_name=ms.name, product_name="_Test Product", s_id=s_id)
		self.assertEqual(len(test_map_visit.purposes), 1)
		self.assertEqual(test_map_visit.purposes[0].product_name, "_Test Product")

		visit = frappe.new_doc("Maintenance Visit")
		visit = test_map_visit
		visit.maintenance_schedule = ms.name
		visit.maintenance_schedule_detail = s_id
		visit.completion_status = "Partially Completed"
		visit.set(
			"purposes",
			[
				{
					"product_code": i.product_code,
					"description": "test",
					"work_done": "test",
					"service_person": "Sales Team",
				}
			],
		)
		visit.save()
		visit.submit()
		ms = frappe.get_doc("Maintenance Schedule", ms.name)

		# checks if visit status is back updated in schedule
		self.assertTrue(ms.schedules[1].completion_status, "Partially Completed")
		self.assertEqual(format_date(visit.mntc_date), format_date(ms.schedules[1].actual_date))

		# checks if visit status is updated on cancel
		visit.cancel()
		ms.reload()
		self.assertTrue(ms.schedules[1].completion_status, "Pending")
		self.assertEqual(ms.schedules[1].actual_date, None)

	def test_serial_no_filters(self):
		# Without serial no. set in schedule -> returns None
		product_code = "_Test Serial Product"
		make_serial_product_with_serial(product_code)
		ms = make_maintenance_schedule(product_code=product_code)
		ms.submit()

		s_product = ms.schedules[0]
		mv = make_maintenance_visit(source_name=ms.name, product_name=product_code, s_id=s_product.name)
		mvi = mv.purposes[0]
		serial_nos = get_serial_nos_from_schedule(mvi.product_name, ms.name)
		self.assertEqual(serial_nos, None)

		# With serial no. set in schedule -> returns serial nos.
		make_serial_product_with_serial(product_code)
		ms = make_maintenance_schedule(product_code=product_code, serial_no="TEST001, TEST002")
		ms.submit()

		s_product = ms.schedules[0]
		mv = make_maintenance_visit(source_name=ms.name, product_name=product_code, s_id=s_product.name)
		mvi = mv.purposes[0]
		serial_nos = get_serial_nos_from_schedule(mvi.product_name, ms.name)
		self.assertEqual(serial_nos, ["TEST001", "TEST002"])

		frappe.db.rollback()

	def test_schedule_with_serials(self):
		# Checks whether serials are automatically updated when changing in products table.
		# Also checks if other fields trigger generate schdeule if changed in products table.
		product_code = "_Test Serial Product"
		make_serial_product_with_serial(product_code)
		ms = make_maintenance_schedule(product_code=product_code, serial_no="TEST001, TEST002")
		ms.save()

		# Before Save
		self.assertEqual(ms.schedules[0].serial_no, "TEST001, TEST002")
		self.assertEqual(ms.schedules[0].sales_person, "Sales Team")
		self.assertEqual(len(ms.schedules), 4)
		self.assertFalse(ms.validate_products_table_change())
		# After Save
		ms.products[0].serial_no = "TEST001"
		ms.products[0].sales_person = "_Test Sales Person"
		ms.products[0].no_of_visits = 2
		self.assertTrue(ms.validate_products_table_change())
		ms.save()
		self.assertEqual(ms.schedules[0].serial_no, "TEST001")
		self.assertEqual(ms.schedules[0].sales_person, "_Test Sales Person")
		self.assertEqual(len(ms.schedules), 2)
		# When user manually deleted a row from schedules table.
		ms.schedules.pop()
		self.assertEqual(len(ms.schedules), 1)
		ms.save()
		self.assertEqual(len(ms.schedules), 2)

		frappe.db.rollback()


def make_serial_product_with_serial(product_code):
	serial_product_doc = create_product(product_code, is_stock_product=1)
	if not serial_product_doc.has_serial_no or not serial_product_doc.serial_no_series:
		serial_product_doc.has_serial_no = 1
		serial_product_doc.serial_no_series = "TEST.###"
		serial_product_doc.save(ignore_permissions=True)
	active_serials = frappe.db.get_all("Serial No", {"status": "Active", "product_code": product_code})
	if len(active_serials) < 2:
		make_serialized_product(product_code=product_code)


def get_events(ms):
	return frappe.get_all(
		"Event Participants",
		filters={"reference_doctype": ms.doctype, "reference_docname": ms.name, "parenttype": "Event"},
	)


def make_maintenance_schedule(**args):
	ms = frappe.new_doc("Maintenance Schedule")
	ms.company = "_Test Company"
	ms.customer = "_Test Customer"
	ms.transaction_date = today()

	ms.append(
		"products",
		{
			"product_code": args.get("product_code") or "_Test Product",
			"start_date": today(),
			"periodicity": "Weekly",
			"no_of_visits": 4,
			"serial_no": args.get("serial_no"),
			"sales_person": "Sales Team",
		},
	)
	ms.insert(ignore_permissions=True)

	return ms
