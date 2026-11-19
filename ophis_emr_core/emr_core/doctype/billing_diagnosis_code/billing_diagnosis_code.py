import frappe
from frappe import _
from frappe.model.document import Document


class BillingDiagnosisCode(Document):
	"""A diagnosis code of one code set version. Written only by the import (`billing_codes.importing`)."""

	def validate(self):
		frappe.throw(_("Diagnosis codes are imported from the health plan's file and cannot be changed."))

	def on_trash(self):
		frappe.throw(_("Diagnosis codes are never deleted; a code set version is retired instead."))


def on_doctype_update():
	frappe.db.add_index("Billing Diagnosis Code", ["code_set", "code"])
