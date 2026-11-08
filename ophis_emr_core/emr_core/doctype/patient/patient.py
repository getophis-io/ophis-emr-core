import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_link_to_form, getdate, today

from ophis_emr_core.emr_core import phi
from ophis_emr_core.emr_core.validators import (
	normalize_health_card_number,
	normalize_health_card_version,
	normalize_postal_code,
)


class DuplicateHealthCardError(frappe.ValidationError):
	pass


def get_patient_type(enrolment_status: str | None) -> str:
	"""How the clinic sees a patient: rostered patients are its Family Clinic patients, booked with
	their primary practitioner; everyone else is seen as a walk-in."""
	return "Family Clinic patient" if enrolment_status == "Rostered" else "Walk-in patient"


class Patient(phi.EncryptedFieldsMixin, Document):
	def validate(self):
		self.set_patient_name()
		self.validate_date_of_birth()
		self.validate_date_of_death()
		self.validate_primary_practitioner()
		self.validate_health_card()
		self.postal_code = normalize_postal_code(self.postal_code)
		phi.validate_lengths(self)
		for row in self.emergency_contacts:
			row.contact_name = " ".join(filter(None, [row.first_name, row.last_name]))
			phi.validate_lengths(row)
		self.warn_about_possible_duplicates()

	def after_delete(self):
		phi.encrypt_deleted_document(self)

	def set_patient_name(self):
		self.patient_name = " ".join(filter(None, [self.first_name, self.middle_name, self.last_name]))

	def validate_date_of_birth(self):
		if getdate(self.date_of_birth) > getdate(today()):
			frappe.throw(_("Date of Birth cannot be in the future."))

	def validate_date_of_death(self):
		# A date of death only means something on a deceased patient's record.
		if self.status != "Deceased":
			self.date_of_death = None
		if not self.date_of_death:
			return
		if getdate(self.date_of_death) > getdate(today()):
			frappe.throw(_("Date of Death cannot be in the future."))
		if getdate(self.date_of_death) < getdate(self.date_of_birth):
			frappe.throw(_("Date of Death cannot be before Date of Birth."))

	def validate_primary_practitioner(self):
		"""A patient is newly assigned only to a practitioner who is accepting patients.

		Patients a practitioner already has stay with them when they stop accepting more.
		"""
		before = self.get_doc_before_save()
		if not self.primary_practitioner or (before and before.primary_practitioner == self.primary_practitioner):
			return
		name, accepting = frappe.db.get_value(
			"Practitioner", self.primary_practitioner, ["practitioner_name", "accepting_patients"]
		)
		if not accepting:
			frappe.throw(_("{0} is not accepting new patients.").format(name))

	def validate_health_card(self):
		self.health_card_number = normalize_health_card_number(
			self.health_card_number, self.health_card_province
		)
		self.health_card_version = normalize_health_card_version(self.health_card_version)
		self.health_card_expiry = phi.normalize_date(self.health_card_expiry)
		if not self.health_card_number:
			return

		duplicate = frappe.db.get_value(
			"Patient",
			{
				# The number is stored encrypted; its blind index is what can be matched.
				"health_card_number_index": ("in", phi.blind_indexes("health_card_number", self.health_card_number)),
				"health_card_province": self.health_card_province,
				"name": ("!=", self.name),
			},
		)
		if duplicate:
			frappe.throw(
				_("Health Card Number {0} is already registered to {1}.").format(
					self.health_card_number, get_link_to_form("Patient", duplicate)
				),
				DuplicateHealthCardError,
			)

	def get_possible_duplicates(self) -> list[str]:
		"""Other patients sharing this patient's first name, last name and date of birth."""
		return frappe.get_all(
			"Patient",
			filters={
				"first_name": self.first_name,
				"last_name": self.last_name,
				"date_of_birth": self.date_of_birth,
				"name": ("!=", self.name),
			},
			pluck="name",
		)

	def warn_about_possible_duplicates(self):
		if not self.is_new():
			return

		duplicates = self.get_possible_duplicates()
		if duplicates:
			frappe.msgprint(
				_("Possible duplicate of {0}: same name and date of birth.").format(
					", ".join(get_link_to_form("Patient", name) for name in duplicates)
				),
				title=_("Possible Duplicate Patient"),
				indicator="orange",
			)
