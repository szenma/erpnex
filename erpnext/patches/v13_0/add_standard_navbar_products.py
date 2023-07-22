# import frappe
from erpnext.setup.install import add_standard_navbar_products


def execute():
	# Add standard navbar products for ERPNext in Navbar Settings
	add_standard_navbar_products()
