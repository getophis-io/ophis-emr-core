import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core.testing import make_practitioner

# Tests build their own records with the factories in ophis_emr_core.emr_core.testing, so Frappe does not
# need to generate ERPNext test fixtures for the standard DocTypes this one links to.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]


class TestPractitioner(IntegrationTestCase):
	def test_full_name(self):
		self.assertEqual(make_practitioner(first_name="Grace", last_name="Chen").practitioner_name, "Grace Chen")

	def test_ohip_numbers_are_validated(self):
		make_practitioner(ohip_billing_number="123456", ohip_specialty_code="00")
		self.assertRaises(frappe.ValidationError, make_practitioner, ohip_billing_number="12345")
		self.assertRaises(frappe.ValidationError, make_practitioner, ohip_specialty_code="0A")
