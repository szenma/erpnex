# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import unittest

import frappe
from frappe.utils.nestedset import (
	NestedSetChildExistsError,
	NestedSetInvalidMergeError,
	NestedSetMultipleRootsError,
	NestedSetRecursionError,
	get_ancestors_of,
	rebuild_tree,
)

test_records = frappe.get_test_records("Product Group")


class TestProduct(unittest.TestCase):
	def test_basic_tree(self, records=None):
		min_lft = 1
		max_rgt = frappe.db.sql("select max(rgt) from `tabProduct Group`")[0][0]

		if not records:
			records = test_records[2:]

		for product_group in records:
			lft, rgt, parent_product_group = frappe.db.get_value(
				"Product Group", product_group["product_group_name"], ["lft", "rgt", "parent_product_group"]
			)

			if parent_product_group:
				parent_lft, parent_rgt = frappe.db.get_value("Product Group", parent_product_group, ["lft", "rgt"])
			else:
				# root
				parent_lft = min_lft - 1
				parent_rgt = max_rgt + 1

			self.assertTrue(lft)
			self.assertTrue(rgt)
			self.assertTrue(lft < rgt)
			self.assertTrue(parent_lft < parent_rgt)
			self.assertTrue(lft > parent_lft)
			self.assertTrue(rgt < parent_rgt)
			self.assertTrue(lft >= min_lft)
			self.assertTrue(rgt <= max_rgt)

			no_of_children = self.get_no_of_children(product_group["product_group_name"])
			self.assertTrue(rgt == (lft + 1 + (2 * no_of_children)))

			no_of_children = self.get_no_of_children(parent_product_group)
			self.assertTrue(parent_rgt == (parent_lft + 1 + (2 * no_of_children)))

	def get_no_of_children(self, product_group):
		def get_no_of_children(product_groups, no_of_children):
			children = []
			for ig in product_groups:
				children += frappe.db.sql_list(
					"""select name from `tabProduct Group`
				where ifnull(parent_product_group, '')=%s""",
					ig or "",
				)

			if len(children):
				return get_no_of_children(children, no_of_children + len(children))
			else:
				return no_of_children

		return get_no_of_children([product_group], 0)

	def test_recursion(self):
		group_b = frappe.get_doc("Product Group", "_Test Product Group B")
		group_b.parent_product_group = "_Test Product Group B - 3"
		self.assertRaises(NestedSetRecursionError, group_b.save)

		# cleanup
		group_b.parent_product_group = "All Product Groups"
		group_b.save()

	def test_rebuild_tree(self):
		rebuild_tree("Product Group", "parent_product_group")
		self.test_basic_tree()

	def move_it_back(self):
		group_b = frappe.get_doc("Product Group", "_Test Product Group B")
		group_b.parent_product_group = "All Product Groups"
		group_b.save()
		self.test_basic_tree()

	def test_move_group_into_another(self):
		# before move
		old_lft, old_rgt = frappe.db.get_value("Product Group", "_Test Product Group C", ["lft", "rgt"])

		# put B under C
		group_b = frappe.get_doc("Product Group", "_Test Product Group B")
		lft, rgt = group_b.lft, group_b.rgt

		group_b.parent_product_group = "_Test Product Group C"
		group_b.save()
		self.test_basic_tree()

		# after move
		new_lft, new_rgt = frappe.db.get_value("Product Group", "_Test Product Group C", ["lft", "rgt"])

		# lft should reduce
		self.assertEqual(old_lft - new_lft, rgt - lft + 1)

		# adjacent siblings, hence rgt diff will be 0
		self.assertEqual(new_rgt - old_rgt, 0)

		self.move_it_back()

	def test_move_group_into_root(self):
		group_b = frappe.get_doc("Product Group", "_Test Product Group B")
		group_b.parent_product_group = ""
		self.assertRaises(NestedSetMultipleRootsError, group_b.save)

		# trick! works because it hasn't been rolled back :D
		self.test_basic_tree()

		self.move_it_back()

	def print_tree(self):
		import json

		print(
			json.dumps(frappe.db.sql("select name, lft, rgt from `tabProduct Group` order by lft"), indent=1)
		)

	def test_move_leaf_into_another_group(self):
		# before move
		old_lft, old_rgt = frappe.db.get_value("Product Group", "_Test Product Group C", ["lft", "rgt"])

		group_b_3 = frappe.get_doc("Product Group", "_Test Product Group B - 3")
		lft, rgt = group_b_3.lft, group_b_3.rgt

		# child of right sibling is moved into it
		group_b_3.parent_product_group = "_Test Product Group C"
		group_b_3.save()
		self.test_basic_tree()

		new_lft, new_rgt = frappe.db.get_value("Product Group", "_Test Product Group C", ["lft", "rgt"])

		# lft should remain the same
		self.assertEqual(old_lft - new_lft, 0)

		# rgt should increase
		self.assertEqual(new_rgt - old_rgt, rgt - lft + 1)

		# move it back
		group_b_3 = frappe.get_doc("Product Group", "_Test Product Group B - 3")
		group_b_3.parent_product_group = "_Test Product Group B"
		group_b_3.save()
		self.test_basic_tree()

	def test_delete_leaf(self):
		# for checking later
		parent_product_group = frappe.db.get_value(
			"Product Group", "_Test Product Group B - 3", "parent_product_group"
		)
		rgt = frappe.db.get_value("Product Group", parent_product_group, "rgt")

		ancestors = get_ancestors_of("Product Group", "_Test Product Group B - 3")
		ancestors = frappe.db.sql(
			"""select name, rgt from `tabProduct Group`
			where name in ({})""".format(
				", ".join(["%s"] * len(ancestors))
			),
			tuple(ancestors),
			as_dict=True,
		)

		frappe.delete_doc("Product Group", "_Test Product Group B - 3")
		records_to_test = test_records[2:]
		del records_to_test[4]
		self.test_basic_tree(records=records_to_test)

		# rgt of each ancestor would reduce by 2
		for product_group in ancestors:
			new_lft, new_rgt = frappe.db.get_value("Product Group", product_group.name, ["lft", "rgt"])
			self.assertEqual(new_rgt, product_group.rgt - 2)

		# insert it back
		frappe.copy_doc(test_records[6]).insert()

		self.test_basic_tree()

	def test_delete_group(self):
		# cannot delete group with child, but can delete leaf
		self.assertRaises(
			NestedSetChildExistsError, frappe.delete_doc, "Product Group", "_Test Product Group B"
		)

	def test_merge_groups(self):
		frappe.rename_doc("Product Group", "_Test Product Group B", "_Test Product Group C", merge=True)
		records_to_test = test_records[2:]
		del records_to_test[1]
		self.test_basic_tree(records=records_to_test)

		# insert Group B back
		frappe.copy_doc(test_records[3]).insert()
		self.test_basic_tree()

		# move its children back
		for name in frappe.db.sql_list(
			"""select name from `tabProduct Group`
			where parent_product_group='_Test Product Group C'"""
		):

			doc = frappe.get_doc("Product Group", name)
			doc.parent_product_group = "_Test Product Group B"
			doc.save()

		self.test_basic_tree()

	def test_merge_leaves(self):
		frappe.rename_doc("Product Group", "_Test Product Group B - 2", "_Test Product Group B - 1", merge=True)
		records_to_test = test_records[2:]
		del records_to_test[3]
		self.test_basic_tree(records=records_to_test)

		# insert Group B - 2back
		frappe.copy_doc(test_records[5]).insert()
		self.test_basic_tree()

	def test_merge_leaf_into_group(self):
		self.assertRaises(
			NestedSetInvalidMergeError,
			frappe.rename_doc,
			"Product Group",
			"_Test Product Group B - 3",
			"_Test Product Group B",
			merge=True,
		)

	def test_merge_group_into_leaf(self):
		self.assertRaises(
			NestedSetInvalidMergeError,
			frappe.rename_doc,
			"Product Group",
			"_Test Product Group B",
			"_Test Product Group B - 3",
			merge=True,
		)
