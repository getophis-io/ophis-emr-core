import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import billing_profiles
from ophis_emr_core.emr_core.requisitions.ontario import get_billing_number
from ophis_emr_core.emr_core.testing import make_clinic, make_practitioner

# Tests build their own records with the factories in ophis_emr_core.emr_core.testing, so Frappe does not
# need to generate ERPNext test fixtures for the standard DocTypes this one links to.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]


class TestPractitioner(IntegrationTestCase):
	def test_full_name(self):
		practitioner = make_practitioner(first_name="Grace", last_name="Chen", clinic=make_clinic().name)
		self.assertEqual(practitioner.practitioner_name, "Grace Chen")

	def test_requisition_uses_the_ontario_billing_number(self):
		practitioner = make_practitioner(clinic=make_clinic(province="NS").name)
		self.assertEqual(get_billing_number(practitioner.name), "")
		billing_profiles.set_values(practitioner.name, "ON", {"billing_number": "654321"})
		self.assertEqual(get_billing_number(practitioner.name), "654321")

	def test_deleting_a_practitioner_deletes_their_billing_profiles(self):
		practitioner = make_practitioner(
			clinic=make_clinic(province="ON").name, billing={"billing_number": "123456"}
		)
		practitioner.delete()
		self.assertFalse(
			frappe.db.exists("Practitioner Billing Profile", {"practitioner": practitioner.name})
		)
