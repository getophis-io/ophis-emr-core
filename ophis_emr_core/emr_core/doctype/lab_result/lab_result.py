import frappe
from frappe import _
from frappe.model.document import Document


class LabResult(Document):
	"""Received from outside the clinic. It is reviewed, never edited or deleted."""

	def validate(self):
		if self.order and frappe.db.get_value(self.meta.get_field("order").options, self.order, "patient") != self.patient:
			frappe.throw(_("{0} belongs to a different patient.").format(self.order))

	def on_trash(self):
		frappe.throw(_("Results and reports received from outside the clinic cannot be deleted."), frappe.PermissionError)
