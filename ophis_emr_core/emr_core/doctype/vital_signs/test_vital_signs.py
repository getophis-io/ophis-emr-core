import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core.testing import make_patient

# Tests build their own records with the factories in ophis_emr_core.emr_core.testing, so Frappe does not
# need to generate ERPNext test fixtures for the standard DocTypes this one links to.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]

class TestVitalSigns(IntegrationTestCase):
	def record(self, **kwargs):
		return frappe.get_doc({"doctype": "Vital Signs", "patient": make_patient().name, **kwargs}).insert()

	def test_bmi_is_computed(self):
		self.assertEqual(self.record(height_cm=170, weight_kg=65).bmi, 22.5)
		self.assertFalse(self.record(weight_kg=65).bmi)

	def test_implausible_values_are_rejected(self):
		self.assertRaises(frappe.ValidationError, self.record, oxygen_saturation=101)
		self.assertRaises(frappe.ValidationError, self.record, bp_systolic=80, bp_diastolic=120)
