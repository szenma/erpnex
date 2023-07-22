# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import json
from math import ceil

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, nowdate

import erpnext


def reorder_product():
	"""Reorder product if stock reaches reorder level"""
	# if initial setup not completed, return
	if not (frappe.db.a_row_exists("Company") and frappe.db.a_row_exists("Fiscal Year")):
		return

	if cint(frappe.db.get_value("Stock Settings", None, "auto_indent")):
		return _reorder_product()


def _reorder_product():
	material_requests = {"Purchase": {}, "Transfer": {}, "Material Issue": {}, "Manufacture": {}}
	warehouse_company = frappe._dict(
		frappe.db.sql(
			"""select name, company from `tabWarehouse`
		where disabled=0"""
		)
	)
	default_company = (
		erpnext.get_default_company() or frappe.db.sql("""select name from tabCompany limit 1""")[0][0]
	)

	products_to_consider = frappe.db.sql_list(
		"""select name from `tabProduct` product
		where is_stock_product=1 and has_variants=0
			and disabled=0
			and (end_of_life is null or end_of_life='0000-00-00' or end_of_life > %(today)s)
			and (exists (select name from `tabProduct Reorder` ir where ir.parent=product.name)
				or (variant_of is not null and variant_of != ''
				and exists (select name from `tabProduct Reorder` ir where ir.parent=product.variant_of))
			)""",
		{"today": nowdate()},
	)

	if not products_to_consider:
		return

	product_warehouse_projected_qty = get_product_warehouse_projected_qty(products_to_consider)

	def add_to_material_request(
		product_code, warehouse, reorder_level, reorder_qty, material_request_type, warehouse_group=None
	):
		if warehouse not in warehouse_company:
			# a disabled warehouse
			return

		reorder_level = flt(reorder_level)
		reorder_qty = flt(reorder_qty)

		# projected_qty will be 0 if Bin does not exist
		if warehouse_group:
			projected_qty = flt(product_warehouse_projected_qty.get(product_code, {}).get(warehouse_group))
		else:
			projected_qty = flt(product_warehouse_projected_qty.get(product_code, {}).get(warehouse))

		if (reorder_level or reorder_qty) and projected_qty < reorder_level:
			deficiency = reorder_level - projected_qty
			if deficiency > reorder_qty:
				reorder_qty = deficiency

			company = warehouse_company.get(warehouse) or default_company

			material_requests[material_request_type].setdefault(company, []).append(
				{"product_code": product_code, "warehouse": warehouse, "reorder_qty": reorder_qty}
			)

	for product_code in products_to_consider:
		product = frappe.get_doc("Product", product_code)

		if product.variant_of and not product.get("reorder_levels"):
			product.update_template_tables()

		if product.get("reorder_levels"):
			for d in product.get("reorder_levels"):
				add_to_material_request(
					product_code,
					d.warehouse,
					d.warehouse_reorder_level,
					d.warehouse_reorder_qty,
					d.material_request_type,
					warehouse_group=d.warehouse_group,
				)

	if material_requests:
		return create_material_request(material_requests)


def get_product_warehouse_projected_qty(products_to_consider):
	product_warehouse_projected_qty = {}

	for product_code, warehouse, projected_qty in frappe.db.sql(
		"""select product_code, warehouse, projected_qty
		from tabBin where product_code in ({0})
			and (warehouse != '' and warehouse is not null)""".format(
			", ".join(["%s"] * len(products_to_consider))
		),
		products_to_consider,
	):

		if product_code not in product_warehouse_projected_qty:
			product_warehouse_projected_qty.setdefault(product_code, {})

		if warehouse not in product_warehouse_projected_qty.get(product_code):
			product_warehouse_projected_qty[product_code][warehouse] = flt(projected_qty)

		warehouse_doc = frappe.get_doc("Warehouse", warehouse)

		while warehouse_doc.parent_warehouse:
			if not product_warehouse_projected_qty.get(product_code, {}).get(warehouse_doc.parent_warehouse):
				product_warehouse_projected_qty.setdefault(product_code, {})[warehouse_doc.parent_warehouse] = flt(
					projected_qty
				)
			else:
				product_warehouse_projected_qty[product_code][warehouse_doc.parent_warehouse] += flt(projected_qty)
			warehouse_doc = frappe.get_doc("Warehouse", warehouse_doc.parent_warehouse)

	return product_warehouse_projected_qty


