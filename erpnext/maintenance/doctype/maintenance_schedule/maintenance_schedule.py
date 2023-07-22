# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _, throw
from frappe.utils import add_days, cint, cstr, date_diff, formatdate, getdate

from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.utils import get_valid_serial_nos
from erpnext.utilities.transaction_base import TransactionBase, delete_events


class MaintenanceSchedule(TransactionBase):
	@frappe.whitelist()
	def generate_schedule(self):
		if self.docstatus != 0:
			return
		self.set("schedules", [])
		count = 1
		for d in self.get("products"):
			self.validate_maintenance_detail()
			s_list = []
			s_list = self.create_schedule_list(d.start_date, d.end_date, d.no_of_visits, d.sales_person)
			for i in range(d.no_of_visits):
				child = self.append("schedules")
				child.product_code = d.product_code
				child.product_name = d.product_name
				child.scheduled_date = s_list[i].strftime("%Y-%m-%d")
				if d.serial_no:
					child.serial_no = d.serial_no
				child.idx = count
				count = count + 1
				child.sales_person = d.sales_person
				child.completion_status = "Pending"
				child.product_reference = d.name

	@frappe.whitelist()
	def validate_end_date_visits(self):
		days_in_period = {"Weekly": 7, "Monthly": 30, "Quarterly": 91, "Half Yearly": 182, "Yearly": 365}
		for product in self.products:
			if product.periodicity and product.periodicity != "Random" and product.start_date:
				if not product.end_date:
					if product.no_of_visits:
						product.end_date = add_days(
							product.start_date, product.no_of_visits * days_in_period[product.periodicity]
						)
					else:
						product.end_date = add_days(product.start_date, days_in_period[product.periodicity])

				diff = date_diff(product.end_date, product.start_date) + 1
				no_of_visits = cint(diff / days_in_period[product.periodicity])

				if not product.no_of_visits or product.no_of_visits == 0:
					product.end_date = add_days(product.start_date, days_in_period[product.periodicity])
					diff = date_diff(product.end_date, product.start_date) + 1
					product.no_of_visits = cint(diff / days_in_period[product.periodicity])

				elif product.no_of_visits > no_of_visits:
					product.end_date = add_days(
						product.start_date, product.no_of_visits * days_in_period[product.periodicity]
					)

				elif product.no_of_visits < no_of_visits:
					product.end_date = add_days(
						product.start_date, product.no_of_visits * days_in_period[product.periodicity]
					)

	def on_submit(self):
		if not self.get("schedules"):
			throw(_("Please click on 'Generate Schedule' to get schedule"))
		self.check_serial_no_added()
		self.validate_schedule()

		email_map = {}
		for d in self.get("products"):
			if d.serial_no:
				serial_nos = get_valid_serial_nos(d.serial_no)
				self.validate_serial_no(d.product_code, serial_nos, d.start_date)
				self.update_amc_date(serial_nos, d.end_date)

			no_email_sp = []
			if d.sales_person not in email_map:
				sp = frappe.get_doc("Sales Person", d.sales_person)
				try:
					email_map[d.sales_person] = sp.get_email_id()
				except frappe.ValidationError:
					no_email_sp.append(d.sales_person)

			if no_email_sp:
				frappe.msgprint(
					_(
						"Setting Events to {0}, since the Employee attached to the below Sales Persons does not have a User ID{1}"
					).format(self.owner, "<br>" + "<br>".join(no_email_sp))
				)

			scheduled_date = frappe.db.sql(
				"""select scheduled_date from
				`tabMaintenance Schedule Detail` where sales_person=%s and product_code=%s and
				parent=%s""",
				(d.sales_person, d.product_code, self.name),
				as_dict=1,
			)

			for key in scheduled_date:
				description = frappe._("Reference: {0}, Product Code: {1} and Customer: {2}").format(
					self.name, d.product_code, self.customer
				)
				event = frappe.get_doc(
					{
						"doctype": "Event",
						"owner": email_map.get(d.sales_person, self.owner),
						"subject": description,
						"description": description,
						"starts_on": cstr(key["scheduled_date"]) + " 10:00:00",
						"event_type": "Private",
					}
				)
				event.add_participant(self.doctype, self.name)
				event.insert(ignore_permissions=1)

		self.db_set("status", "Submitted")

	def create_schedule_list(self, start_date, end_date, no_of_visit, sales_person):
		schedule_list = []
		start_date_copy = start_date
		date_diff = (getdate(end_date) - getdate(start_date)).days
		add_by = date_diff / no_of_visit

		for visit in range(cint(no_of_visit)):
			if getdate(start_date_copy) < getdate(end_date):
				start_date_copy = add_days(start_date_copy, add_by)
				if len(schedule_list) < no_of_visit:
					schedule_date = self.validate_schedule_date_for_holiday_list(
						getdate(start_date_copy), sales_person
					)
					if schedule_date > getdate(end_date):
						schedule_date = getdate(end_date)
					schedule_list.append(schedule_date)

		return schedule_list

	def validate_schedule_date_for_holiday_list(self, schedule_date, sales_person):
		validated = False

		employee = frappe.db.get_value("Sales Person", sales_person, "employee")
		if employee:
			holiday_list = get_holiday_list_for_employee(employee)
		else:
			holiday_list = frappe.get_cached_value("Company", self.company, "default_holiday_list")

		holidays = frappe.db.sql_list(
			"""select holiday_date from `tabHoliday` where parent=%s""", holiday_list
		)

		if not validated and holidays:

			# max iterations = len(holidays)
			for i in range(len(holidays)):
				if schedule_date in holidays:
					schedule_date = add_days(schedule_date, -1)
				else:
					validated = True
					break

		return schedule_date

	def validate_dates_with_periodicity(self):
		for d in self.get("products"):
			if d.start_date and d.end_date and d.periodicity and d.periodicity != "Random":
				date_diff = (getdate(d.end_date) - getdate(d.start_date)).days + 1
				days_in_period = {
					"Weekly": 7,
					"Monthly": 30,
					"Quarterly": 90,
					"Half Yearly": 180,
					"Yearly": 365,
				}

				if date_diff < days_in_period[d.periodicity]:
					throw(
						_(
							"Row {0}: To set {1} periodicity, difference between from and to date must be greater than or equal to {2}"
						).format(d.idx, d.periodicity, days_in_period[d.periodicity])
					)

	def validate_maintenance_detail(self):
		if not self.get("products"):
			throw(_("Please enter Maintaince Details first"))

		for d in self.get("products"):
			if not d.product_code:
				throw(_("Please select product code"))
			elif not d.start_date or not d.end_date:
				throw(_("Please select Start Date and End Date for Product {0}").format(d.product_code))
			elif not d.no_of_visits:
				throw(_("Please mention no of visits required"))
			elif not d.sales_person:
				throw(_("Please select a Sales Person for product: {0}").format(d.product_name))

			if getdate(d.start_date) >= getdate(d.end_date):
				throw(_("Start date should be less than end date for Product {0}").format(d.product_code))

	def validate_sales_order(self):
		for d in self.get("products"):
			if d.sales_order:
				chk = frappe.db.sql(
					"""select ms.name from `tabMaintenance Schedule` ms,
					`tabMaintenance Schedule Product` msi where msi.parent=ms.name and
					msi.sales_order=%s and ms.docstatus=1""",
					d.sales_order,
				)
				if chk:
					throw(_("Maintenance Schedule {0} exists against {1}").format(chk[0][0], d.sales_order))

	def validate_products_table_change(self):
		doc_before_save = self.get_doc_before_save()
		if not doc_before_save:
			return
		for prev_product, product in zip(doc_before_save.products, self.products):
			fields = [
				"product_code",
				"start_date",
				"end_date",
				"periodicity",
				"sales_person",
				"no_of_visits",
				"serial_no",
			]
			for field in fields:
				b_doc = prev_product.as_dict()
				doc = product.as_dict()
				if cstr(b_doc[field]) != cstr(doc[field]):
					return True

	def validate_no_of_visits(self):
		return len(self.schedules) != sum(d.no_of_visits for d in self.products)

	def validate(self):
		self.validate_end_date_visits()
		self.validate_maintenance_detail()
		self.validate_dates_with_periodicity()
		self.validate_sales_order()
		if not self.schedules or self.validate_products_table_change() or self.validate_no_of_visits():
			self.generate_schedule()

	def on_update(self):
		self.db_set("status", "Draft")

	def update_amc_date(self, serial_nos, amc_expiry_date=None):
		for serial_no in serial_nos:
			serial_no_doc = frappe.get_doc("Serial No", serial_no)
			serial_no_doc.amc_expiry_date = amc_expiry_date
			serial_no_doc.save()

	def validate_serial_no(self, product_code, serial_nos, amc_start_date):
		for serial_no in serial_nos:
			sr_details = frappe.db.get_value(
				"Serial No",
				serial_no,
				["warranty_expiry_date", "amc_expiry_date", "warehouse", "delivery_date", "product_code"],
				as_dict=1,
			)

			if not sr_details:
				frappe.throw(_("Serial No {0} not found").format(serial_no))

			if sr_details.get("product_code") != product_code:
				frappe.throw(
					_("Serial No {0} does not belong to Product {1}").format(
						frappe.bold(serial_no), frappe.bold(product_code)
					),
					title=_("Invalid"),
				)

			if sr_details.warranty_expiry_date and getdate(sr_details.warranty_expiry_date) >= getdate(
				amc_start_date
			):
				throw(
					_("Serial No {0} is under warranty upto {1}").format(
						serial_no, sr_details.warranty_expiry_date
					)
				)

			if sr_details.amc_expiry_date and getdate(sr_details.amc_expiry_date) >= getdate(
				amc_start_date
			):
				throw(
					_("Serial No {0} is under maintenance contract upto {1}").format(
						serial_no, sr_details.amc_expiry_date
					)
				)

			if (
				not sr_details.warehouse
				and sr_details.delivery_date
				and getdate(sr_details.delivery_date) >= getdate(amc_start_date)
			):
				throw(
					_("Maintenance start date can not be before delivery date for Serial No {0}").format(
						serial_no
					)
				)

	def validate_schedule(self):
		product_lst1 = []
		product_lst2 = []
		for d in self.get("products"):
			if d.product_code not in product_lst1:
				product_lst1.append(d.product_code)

		for m in self.get("schedules"):
			if m.product_code not in product_lst2:
				product_lst2.append(m.product_code)

		if len(product_lst1) != len(product_lst2):
			throw(
				_(
					"Maintenance Schedule is not generated for all the products. Please click on 'Generate Schedule'"
				)
			)
		else:
			for x in product_lst1:
				if x not in product_lst2:
					throw(_("Please click on 'Generate Schedule'"))

	def check_serial_no_added(self):
		serial_present = []
		for d in self.get("products"):
			if d.serial_no:
				serial_present.append(d.product_code)

		for m in self.get("schedules"):
			if serial_present:
				if m.product_code in serial_present and not m.serial_no:
					throw(
						_("Please click on 'Generate Schedule' to fetch Serial No added for Product {0}").format(
							m.product_code
						)
					)

	def on_cancel(self):
		for d in self.get("products"):
			if d.serial_no:
				serial_nos = get_valid_serial_nos(d.serial_no)
				self.update_amc_date(serial_nos)
		self.db_set("status", "Cancelled")
		delete_events(self.doctype, self.name)

	def on_trash(self):
		delete_events(self.doctype, self.name)

	@frappe.whitelist()
	def get_pending_data(self, data_type, s_date=None, product_name=None):
		if data_type == "date":
			dates = ""
			for schedule in self.schedules:
				if schedule.product_name == product_name and schedule.completion_status == "Pending":
					dates = dates + "\n" + formatdate(schedule.scheduled_date, "dd-MM-yyyy")
			return dates
		elif data_type == "products":
			products = ""
			for product in self.products:
				for schedule in self.schedules:
					if product.product_name == schedule.product_name and schedule.completion_status == "Pending":
						products = products + "\n" + product.product_name
						break
			return products
		elif data_type == "id":
			for schedule in self.schedules:
				if schedule.product_name == product_name and s_date == formatdate(
					schedule.scheduled_date, "dd-mm-yyyy"
				):
					return schedule.name


