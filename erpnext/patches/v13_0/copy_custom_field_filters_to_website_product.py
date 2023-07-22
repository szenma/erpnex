import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	"Add Field Filters, that are not standard fields in Website Product, as Custom Fields."

	def move_table_multiselect_data(docfield):
		"Copy child table data (Table Multiselect) from Product to Website Product for a docfield."
		table_multiselect_data = get_table_multiselect_data(docfield)
		field = docfield.fieldname

		for row in table_multiselect_data:
			# add copied multiselect data rows in Website Product
			web_product = frappe.db.get_value("Website Product", {"product_code": row.parent})
			web_product_doc = frappe.get_doc("Website Product", web_product)

			child_doc = frappe.new_doc(docfield.options, web_product_doc, field)

			for field in ["name", "creation", "modified", "idx"]:
				row[field] = None

			child_doc.update(row)

			child_doc.parenttype = "Website Product"
			child_doc.parent = web_product

			child_doc.insert()

	def get_table_multiselect_data(docfield):
		child_table = frappe.qb.DocType(docfield.options)
		product = frappe.qb.DocType("Product")

		table_multiselect_data = (  # query table data for field
			frappe.qb.from_(child_table)
			.join(product)
			.on(product.product_code == child_table.parent)
			.select(child_table.star)
			.where((child_table.parentfield == docfield.fieldname) & (product.published_in_website == 1))
		).run(as_dict=True)

		return table_multiselect_data

	settings = frappe.get_doc("E Commerce Settings")

	if not (settings.enable_field_filters or settings.filter_fields):
		return

	product_meta = frappe.get_meta("Product")
	valid_product_fields = [
		df.fieldname for df in product_meta.fields if df.fieldtype in ["Link", "Table MultiSelect"]
	]

	web_product_meta = frappe.get_meta("Website Product")
	valid_web_product_fields = [
		df.fieldname for df in web_product_meta.fields if df.fieldtype in ["Link", "Table MultiSelect"]
	]

	for row in settings.filter_fields:
		# skip if illegal field
		if row.fieldname not in valid_product_fields:
			continue

		# if Product field is not in Website Product, add it as a custom field
		if row.fieldname not in valid_web_product_fields:
			df = product_meta.get_field(row.fieldname)
			create_custom_field(
				"Website Product",
				dict(
					owner="Administrator",
					fieldname=df.fieldname,
					label=df.label,
					fieldtype=df.fieldtype,
					options=df.options,
					description=df.description,
					read_only=df.read_only,
					no_copy=df.no_copy,
					insert_after="on_backorder",
				),
			)

			# map field values
			if df.fieldtype == "Table MultiSelect":
				move_table_multiselect_data(df)
			else:
				frappe.db.sql(  # nosemgrep
					"""
						UPDATE `tabWebsite Product` wi, `tabProduct` i
						SET wi.{0} = i.{0}
						WHERE wi.product_code = i.product_code
					""".format(
						row.fieldname
					)
				)
