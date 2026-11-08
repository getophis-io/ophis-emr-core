import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class VitalSigns(Document):
	def validate(self):
		if not self.recorded_by:
			self.recorded_by = frappe.session.user
		self.validate_ranges()
		self.set_bmi()

	def validate_ranges(self):
		if self.oxygen_saturation and self.oxygen_saturation > 100:
			frappe.throw(_("SpO2 cannot be more than 100%."))
		if self.bp_systolic and self.bp_diastolic and self.bp_diastolic >= self.bp_systolic:
			frappe.throw(_("Diastolic pressure must be lower than systolic pressure."))

	def set_bmi(self):
		self.bmi = None
		if flt(self.height_cm) and flt(self.weight_kg):
			height_m = flt(self.height_cm) / 100
			self.bmi = flt(flt(self.weight_kg) / (height_m * height_m), 1)
