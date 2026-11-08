import frappe
from frappe import _
from frappe.model.document import Document


class Practitioner(Document):
	def validate(self):
		self.practitioner_name = " ".join(filter(None, [self.first_name, self.last_name]))
		self.validate_ohip_numbers()
		# Only someone who is practising takes on new patients.
		if self.status != "Active":
			self.accepting_patients = 0

	def validate_ohip_numbers(self):
		if self.ohip_billing_number and not (
			self.ohip_billing_number.isdigit() and len(self.ohip_billing_number) == 6
		):
			frappe.throw(_("OHIP Billing Number must be 6 digits."))
		if self.ohip_specialty_code and not (
			self.ohip_specialty_code.isdigit() and len(self.ohip_specialty_code) == 2
		):
			frappe.throw(_("OHIP Specialty Code must be 2 digits."))
