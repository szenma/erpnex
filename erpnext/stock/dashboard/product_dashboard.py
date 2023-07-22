import frappe
from frappe.model.db_query import DatabaseQuery
from frappe.utils import cint, flt


@frappe.whitelist()
def get_data(
	product_code=None, warehouse=None, product_group=None, start=0, sort_by="actual_qty", sort_order="desc"
):
	"""Return data to render the product dashboard"""
	filters = []
	if product_code:
		filters.append(["product_code", "=", product_code])
	if warehouse:
		filters.append(["warehouse", "=", warehouse])
	if product_group:
		lft, rgt = frappe.db.get_value("Product Group", product_group, ["lft", "rgt"])
		products = frappe.db.sql_list(
			"""
			select i.name from `tabProduct` i
			where exists(select name from `tabProduct Group`
				where name=i.product_group and lft >=%s and rgt<=%s)
		""",
			(lft, rgt),
		)
		filters.append(["product_code", "in", products])
	try:
		# check if user has any restrictions based on user permissions on warehouse
		if DatabaseQuery("Warehouse", user=frappe.session.user).build_match_conditions():
			filters.append(["warehouse", "in", [w.name for w in frappe.get_list("Warehouse")]])
	except frappe.PermissionError:
		# user does not have access on warehouse
		return []

	products = frappe.db.get_all(
		"Bin",
		fields=[
			"product_code",
			"warehouse",
			"projected_qty",
			"reserved_qty",
			"reserved_qty_for_production",
			"reserved_qty_for_sub_contract",
			"actual_qty",
			"valuation_rate",
		],
		or_filters={
			"projected_qty": ["!=", 0],
			"reserved_qty": ["!=", 0],
			"reserved_qty_for_production": ["!=", 0],
			"reserved_qty_for_sub_contract": ["!=", 0],
			"actual_qty": ["!=", 0],
		},
		filters=filters,
		order_by=sort_by + " " + sort_order,
		limit_start=start,
		limit_page_length=21,
	)

	precision = cint(frappe.db.get_single_value("System Settings", "float_precision"))

	for product in products:
		product.update(
			{
				"product_name": frappe.get_cached_value("Product", product.product_code, "product_name"),
				"disable_quick_entry": frappe.get_cached_value("Product", product.product_code, "has_batch_no")
				or frappe.get_cached_value("Product", product.product_code, "has_serial_no"),
				"projected_qty": flt(product.projected_qty, precision),
				"reserved_qty": flt(product.reserved_qty, precision),
				"reserved_qty_for_production": flt(product.reserved_qty_for_production, precision),
				"reserved_qty_for_sub_contract": flt(product.reserved_qty_for_sub_contract, precision),
				"actual_qty": flt(product.actual_qty, precision),
			}
		)
	return products