def create_material_request(material_requests):
	"""Create indent on reaching reorder level"""
	mr_list = []
	exceptions_list = []

	def _log_exception(mr):
		if frappe.local.message_log:
			exceptions_list.extend(frappe.local.message_log)
			frappe.local.message_log = []
		else:
			exceptions_list.append(frappe.get_traceback())

		mr.log_error("Unable to create material request")

	for request_type in material_requests:
		for company in material_requests[request_type]:
			try:
				products = material_requests[request_type][company]
				if not products:
					continue

				mr = frappe.new_doc("Material Request")
				mr.update(
					{
						"company": company,
						"transaction_date": nowdate(),
						"material_request_type": "Material Transfer" if request_type == "Transfer" else request_type,
					}
				)

				for d in products:
					d = frappe._dict(d)
					product = frappe.get_doc("Product", d.product_code)
					uom = product.stock_uom
					conversion_factor = 1.0

					if request_type == "Purchase":
						uom = product.purchase_uom or product.stock_uom
						if uom != product.stock_uom:
							conversion_factor = (
								frappe.db.get_value(
									"UOM Conversion Detail", {"parent": product.name, "uom": uom}, "conversion_factor"
								)
								or 1.0
							)

					must_be_whole_number = frappe.db.get_value("UOM", uom, "must_be_whole_number", cache=True)
					qty = d.reorder_qty / conversion_factor
					if must_be_whole_number:
						qty = ceil(qty)

					mr.append(
						"products",
						{
							"doctype": "Material Request Product",
							"product_code": d.product_code,
							"schedule_date": add_days(nowdate(), cint(product.lead_time_days)),
							"qty": qty,
							"uom": uom,
							"stock_uom": product.stock_uom,
							"warehouse": d.warehouse,
							"product_name": product.product_name,
							"description": product.description,
							"product_group": product.product_group,
							"brand": product.brand,
						},
					)

				schedule_dates = [d.schedule_date for d in mr.products]
				mr.schedule_date = max(schedule_dates or [nowdate()])
				mr.flags.ignore_mandatory = True
				mr.insert()
				mr.submit()
				mr_list.append(mr)

			except Exception:
				_log_exception(mr)

	if mr_list:
		if getattr(frappe.local, "reorder_email_notify", None) is None:
			frappe.local.reorder_email_notify = cint(
				frappe.db.get_value("Stock Settings", None, "reorder_email_notify")
			)

		if frappe.local.reorder_email_notify:
			send_email_notification(mr_list)

	if exceptions_list:
		notify_errors(exceptions_list)

	return mr_list


def send_email_notification(mr_list):
	"""Notify user about auto creation of indent"""

	email_list = frappe.db.sql_list(
		"""select distinct r.parent
		from `tabHas Role` r, tabUser p
		where p.name = r.parent and p.enabled = 1 and p.docstatus < 2
		and r.role in ('Purchase Manager','Stock Manager')
		and p.name not in ('Administrator', 'All', 'Guest')"""
	)

	msg = frappe.render_template("templates/emails/reorder_product.html", {"mr_list": mr_list})

	frappe.sendmail(recipients=email_list, subject=_("Auto Material Requests Generated"), message=msg)


def notify_errors(exceptions_list):
	subject = _("[Important] [ERPNext] Auto Reorder Errors")
	content = (
		_("Dear System Manager,")
		+ "<br>"
		+ _(
			"An error occured for certain Products while creating Material Requests based on Re-order level. Please rectify these issues :"
		)
		+ "<br>"
	)

	for exception in exceptions_list:
		try:
			exception = json.loads(exception)
			error_message = """<div class='small text-muted'>{0}</div><br>""".format(
				_(exception.get("message"))
			)
			content += error_message
		except Exception:
			pass

	content += _("Regards,") + "<br>" + _("Administrator")

	from frappe.email import sendmail_to_system_managers

	sendmail_to_system_managers(subject, content)
