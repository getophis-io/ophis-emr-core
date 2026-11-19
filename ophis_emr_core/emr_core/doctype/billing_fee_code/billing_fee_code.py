import frappe
from frappe import _
from frappe.model.document import Document


class BillingFeeCode(Document):
	"""A fee code of one code set version. Written only by the import (`billing_codes.importing`),
	never edited or deleted on its own: a new file is a new version."""

	def validate(self):
		frappe.throw(_("Fee codes are imported from the health plan's file and cannot be changed."))

	def on_trash(self):
		frappe.throw(_("Fee codes are never deleted; a code set version is retired instead."))


def on_doctype_update():
	frappe.db.add_index("Billing Fee Code", ["code_set", "code"])
