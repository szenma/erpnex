# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


from collections import deque
from functools import partial

import frappe
from frappe.tests.utils import FrappeTestCase, timeout
from frappe.utils import cstr, flt

from erpnext.controllers.tests.test_subcontracting_controller import (
	make_stock_in_entry,
	set_backflush_based_on,
)
from erpnext.manufacturing.doctype.bom.bom import BOMRecursionError, product_query, make_variant_bom
from erpnext.manufacturing.doctype.bom_update_log.test_bom_update_log import (
	update_cost_in_all_boms_in_test,
)
from erpnext.stock.doctype.product.test_product import make_product
from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import (
	create_stock_reconciliation,
)

test_records = frappe.get_test_records("BOM")
test_dependencies = ["Product", "Quality Inspection Template"]


class TestBOM(FrappeTestCase):
	@timeout
	def test_get_products(self):
		from erpnext.manufacturing.doctype.bom.bom import get_bom_products_as_dict

		products_dict = get_bom_products_as_dict(
			bom=get_default_bom(), company="_Test Company", qty=1, fetch_exploded=0
		)
		self.assertTrue(test_records[2]["products"][0]["product_code"] in products_dict)
		self.assertTrue(test_records[2]["products"][1]["product_code"] in products_dict)
		self.assertEqual(len(products_dict.values()), 2)

	@timeout
	def test_get_products_exploded(self):
		from erpnext.manufacturing.doctype.bom.bom import get_bom_products_as_dict

		products_dict = get_bom_products_as_dict(
			bom=get_default_bom(), company="_Test Company", qty=1, fetch_exploded=1
		)
		self.assertTrue(test_records[2]["products"][0]["product_code"] in products_dict)
		self.assertFalse(test_records[2]["products"][1]["product_code"] in products_dict)
		self.assertTrue(test_records[0]["products"][0]["product_code"] in products_dict)
		self.assertTrue(test_records[0]["products"][1]["product_code"] in products_dict)
		self.assertEqual(len(products_dict.values()), 3)

	@timeout
	def test_get_products_list(self):
		from erpnext.manufacturing.doctype.bom.bom import get_bom_products

		self.assertEqual(len(get_bom_products(bom=get_default_bom(), company="_Test Company")), 3)

	@timeout
	def test_default_bom(self):
		def _get_default_bom_in_product():
			return cstr(frappe.db.get_value("Product", "_Test FG Product 2", "default_bom"))

		bom = frappe.get_doc("BOM", {"product": "_Test FG Product 2", "is_default": 1})
		self.assertEqual(_get_default_bom_in_product(), bom.name)

		bom.is_active = 0
		bom.save()
		self.assertEqual(_get_default_bom_in_product(), "")

		bom.is_active = 1
		bom.is_default = 1
		bom.save()

		self.assertTrue(_get_default_bom_in_product(), bom.name)

	@timeout
	def test_update_bom_cost_in_all_boms(self):
		# get current rate for '_Test Product 2'
		bom_rates = frappe.db.get_values(
			"BOM Product",
			{
				"parent": "BOM-_Test Product Home Desktop Manufactured-001",
				"product_code": "_Test Product 2",
				"docstatus": 1,
			},
			fieldname=["rate", "base_rate"],
			as_dict=True,
		)
		rm_base_rate = bom_rates[0].get("base_rate") if bom_rates else 0

		# Reset product valuation rate
		reset_product_valuation_rate(product_code="_Test Product 2", qty=200, rate=rm_base_rate + 10)

		# update cost of all BOMs based on latest valuation rate
		update_cost_in_all_boms_in_test()

		# check if new valuation rate updated in all BOMs
		for d in frappe.db.sql(
			"""select base_rate from `tabBOM Product`
			where product_code='_Test Product 2' and docstatus=1 and parenttype='BOM'""",
			as_dict=1,
		):
			self.assertEqual(d.base_rate, rm_base_rate + 10)

	@timeout
	def test_bom_cost(self):
		bom = frappe.copy_doc(test_records[2])
		bom.insert()

		raw_material_cost = 0.0
		op_cost = 0.0

		for op_row in bom.operations:
			op_cost += op_row.operating_cost

		for row in bom.products:
			raw_material_cost += row.amount

		base_raw_material_cost = raw_material_cost * flt(
			bom.conversion_rate, bom.precision("conversion_rate")
		)
		base_op_cost = op_cost * flt(bom.conversion_rate, bom.precision("conversion_rate"))

		# test amounts in selected currency, almostEqual checks for 7 digits by default
		self.assertAlmostEqual(bom.operating_cost, op_cost)
		self.assertAlmostEqual(bom.raw_material_cost, raw_material_cost)
		self.assertAlmostEqual(bom.total_cost, raw_material_cost + op_cost)

		# test amounts in selected currency
		self.assertAlmostEqual(bom.base_operating_cost, base_op_cost)
		self.assertAlmostEqual(bom.base_raw_material_cost, base_raw_material_cost)
		self.assertAlmostEqual(bom.base_total_cost, base_raw_material_cost + base_op_cost)

	@timeout
	def test_bom_cost_with_batch_size(self):
		bom = frappe.copy_doc(test_records[2])
		bom.docstatus = 0
		op_cost = 0.0
		for op_row in bom.operations:
			op_row.docstatus = 0
			op_row.batch_size = 2
			op_row.set_cost_based_on_bom_qty = 1
			op_cost += op_row.operating_cost

		bom.save()

		for op_row in bom.operations:
			self.assertAlmostEqual(op_row.cost_per_unit, op_row.operating_cost / 2)

		self.assertAlmostEqual(bom.operating_cost, op_cost / 2)
		bom.delete()

	@timeout
	def test_bom_cost_multi_uom_multi_currency_based_on_price_list(self):
		frappe.db.set_value("Price List", "_Test Price List", "price_not_uom_dependent", 1)
		for product_code, rate in (("_Test Product", 3600), ("_Test Product Home Desktop Manufactured", 3000)):
			frappe.db.sql(
				"delete from `tabProduct Price` where price_list='_Test Price List' and product_code=%s", product_code
			)
			product_price = frappe.new_doc("Product Price")
			product_price.price_list = "_Test Price List"
			product_price.product_code = product_code
			product_price.price_list_rate = rate
			product_price.insert()

		bom = frappe.copy_doc(test_records[2])
		bom.set_rate_of_sub_assembly_product_based_on_bom = 0
		bom.rm_cost_as_per = "Price List"
		bom.buying_price_list = "_Test Price List"
		bom.products[0].uom = "_Test UOM 1"
		bom.products[0].conversion_factor = 5
		bom.insert()

		bom.update_cost(update_hour_rate=False)

		# test amounts in selected currency
		self.assertEqual(bom.products[0].rate, 300)
		self.assertEqual(bom.products[1].rate, 50)
		self.assertEqual(bom.operating_cost, 100)
		self.assertEqual(bom.raw_material_cost, 450)
		self.assertEqual(bom.total_cost, 550)

		# test amounts in selected currency
		self.assertEqual(bom.products[0].base_rate, 18000)
		self.assertEqual(bom.products[1].base_rate, 3000)
		self.assertEqual(bom.base_operating_cost, 6000)
		self.assertEqual(bom.base_raw_material_cost, 27000)
		self.assertEqual(bom.base_total_cost, 33000)

	@timeout
	def test_bom_cost_multi_uom_based_on_valuation_rate(self):
		bom = frappe.copy_doc(test_records[2])
		bom.set_rate_of_sub_assembly_product_based_on_bom = 0
		bom.rm_cost_as_per = "Valuation Rate"
		bom.products[0].uom = "_Test UOM 1"
		bom.products[0].conversion_factor = 6
		bom.insert()

		reset_product_valuation_rate(
			product_code="_Test Product",
			warehouse_list=frappe.get_all(
				"Warehouse", {"is_group": 0, "company": bom.company}, pluck="name"
			),
			qty=200,
			rate=200,
		)

		bom.update_cost()

		self.assertEqual(bom.products[0].rate, 20)

	@timeout
	def test_bom_cost_with_fg_based_operating_cost(self):
		bom = frappe.copy_doc(test_records[4])
		bom.insert()

		raw_material_cost = 0.0
		op_cost = 0.0

		op_cost = bom.quantity * bom.operating_cost_per_bom_quantity

		for row in bom.products:
			raw_material_cost += row.amount

		base_raw_material_cost = raw_material_cost * flt(
			bom.conversion_rate, bom.precision("conversion_rate")
		)
		base_op_cost = op_cost * flt(bom.conversion_rate, bom.precision("conversion_rate"))

		# test amounts in selected currency, almostEqual checks for 7 digits by default
		self.assertAlmostEqual(bom.operating_cost, op_cost)
		self.assertAlmostEqual(bom.raw_material_cost, raw_material_cost)
		self.assertAlmostEqual(bom.total_cost, raw_material_cost + op_cost)

		# test amounts in selected currency
		self.assertAlmostEqual(bom.base_operating_cost, base_op_cost)
		self.assertAlmostEqual(bom.base_raw_material_cost, base_raw_material_cost)
		self.assertAlmostEqual(bom.base_total_cost, base_raw_material_cost + base_op_cost)

	@timeout
	def test_subcontractor_sourced_product(self):
		product_code = "_Test Subcontracted FG Product 1"
		set_backflush_based_on("Material Transferred for Subcontract")

		if not frappe.db.exists("Product", product_code):
			make_product(product_code, {"is_stock_product": 1, "is_sub_contracted_product": 1, "stock_uom": "Nos"})

		if not frappe.db.exists("Product", "Test Extra Product 1"):
			make_product("Test Extra Product 1", {"is_stock_product": 1, "stock_uom": "Nos"})

		if not frappe.db.exists("Product", "Test Extra Product 2"):
			make_product("Test Extra Product 2", {"is_stock_product": 1, "stock_uom": "Nos"})

		if not frappe.db.exists("Product", "Test Extra Product 3"):
			make_product("Test Extra Product 3", {"is_stock_product": 1, "stock_uom": "Nos"})
		bom = frappe.get_doc(
			{
				"doctype": "BOM",
				"is_default": 1,
				"product": product_code,
				"currency": "USD",
				"quantity": 1,
				"company": "_Test Company",
			}
		)

		for product in ["Test Extra Product 1", "Test Extra Product 2"]:
			product_doc = frappe.get_doc("Product", product)

			bom.append(
				"products",
				{
					"product_code": product,
					"qty": 1,
					"uom": product_doc.stock_uom,
					"stock_uom": product_doc.stock_uom,
					"rate": product_doc.valuation_rate,
				},
			)

		bom.append(
			"products",
			{
				"product_code": "Test Extra Product 3",
				"qty": 1,
				"uom": product_doc.stock_uom,
				"stock_uom": product_doc.stock_uom,
				"rate": 0,
				"sourced_by_supplier": 1,
			},
		)
		bom.insert(ignore_permissions=True)
		bom.update_cost()
		bom.submit()
		# test that sourced_by_supplier rate is zero even after updating cost
		self.assertEqual(bom.products[2].rate, 0)

		from erpnext.controllers.tests.test_subcontracting_controller import (
			get_subcontracting_order,
			make_service_product,
		)

		make_service_product("Subcontracted Service Product 1")
		service_products = [
			{
				"warehouse": "_Test Warehouse - _TC",
				"product_code": "Subcontracted Service Product 1",
				"qty": 1,
				"rate": 100,
				"fg_product": product_code,
				"fg_product_qty": 1,
			},
		]
		# test in Subcontracting Order sourced_by_supplier is not added to Supplied Product
		sco = get_subcontracting_order(
			service_products=service_products, supplier_warehouse="_Test Warehouse 1 - _TC"
		)
		bom_products = sorted([d.product_code for d in bom.products if d.sourced_by_supplier != 1])
		supplied_products = sorted([d.rm_product_code for d in sco.supplied_products])
		self.assertEqual(bom_products, supplied_products)

	@timeout
	def test_bom_tree_representation(self):
		bom_tree = {
			"Assembly": {
				"SubAssembly1": {
					"ChildPart1": {},
					"ChildPart2": {},
				},
				"SubAssembly2": {"ChildPart3": {}},
				"SubAssembly3": {"SubSubAssy1": {"ChildPart4": {}}},
				"ChildPart5": {},
				"ChildPart6": {},
				"SubAssembly4": {"SubSubAssy2": {"ChildPart7": {}}},
			}
		}
		parent_bom = create_nested_bom(bom_tree, prefix="")
		created_tree = parent_bom.get_tree_representation()

		reqd_order = level_order_traversal(bom_tree)[1:]  # skip first product
		created_order = created_tree.level_order_traversal()

		self.assertEqual(len(reqd_order), len(created_order))

		for reqd_product, created_product in zip(reqd_order, created_order):
			self.assertEqual(reqd_product, created_product.product_code)

	@timeout
	def test_generated_variant_bom(self):
		from erpnext.controllers.product_variant import create_variant

		template_product = make_product(
			"_TestTemplateProduct",
			{
				"has_variants": 1,
				"attributes": [
					{"attribute": "Test Size"},
				],
			},
		)
		variant = create_variant(template_product.product_code, {"Test Size": "Large"})
		variant.insert(ignore_if_duplicate=True)

		bom_tree = {
			template_product.product_code: {
				"SubAssembly1": {
					"ChildPart1": {},
					"ChildPart2": {},
				},
				"ChildPart5": {},
			}
		}
		template_bom = create_nested_bom(bom_tree, prefix="")
		variant_bom = make_variant_bom(
			template_bom.name, template_bom.name, variant.product_code, variant_products=[]
		)
		variant_bom.save()

		reqd_order = template_bom.get_tree_representation().level_order_traversal()
		created_order = variant_bom.get_tree_representation().level_order_traversal()

		self.assertEqual(len(reqd_order), len(created_order))

		for reqd_product, created_product in zip(reqd_order, created_order):
			self.assertEqual(reqd_product.product_code, created_product.product_code)
			self.assertEqual(reqd_product.qty, created_product.qty)
			self.assertEqual(reqd_product.exploded_qty, created_product.exploded_qty)

	@timeout
	def test_bom_recursion_1st_level(self):
		"""BOM should not allow BOM product again in child"""
		product_code = make_product(properties={"is_stock_product": 1}).name

		bom = frappe.new_doc("BOM")
		bom.product = product_code
		bom.append("products", frappe._dict(product_code=product_code))
		bom.save()
		with self.assertRaises(BOMRecursionError):
			bom.products[0].bom_no = bom.name
			bom.save()

	@timeout
	def test_bom_recursion_transitive(self):
		product1 = make_product(properties={"is_stock_product": 1}).name
		product2 = make_product(properties={"is_stock_product": 1}).name

		bom1 = frappe.new_doc("BOM")
		bom1.product = product1
		bom1.append("products", frappe._dict(product_code=product2))
		bom1.save()

		bom2 = frappe.new_doc("BOM")
		bom2.product = product2
		bom2.append("products", frappe._dict(product_code=product1))
		bom2.save()

		bom2.products[0].bom_no = bom1.name
		bom1.products[0].bom_no = bom2.name

		with self.assertRaises(BOMRecursionError):
			bom1.save()
			bom2.save()

	@timeout
	def test_bom_with_process_loss_product(self):
		fg_product_non_whole, fg_product_whole, bom_product = create_process_loss_bom_products()

		bom_doc = create_bom_with_process_loss_product(
			fg_product_non_whole, bom_product, scrap_qty=2, scrap_rate=0, process_loss_percentage=110
		)
		#  PL can't be > 100
		self.assertRaises(frappe.ValidationError, bom_doc.submit)

		bom_doc = create_bom_with_process_loss_product(fg_product_whole, bom_product, process_loss_percentage=20)
		#  Products with whole UOMs can't be PL Products
		self.assertRaises(frappe.ValidationError, bom_doc.submit)

	@timeout
	def test_bom_product_query(self):
		query = partial(
			product_query,
			doctype="Product",
			txt="",
			searchfield="name",
			start=0,
			page_len=20,
			filters={"is_stock_product": 1},
		)

		test_products = query(txt="_Test")
		filtered = query(txt="_Test Product 2")

		self.assertNotEqual(
			len(test_products), len(filtered), msg="Product filtering showing excessive results"
		)
		self.assertTrue(0 < len(filtered) <= 3, msg="Product filtering showing excessive results")

	@timeout
	def test_exclude_exploded_products_from_bom(self):
		bom_no = get_default_bom()
		new_bom = frappe.copy_doc(frappe.get_doc("BOM", bom_no))
		for row in new_bom.products:
			if row.product_code == "_Test Product Home Desktop Manufactured":
				self.assertTrue(row.bom_no)
				row.do_not_explode = True

		new_bom.docstatus = 0
		new_bom.save()
		new_bom.load_from_db()

		for row in new_bom.products:
			if row.product_code == "_Test Product Home Desktop Manufactured" and row.do_not_explode:
				self.assertFalse(row.bom_no)

		new_bom.delete()

	@timeout
	def test_valid_transfer_defaults(self):
		bom_with_op = frappe.db.get_value(
			"BOM", {"product": "_Test FG Product 2", "with_operations": 1, "is_active": 1}
		)
		bom = frappe.copy_doc(frappe.get_doc("BOM", bom_with_op), ignore_no_copy=False)

		# test defaults
		bom.docstatus = 0
		bom.transfer_material_against = None
		bom.insert()
		self.assertEqual(bom.transfer_material_against, "Work Order")

		bom.reload()
		bom.transfer_material_against = None
		with self.assertRaises(frappe.ValidationError):
			bom.save()
		bom.reload()

		# test saner default
		bom.transfer_material_against = "Job Card"
		bom.with_operations = 0
		bom.save()
		self.assertEqual(bom.transfer_material_against, "Work Order")

		# test no value on existing doc
		bom.transfer_material_against = None
		bom.with_operations = 0
		bom.save()
		self.assertEqual(bom.transfer_material_against, "Work Order")
		bom.delete()

	@timeout
	def test_bom_name_length(self):
		"""test >140 char names"""
		bom_tree = {"x" * 140: {" ".join(["abc"] * 35): {}}}
		create_nested_bom(bom_tree, prefix="")

	@timeout
	def test_version_index(self):

		bom = frappe.new_doc("BOM")

		version_index_test_cases = [
			(1, []),
			(1, ["BOM#XYZ"]),
			(2, ["BOM/PRODUCT/001"]),
			(2, ["BOM-PRODUCT-001"]),
			(3, ["BOM-PRODUCT-001", "BOM-PRODUCT-002"]),
			(4, ["BOM-PRODUCT-001", "BOM-PRODUCT-002", "BOM-PRODUCT-003"]),
		]

		for expected_index, existing_boms in version_index_test_cases:
			with self.subTest():
				self.assertEqual(
					expected_index,
					bom.get_next_version_index(existing_boms),
					msg=f"Incorrect index for {existing_boms}",
				)

	@timeout
	def test_bom_versioning(self):
		bom_tree = {frappe.generate_hash(length=10): {frappe.generate_hash(length=10): {}}}
		bom = create_nested_bom(bom_tree, prefix="")
		self.assertEqual(int(bom.name.split("-")[-1]), 1)
		original_bom_name = bom.name

		bom.cancel()
		bom.reload()
		self.assertEqual(bom.name, original_bom_name)

		# create a new amendment
		amendment = frappe.copy_doc(bom)
		amendment.docstatus = 0
		amendment.amended_from = bom.name

		amendment.save()
		amendment.submit()
		amendment.reload()

		self.assertNotEqual(amendment.name, bom.name)
		# `origname-001-1` version
		self.assertEqual(int(amendment.name.split("-")[-1]), 1)
		self.assertEqual(int(amendment.name.split("-")[-2]), 1)

		# create a new version
		version = frappe.copy_doc(amendment)
		version.docstatus = 0
		version.amended_from = None
		version.save()
		self.assertNotEqual(amendment.name, version.name)
		self.assertEqual(int(version.name.split("-")[-1]), 2)

	@timeout
	def test_clear_inpection_quality(self):

		bom = frappe.copy_doc(test_records[2], ignore_no_copy=True)
		bom.docstatus = 0
		bom.is_default = 0
		bom.quality_inspection_template = "_Test Quality Inspection Template"
		bom.inspection_required = 1
		bom.save()
		bom.reload()

		self.assertEqual(bom.quality_inspection_template, "_Test Quality Inspection Template")

		bom.inspection_required = 0
		bom.save()
		bom.reload()

		self.assertEqual(bom.quality_inspection_template, None)

	@timeout
	def test_bom_pricing_based_on_lpp(self):
		from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import make_purchase_receipt

		parent = frappe.generate_hash(length=10)
		child = frappe.generate_hash(length=10)
		bom_tree = {parent: {child: {}}}
		bom = create_nested_bom(bom_tree, prefix="")

		# add last purchase price
		make_purchase_receipt(product_code=child, rate=42)

		bom = frappe.copy_doc(bom)
		bom.docstatus = 0
		bom.amended_from = None
		bom.rm_cost_as_per = "Last Purchase Rate"
		bom.conversion_rate = 1
		bom.save()
		bom.submit()
		self.assertEqual(bom.products[0].rate, 42)

	@timeout
	def test_set_default_bom_for_product_having_single_bom(self):
		from erpnext.stock.doctype.product.test_product import make_product

		fg_product = make_product(properties={"is_stock_product": 1})
		bom_product = make_product(properties={"is_stock_product": 1})

		# Step 1: Create BOM
		bom = frappe.new_doc("BOM")
		bom.product = fg_product.product_code
		bom.quantity = 1
		bom.append(
			"products",
			{
				"product_code": bom_product.product_code,
				"qty": 1,
				"uom": bom_product.stock_uom,
				"stock_uom": bom_product.stock_uom,
				"rate": 100.0,
			},
		)
		bom.save()
		bom.submit()
		self.assertEqual(frappe.get_value("Product", fg_product.product_code, "default_bom"), bom.name)

		# Step 2: Uncheck is_active field
		bom.is_active = 0
		bom.save()
		bom.reload()
		self.assertIsNone(frappe.get_value("Product", fg_product.product_code, "default_bom"))

		# Step 3: Check is_active field
		bom.is_active = 1
		bom.save()
		bom.reload()
		self.assertEqual(frappe.get_value("Product", fg_product.product_code, "default_bom"), bom.name)

	@timeout
	def test_exploded_products_rate(self):
		rm_product = make_product(
			properties={"is_stock_product": 1, "valuation_rate": 99, "last_purchase_rate": 89}
		).name
		fg_product = make_product(properties={"is_stock_product": 1}).name

		from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom

		bom = make_bom(product=fg_product, raw_materials=[rm_product], do_not_save=True)

		bom.rm_cost_as_per = "Last Purchase Rate"
		bom.save()
		self.assertEqual(bom.products[0].base_rate, 89)
		self.assertEqual(bom.exploded_products[0].rate, bom.products[0].base_rate)

		bom.rm_cost_as_per = "Price List"
		bom.save()
		self.assertEqual(bom.products[0].base_rate, 0.0)
		self.assertEqual(bom.exploded_products[0].rate, bom.products[0].base_rate)

		bom.rm_cost_as_per = "Valuation Rate"
		bom.save()
		self.assertEqual(bom.products[0].base_rate, 99)
		self.assertEqual(bom.exploded_products[0].rate, bom.products[0].base_rate)

		bom.submit()
		self.assertEqual(bom.exploded_products[0].rate, bom.products[0].base_rate)

	@timeout
	def test_bom_cost_update_flag(self):
		rm_product = make_product(
			properties={"is_stock_product": 1, "valuation_rate": 99, "last_purchase_rate": 89}
		).name
		fg_product = make_product(properties={"is_stock_product": 1}).name

		from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom

		bom = make_bom(product=fg_product, raw_materials=[rm_product])

		create_stock_reconciliation(
			product_code=rm_product, warehouse="_Test Warehouse - _TC", qty=100, rate=600
		)

		bom.load_from_db()
		bom.update_cost()
		self.assertTrue(bom.flags.cost_updated)

		bom.load_from_db()
		bom.update_cost()
		self.assertFalse(bom.flags.cost_updated)

	def test_do_not_include_manufacturing_and_fixed_products(self):
		from erpnext.manufacturing.doctype.bom.bom import product_query

		if not frappe.db.exists("Asset Category", "Computers-Test"):
			doc = frappe.get_doc({"doctype": "Asset Category", "asset_category_name": "Computers-Test"})
			doc.flags.ignore_mandatory = True
			doc.insert()

		for product_code, properties in {
			"_Test RM Product 1 Do Not Include In Manufacture": {
				"is_stock_product": 1,
				"include_product_in_manufacturing": 0,
			},
			"_Test RM Product 2 Fixed Asset Product": {
				"is_fixed_asset": 1,
				"is_stock_product": 0,
				"asset_category": "Computers-Test",
			},
			"_Test RM Product 3 Manufacture Product": {"is_stock_product": 1, "include_product_in_manufacturing": 1},
		}.products():
			make_product(product_code, properties)

		data = product_query(
			"Product",
			txt="_Test RM Product",
			searchfield="name",
			start=0,
			page_len=20000,
			filters={"include_product_in_manufacturing": 1, "is_fixed_asset": 0},
		)

		products = []
		for row in data:
			products.append(row[0])

		self.assertTrue("_Test RM Product 1 Do Not Include In Manufacture" not in products)
		self.assertTrue("_Test RM Product 2 Fixed Asset Product" not in products)
		self.assertTrue("_Test RM Product 3 Manufacture Product" in products)


