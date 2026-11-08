import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core.testing import make_appointment, make_patient, make_practitioner, make_room

# Practitioners link to staff records; the factories build what these tests need.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]


class TestEncounter(IntegrationTestCase):
	def setUp(self):
		self.patient = make_patient()
		self.practitioner = make_practitioner()
		self.appointment = make_appointment(self.patient.name, self.practitioner.name)
		self.encounter = frappe.get_doc("Encounter", self.appointment.start_encounter(make_room().name))

	def test_complete_closes_the_appointment(self):
		self.encounter.complete()
		self.assertEqual(self.encounter.status, "Completed")
		self.assertIsNotNone(self.encounter.completed_on)
		self.assertEqual(frappe.db.get_value("Patient Appointment", self.appointment.name, "status"), "Completed")
		self.assertRaises(frappe.ValidationError, self.encounter.complete)

	def test_cannot_complete_with_unsigned_notes(self):
		frappe.get_doc(
			{
				"doctype": "Clinical Note",
				"patient": self.patient.name,
				"encounter": self.encounter.name,
				"practitioner": self.practitioner.name,
				"subjective": "Draft in progress",
			}
		).insert()
		self.assertRaises(frappe.ValidationError, self.encounter.complete)

	def test_appointment_must_belong_to_the_patient(self):
		self.assertRaises(
			frappe.ValidationError,
			frappe.get_doc(
				{
					"doctype": "Encounter",
					"patient": make_patient().name,
					"practitioner": self.practitioner.name,
					"appointment": self.appointment.name,
				}
			).insert,
		)
