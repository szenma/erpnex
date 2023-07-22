import frappe

from erpnext.stock.stock_balance import (
	get_balance_qty_from_sle,
	get_indented_qty,
	get_ordered_qty,
	get_planned_qty,
	get_reserved_qty,
)
from erpnext.stock.utils import get_bin


def execute():
	delete_broken_bins()
	delete_and_patch_duplicate_bins()


def delete_broken_bins():
	# delete useless bins
	frappe.db.sql("delete from `tabBin` where product_code is null or warehouse is null")


def delete_and_patch_duplicate_bins():

	duplicate_bins = frappe.db.sql(
		"""
		SELECT
			product_code, warehouse, count(*) as bin_count
		FROM
			tabBin
		GROUP BY
			product_code, warehouse
		HAVING
			bin_count > 1
	""",
		as_dict=1,
	)

	for duplicate_bin in duplicate_bins:
		product_code = duplicate_bin.product_code
		warehouse = duplicate_bin.warehouse
		existing_bins = frappe.get_list(
			"Bin",
			filters={"product_code": product_code, "warehouse": warehouse},
			fields=["name"],
			order_by="creation",
		)

		# keep last one
		existing_bins.pop()

		for broken_bin in existing_bins:
			frappe.delete_doc("Bin", broken_bin.name)

		qty_dict = {
			"reserved_qty": get_reserved_qty(product_code, warehouse),
			"indented_qty": get_indented_qty(product_code, warehouse),
			"ordered_qty": get_ordered_qty(product_code, warehouse),
			"planned_qty": get_planned_qty(product_code, warehouse),
			"actual_qty": get_balance_qty_from_sle(product_code, warehouse),
		}

		bin = get_bin(product_code, warehouse)
		bin.update(qty_dict)
		bin.update_reserved_qty_for_production()
		bin.update_reserved_qty_for_sub_contracting()
		if frappe.db.count(
			"Purchase Order", {"status": ["!=", "Completed"], "is_old_subcontracting_flow": 1}
		):
			bin.update_reserved_qty_for_sub_contracting(subcontract_doctype="Purchase Order")
		bin.db_update()
