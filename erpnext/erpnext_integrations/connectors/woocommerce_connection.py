import base64
import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.utils import cstr


def verify_request():
	woocommerce_settings = frappe.get_doc("Woocommerce Settings")
	sig = base64.b64encode(
		hmac.new(
			woocommerce_settings.secret.encode("utf8"), frappe.request.data, hashlib.sha256
		).digest()
	)

	if (
		frappe.request.data
		and not sig == frappe.get_request_header("X-Wc-Webhook-Signature", "").encode()
	):
		frappe.throw(_("Unverified Webhook Data"))
	frappe.set_user(woocommerce_settings.creation_user)


@frappe.whitelist(allow_guest=True)
def order(*args, **kwargs):
	try:
		_order(*args, **kwargs)
	except Exception:
		error_message = (
			frappe.get_traceback() + "\n\n Request Data: \n" + json.loads(frappe.request.data).__str__()
		)
		frappe.log_error("WooCommerce Error", error_message)
		raise


def _order(*args, **kwargs):
	woocommerce_settings = frappe.get_doc("Woocommerce Settings")
	if frappe.flags.woocomm_test_order_data:
		order = frappe.flags.woocomm_test_order_data
		event = "created"

	elif frappe.request and frappe.request.data:
		verify_request()
		try:
			order = json.loads(frappe.request.data)
		except ValueError:
			# woocommerce returns 'webhook_id=value' for the first request which is not JSON
			order = frappe.request.data
		event = frappe.get_request_header("X-Wc-Webhook-Event")

	else:
		return "success"

	if event == "created":
		sys_lang = frappe.get_single("System Settings").language or "en"
		raw_billing_data = order.get("billing")
		raw_shipping_data = order.get("shipping")
		customer_name = raw_billing_data.get("first_name") + " " + raw_billing_data.get("last_name")
		link_customer_and_address(raw_billing_data, raw_shipping_data, customer_name)
		link_products(order.get("line_products"), woocommerce_settings, sys_lang)
		create_sales_order(order, woocommerce_settings, customer_name, sys_lang)


def link_customer_and_address(raw_billing_data, raw_shipping_data, customer_name):
	customer_woo_com_email = raw_billing_data.get("email")
	customer_exists = frappe.get_value("Customer", {"woocommerce_email": customer_woo_com_email})
	if not customer_exists:
		# Create Customer
		customer = frappe.new_doc("Customer")
	else:
		# Edit Customer
		customer = frappe.get_doc("Customer", {"woocommerce_email": customer_woo_com_email})
		old_name = customer.customer_name

	customer.customer_name = customer_name
	customer.woocommerce_email = customer_woo_com_email
	customer.flags.ignore_mandatory = True
	customer.save()

	if customer_exists:
		frappe.rename_doc("Customer", old_name, customer_name)
		for address_type in (
			"Billing",
			"Shipping",
		):
			try:
				address = frappe.get_doc(
					"Address", {"woocommerce_email": customer_woo_com_email, "address_type": address_type}
				)
				rename_address(address, customer)
			except (
				frappe.DoesNotExistError,
				frappe.DuplicateEntryError,
				frappe.ValidationError,
			):
				pass
	else:
		create_address(raw_billing_data, customer, "Billing")
		create_address(raw_shipping_data, customer, "Shipping")
		create_contact(raw_billing_data, customer)


def create_contact(data, customer):
	email = data.get("email", None)
	phone = data.get("phone", None)

	if not email and not phone:
		return

	contact = frappe.new_doc("Contact")
	contact.first_name = data.get("first_name")
	contact.last_name = data.get("last_name")
	contact.is_primary_contact = 1
	contact.is_billing_contact = 1

	if phone:
		contact.add_phone(phone, is_primary_mobile_no=1, is_primary_phone=1)

	if email:
		contact.add_email(email, is_primary=1)

	contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})

	contact.flags.ignore_mandatory = True
	contact.save()


