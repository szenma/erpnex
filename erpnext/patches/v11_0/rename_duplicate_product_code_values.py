import frappe


def execute():
	products = []
	products = frappe.db.sql(
		"""select product_code from `tabProduct` group by product_code having count(*) > 1""", as_dict=True
	)
	if products:
		for product in products:
			frappe.db.sql("""update `tabProduct` set product_code=name where product_code = %s""", (product.product_code))
