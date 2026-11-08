import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class PatientMedication(Document):
	def validate(self):
		if self.din:
			self.din = self.din.strip()
			if not (self.din.isdigit() and len(self.din) == 8):
				frappe.throw(_("DIN must be 8 digits."))
		if self.start_date and self.end_date and getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("End Date cannot be before Start Date."))
