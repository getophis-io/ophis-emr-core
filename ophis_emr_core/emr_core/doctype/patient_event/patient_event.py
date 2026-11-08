import frappe
from frappe import _
from frappe.model.document import Document


class PatientEvent(Document):
	"""One entry in a patient's changelog. Written once, by the system, and never altered."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("Patient events cannot be changed."), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("Patient events cannot be deleted."), frappe.PermissionError)
