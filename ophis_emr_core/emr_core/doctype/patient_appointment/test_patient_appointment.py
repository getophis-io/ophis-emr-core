import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core.doctype.patient_appointment.patient_appointment import AppointmentOverlapError
from ophis_emr_core.emr_core.testing import make_appointment, make_patient, make_practitioner, make_room

# Tests build their own records with the factories in ophis_emr_core.emr_core.testing, so Frappe does not
# need to generate ERPNext test fixtures for the standard DocTypes this one links to.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]

class TestPatientAppointment(IntegrationTestCase):
	def setUp(self):
		self.patient = make_patient()
		self.practitioner = make_practitioner()

	def book(self, **kwargs):
		return make_appointment(self.patient.name, self.practitioner.name, **kwargs)

	def test_time_range_is_computed(self):
		appointment = self.book(start_time="09:00:00", duration=20)
		self.assertEqual(str(appointment.starts_on), "2030-01-07 09:00:00")
		self.assertEqual(str(appointment.ends_on), "2030-01-07 09:20:00")

	def test_duration_defaults_from_appointment_type(self):
		appointment = self.book(appointment_type="Annual Physical", duration=0)
		self.assertEqual(appointment.duration, 30)

	def test_duration_falls_back_to_the_practitioner_default(self):
		self.practitioner.db_set("default_appointment_duration", 20)
		self.assertEqual(self.book(duration=0).duration, 20)

	def test_overlapping_appointment_is_rejected(self):
		self.book(start_time="09:00:00", duration=30)
		self.assertRaises(AppointmentOverlapError, self.book, start_time="09:15:00")

	def test_back_to_back_appointments_are_allowed(self):
		self.book(start_time="09:00:00", duration=15)
		self.book(start_time="09:15:00", duration=15)

	def test_other_practitioner_is_not_blocked(self):
		self.book(start_time="09:00:00")
		make_appointment(self.patient.name, make_practitioner().name, start_time="09:00:00")

	def test_cancelled_appointment_releases_the_slot(self):
		appointment = self.book(start_time="09:00:00")
		appointment.cancel_appointment("Patient called to cancel")
		self.assertEqual(appointment.status, "Cancelled")
		self.book(start_time="09:00:00")

	def test_inactive_practitioner_cannot_be_booked(self):
		self.practitioner.db_set("status", "Inactive")
		self.assertRaises(frappe.ValidationError, self.book)

	def test_check_in(self):
		appointment = self.book()
		appointment.check_in()
		self.assertEqual(appointment.status, "Checked In")
		self.assertIsNotNone(appointment.checked_in_at)
		self.assertRaises(frappe.ValidationError, appointment.mark_no_show)

	def test_start_encounter(self):
		appointment = self.book(reason_for_visit="Cough for two weeks")
		appointment.check_in()
		encounter = frappe.get_doc("Encounter", appointment.start_encounter(make_room().name))
		self.assertEqual(appointment.status, "In Progress")
		self.assertEqual(appointment.encounter, encounter.name)
		self.assertEqual(encounter.patient, self.patient.name)
		self.assertEqual(encounter.appointment, appointment.name)
		self.assertEqual(encounter.reason_for_visit, "Cough for two weeks")
		self.assertEqual(encounter.encounter_type, "In-person Appointment")

	def test_virtual_visit_starts_a_video_encounter(self):
		appointment = self.book(appointment_type="Virtual Visit", start_time="13:00:00")
		encounter = frappe.get_doc("Encounter", appointment.start_encounter(make_room().name))
		self.assertEqual(encounter.encounter_type, "Online - Video")