def get_default_bom(product_code="_Test FG Product 2"):
	return frappe.db.get_value("BOM", {"product": product_code, "is_active": 1, "is_default": 1})


def level_order_traversal(node):
	traversal = []
	q = deque()
	q.append(node)

	while q:
		node = q.popleft()

		for node_name, subtree in node.products():
			traversal.append(node_name)
			q.append(subtree)

	return traversal


def create_nested_bom(tree, prefix="_Test bom "):
	"""Helper function to create a simple nested bom from tree describing product names. (along with required products)"""

	def create_products(bom_tree):
		for product_code, subtree in bom_tree.products():
			bom_product_code = prefix + product_code
			if not frappe.db.exists("Product", bom_product_code):
				frappe.get_doc(doctype="Product", product_code=bom_product_code, product_group="_Test Product Group").insert()
			create_products(subtree)

	create_products(tree)

	def dfs(tree, node):
		"""naive implementation for searching right subtree"""
		for node_name, subtree in tree.products():
			if node_name == node:
				return subtree
			else:
				result = dfs(subtree, node)
				if result is not None:
					return result

	order_of_creating_bom = reversed(level_order_traversal(tree))

	for product in order_of_creating_bom:
		child_products = dfs(tree, product)
		if child_products:
			bom_product_code = prefix + product
			bom = frappe.get_doc(doctype="BOM", product=bom_product_code)
			for child_product in child_products.keys():
				bom.append("products", {"product_code": prefix + child_product})
			bom.company = "_Test Company"
			bom.currency = "INR"
			bom.insert()
			bom.submit()

	return bom  # parent bom is last bom


