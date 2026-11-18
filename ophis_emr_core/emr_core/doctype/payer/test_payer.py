import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import provinces
from ophis_emr_core.emr_core.doctype.payer.payer import get_health_plan_payer
from ophis_emr_core.emr_core.testing import make_payer
from ophis_emr_core.install import ensure_payers


class TestPayer(IntegrationTestCase):
	def setUp(self):
		ensure_payers()

	def test_each_known_province_has_its_health_plan(self):
		self.assertEqual(get_health_plan_payer("ON"), "OHIP")
		self.assertEqual(get_health_plan_payer("NS"), "MSI")
		# Every province and territory.
		for province in provinces.PROVINCES:
			self.assertEqual(
				frappe.db.get_value("Payer", get_health_plan_payer(province), "province"), province
			)
		self.assertEqual(frappe.db.get_value("Payer", "Blue Cross", "payer_type"), "Public Funded")
		self.assertEqual(frappe.db.get_value("Payer", "Manulife", "payer_type"), "Private")

	def test_a_provincial_health_plan_needs_its_province(self):
		self.assertRaises(frappe.ValidationError, make_payer, payer_type="Provincial Health Plan")

	def test_a_province_has_one_enabled_health_plan(self):
		self.assertRaises(
			frappe.ValidationError, make_payer, payer_type="Provincial Health Plan", province="ON"
		)
		disabled = make_payer(payer_type="Provincial Health Plan", province="ON", enabled=0)
		self.assertEqual(disabled.province, "ON")

	def test_only_a_provincial_health_plan_has_a_province(self):
		self.assertIsNone(make_payer(payer_type="Private", province="ON").province)
