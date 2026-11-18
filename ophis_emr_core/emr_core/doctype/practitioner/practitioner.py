from frappe.model.document import Document

from ophis_emr_core.emr_core import billing_profiles, clinics


class Practitioner(Document):
	"""A clinician. The clinics they work at are on `Practitioner Clinic`, and what they bill each
	province with on their `Practitioner Billing Profile` for it. A new practitioner joins
	`flags.clinic`, or the user's own clinic."""

	def validate(self):
		self.practitioner_name = " ".join(filter(None, [self.first_name, self.last_name]))
		if self.is_new():
			clinics.check_clinic_can_be_found(self)
		# Only someone who is practising takes on new patients.
		if self.status != "Active":
			self.accepting_patients = 0

	def after_insert(self):
		clinics.link_new_record(self)

	def on_trash(self):
		clinics.delete_links(self)
		billing_profiles.delete_profiles(self.name)
