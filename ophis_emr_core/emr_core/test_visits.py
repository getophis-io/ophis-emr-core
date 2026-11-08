import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import visits
from ophis_emr_core.emr_core.doctype.encounter.encounter import WAITING_FOR_NURSE, WALK_IN
from ophis_emr_core.emr_core.testing import make_patient, make_practitioner, make_user

PHYSICIAN = "visits.physician@example.com"


class TestWalkIns(IntegrationTestCase):
	def test_walk_in_waits_for_a_nurse_with_no_practitioner(self):
		encounter = visits.walk_in(make_patient().name, "Sore throat")
		self.assertEqual((encounter.visit_type, encounter.stage, encounter.practitioner), (WALK_IN, WAITING_FOR_NURSE, None))

	def test_a_patient_walks_in_once_a_day(self):
		patient = make_patient().name
		visits.walk_in(patient)
		self.assertRaises(frappe.ValidationError, visits.walk_in, patient)

	def test_registration_offers_same_name_and_birth_date_first(self):
		existing = make_patient(date_of_birth="1980-05-05")
		values = {"first_name": existing.first_name, "last_name": existing.last_name, "date_of_birth": "1980-05-05", "sex": "Female"}
		offered = visits.register_walk_in(values)
		self.assertIn(existing.name, [row.name for row in offered["possible_duplicates"]])

		registered = visits.register_walk_in(values, allow_duplicate=True)
		self.assertEqual(frappe.db.get_value("Patient", registered["patient"], "enrolment_status"), "Not Rostered")
		self.assertEqual(frappe.db.get_value("Encounter", registered["encounter"], "patient"), registered["patient"])


class TestWhoMayTakeAVisit(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_any_physician_takes_a_walk_in_without_one(self):
		make_practitioner(user=make_user(PHYSICIAN, ("EMR Physician",)))
		encounter = visits.walk_in(make_patient().name)
		frappe.set_user(PHYSICIAN)
		self.assertTrue(visits.can_take(encounter))

	def test_a_login_without_a_practitioner_takes_nothing(self):
		encounter = visits.walk_in(make_patient().name)
		self.assertFalse(visits.can_take(encounter))


class TestOpenRecords(IntegrationTestCase):
	def test_completed_encounter_cannot_be_changed(self):
		encounter = visits.walk_in(make_patient().name)
		frappe.db.set_value("Encounter", encounter.name, "status", "Completed")
		self.assertRaises(frappe.ValidationError, visits.get_open_encounter, encounter.name)

	def test_cancelling_needs_a_reason(self):
		self.assertRaises(frappe.ValidationError, visits.cancel_appointment, "any", "  ")