def reset_product_valuation_rate(product_code, warehouse_list=None, qty=None, rate=None):
	if warehouse_list and isinstance(warehouse_list, str):
		warehouse_list = [warehouse_list]

	if not warehouse_list:
		warehouse_list = frappe.db.sql_list(
			"""
			select warehouse from `tabBin`
			where product_code=%s and actual_qty > 0
		""",
			product_code,
		)

		if not warehouse_list:
			warehouse_list.append("_Test Warehouse - _TC")

	for warehouse in warehouse_list:
		create_stock_reconciliation(product_code=product_code, warehouse=warehouse, qty=qty, rate=rate)


def create_bom_with_process_loss_product(
	fg_product, bom_product, scrap_qty=0, scrap_rate=0, fg_qty=2, process_loss_percentage=0
):
	bom_doc = frappe.new_doc("BOM")
	bom_doc.product = fg_product.product_code
	bom_doc.quantity = fg_qty
	bom_doc.append(
		"products",
		{
			"product_code": bom_product.product_code,
			"qty": 1,
			"uom": bom_product.stock_uom,
			"stock_uom": bom_product.stock_uom,
			"rate": 100.0,
		},
	)

	if scrap_qty:
		bom_doc.append(
			"scrap_products",
			{
				"product_code": fg_product.product_code,
				"qty": scrap_qty,
				"stock_qty": scrap_qty,
				"uom": fg_product.stock_uom,
				"stock_uom": fg_product.stock_uom,
				"rate": scrap_rate,
			},
		)

	bom_doc.currency = "INR"
	bom_doc.process_loss_percentage = process_loss_percentage
	return bom_doc


def create_process_loss_bom_products():
	product_list = [
		("_Test Product - Non Whole UOM", "Kg"),
		("_Test Product - Whole UOM", "Unit"),
		("_Test PL BOM Product", "Unit"),
	]
	return [create_process_loss_bom_product(it) for it in product_list]


def create_process_loss_bom_product(product_tuple):
	product_code, stock_uom = product_tuple
	if frappe.db.exists("Product", product_code) is None:
		return make_product(product_code, {"stock_uom": stock_uom, "valuation_rate": 100})
	else:
		return frappe.get_doc("Product", product_code)