def create_address(raw_data, customer, address_type):
	address = frappe.new_doc("Address")

	address.address_line1 = raw_data.get("address_1", "Not Provided")
	address.address_line2 = raw_data.get("address_2", "Not Provided")
	address.city = raw_data.get("city", "Not Provided")
	address.woocommerce_email = customer.woocommerce_email
	address.address_type = address_type
	address.country = frappe.get_value("Country", {"code": raw_data.get("country", "IN").lower()})
	address.state = raw_data.get("state")
	address.pincode = raw_data.get("postcode")
	address.phone = raw_data.get("phone")
	address.email_id = customer.woocommerce_email
	address.append("links", {"link_doctype": "Customer", "link_name": customer.name})

	address.flags.ignore_mandatory = True
	address.save()


def rename_address(address, customer):
	old_address_title = address.name
	new_address_title = customer.name + "-" + address.address_type
	address.address_title = customer.customer_name
	address.save()

	frappe.rename_doc("Address", old_address_title, new_address_title)


def link_products(products_list, woocommerce_settings, sys_lang):
	for product_data in products_list:
		product_woo_com_id = cstr(product_data.get("product_id"))

		if not frappe.db.get_value("Product", {"woocommerce_id": product_woo_com_id}, "name"):
			# Create Product
			product = frappe.new_doc("Product")
			product.product_code = _("woocommerce - {0}", sys_lang).format(product_woo_com_id)
			product.stock_uom = woocommerce_settings.uom or _("Nos", sys_lang)
			product.product_group = _("WooCommerce Products", sys_lang)

			product.product_name = product_data.get("name")
			product.woocommerce_id = product_woo_com_id
			product.flags.ignore_mandatory = True
			product.save()


def create_sales_order(order, woocommerce_settings, customer_name, sys_lang):
	new_sales_order = frappe.new_doc("Sales Order")
	new_sales_order.customer = customer_name

	new_sales_order.po_no = new_sales_order.woocommerce_id = order.get("id")
	new_sales_order.naming_series = woocommerce_settings.sales_order_series or "SO-WOO-"

	created_date = order.get("date_created").split("T")
	new_sales_order.transaction_date = created_date[0]
	delivery_after = woocommerce_settings.delivery_after_days or 7
	new_sales_order.delivery_date = frappe.utils.add_days(created_date[0], delivery_after)

	new_sales_order.company = woocommerce_settings.company

	set_products_in_sales_order(new_sales_order, woocommerce_settings, order, sys_lang)
	new_sales_order.flags.ignore_mandatory = True
	new_sales_order.insert()
	new_sales_order.submit()

	frappe.db.commit()


def set_products_in_sales_order(new_sales_order, woocommerce_settings, order, sys_lang):
	company_abbr = frappe.db.get_value("Company", woocommerce_settings.company, "abbr")

	default_warehouse = _("Stores - {0}", sys_lang).format(company_abbr)
	if not frappe.db.exists("Warehouse", default_warehouse) and not woocommerce_settings.warehouse:
		frappe.throw(_("Please set Warehouse in Woocommerce Settings"))

	for product in order.get("line_products"):
		woocomm_product_id = product.get("product_id")
		found_product = frappe.get_doc("Product", {"woocommerce_id": cstr(woocomm_product_id)})

		ordered_products_tax = product.get("total_tax")

		new_sales_order.append(
			"products",
			{
				"product_code": found_product.name,
				"product_name": found_product.product_name,
				"description": found_product.product_name,
				"delivery_date": new_sales_order.delivery_date,
				"uom": woocommerce_settings.uom or _("Nos", sys_lang),
				"qty": product.get("quantity"),
				"rate": product.get("price"),
				"warehouse": woocommerce_settings.warehouse or default_warehouse,
			},
		)

		add_tax_details(
			new_sales_order, ordered_products_tax, "Ordered Product tax", woocommerce_settings.tax_account
		)

	# shipping_details = order.get("shipping_lines") # used for detailed order

	add_tax_details(
		new_sales_order, order.get("shipping_tax"), "Shipping Tax", woocommerce_settings.f_n_f_account
	)
	add_tax_details(
		new_sales_order,
		order.get("shipping_total"),
		"Shipping Total",
		woocommerce_settings.f_n_f_account,
	)


def add_tax_details(sales_order, price, desc, tax_account_head):
	sales_order.append(
		"taxes",
		{
			"charge_type": "Actual",
			"account_head": tax_account_head,
			"tax_amount": price,
			"description": desc,
		},
	)