@frappe.whitelist()
def get_serial_nos_from_schedule(product_code, schedule=None):
	serial_nos = []
	if schedule:
		serial_nos = frappe.db.get_value(
			"Maintenance Schedule Product", {"parent": schedule, "product_code": product_code}, "serial_no"
		)

	if serial_nos:
		serial_nos = get_serial_nos(serial_nos)

	return serial_nos


@frappe.whitelist()
def make_maintenance_visit(source_name, target_doc=None, product_name=None, s_id=None):
	from frappe.model.mapper import get_mapped_doc

	def update_status_and_detail(source, target, parent):
		target.maintenance_type = "Scheduled"
		target.maintenance_schedule_detail = s_id

	def update_serial(source, target, parent):
		serial_nos = get_serial_nos(target.serial_no)
		if len(serial_nos) == 1:
			target.serial_no = serial_nos[0]
		else:
			target.serial_no = ""

	doclist = get_mapped_doc(
		"Maintenance Schedule",
		source_name,
		{
			"Maintenance Schedule": {
				"doctype": "Maintenance Visit",
				"field_map": {"name": "maintenance_schedule"},
				"validation": {"docstatus": ["=", 1]},
				"postprocess": update_status_and_detail,
			},
			"Maintenance Schedule Product": {
				"doctype": "Maintenance Visit Purpose",
				"condition": lambda doc: doc.product_name == product_name if product_name else True,
				"field_map": {"sales_person": "service_person"},
				"postprocess": update_serial,
			},
		},
		target_doc,
	)

	return doclist