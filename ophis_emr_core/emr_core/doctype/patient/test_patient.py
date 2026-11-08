import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from ophis_emr_core.emr_core.doctype.patient.patient import DuplicateHealthCardError
from ophis_emr_core.emr_core.testing import make_health_card_number, make_patient

# Tests build their own records with the factories in ophis_emr_core.emr_core.testing, so Frappe does not
# need to generate ERPNext test fixtures for the standard DocTypes this one links to.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]

class TestPatient(IntegrationTestCase):
	def test_full_name_and_normalization(self):
		number = make_health_card_number()
		patient = make_patient(
			first_name="Marie",
			middle_name="Anne",
			last_name="Tremblay",
			health_card_number=f"{number[:4]}-{number[4:7]}-{number[7:]}",
			health_card_version="k",
			postal_code="m5v3l9",
		)
		self.assertEqual(patient.patient_name, "Marie Anne Tremblay")
		self.assertEqual(patient.health_card_number, number)
		self.assertEqual(patient.health_card_version, "K")
		self.assertEqual(patient.postal_code, "M5V 3L9")

	def test_invalid_ontario_health_card_is_rejected(self):
		self.assertRaises(frappe.ValidationError, make_patient, health_card_number="1234567890")

	def test_patient_without_health_card_is_allowed(self):
		first = make_patient(health_card_number=None)
		second = make_patient(health_card_number=None)
		self.assertIsNone(first.health_card_number)
		self.assertIsNone(second.health_card_number)

	def test_duplicate_health_card_is_rejected(self):
		existing = make_patient()
		self.assertRaises(
			DuplicateHealthCardError, make_patient, health_card_number=existing.health_card_number
		)

	def test_same_number_from_another_province_is_not_a_duplicate(self):
		existing = make_patient()
		make_patient(health_card_number=existing.health_card_number, health_card_province="BC")

	def test_possible_duplicates_by_name_and_date_of_birth(self):
		existing = make_patient(first_name="Liam", last_name="Okafor", date_of_birth="1975-03-02")
		twin = make_patient(first_name="Liam", last_name="Okafor", date_of_birth="1975-03-02")
		other = make_patient(first_name="Liam", last_name="Okafor", date_of_birth="1975-03-03")
		self.assertEqual(twin.get_possible_duplicates(), [existing.name])
		self.assertEqual(other.get_possible_duplicates(), [])

	def test_future_date_of_birth_is_rejected(self):
		self.assertRaises(frappe.ValidationError, make_patient, date_of_birth=add_days(today(), 1))
