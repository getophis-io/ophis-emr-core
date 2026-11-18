import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import provinces
from ophis_emr_core.emr_core.testing import make_clinic
from ophis_emr_core.install import ensure_default_clinic

IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]


class TestClinic(IntegrationTestCase):
	def test_a_clinic_is_a_branch(self):
		clinic = make_clinic()
		self.assertEqual(clinic.branch, clinic.name)
		self.assertTrue(frappe.db.exists("Branch", clinic.name))

	def test_an_existing_branch_becomes_the_clinic(self):
		branch = frappe.get_doc(
			{"doctype": "Branch", "branch": f"Branch {frappe.generate_hash(length=6)}"}
		).insert()
		self.assertEqual(make_clinic(clinic_name=branch.name).branch, branch.name)

	def test_time_zone_defaults_to_the_province(self):
		self.assertEqual(make_clinic(province="NS").time_zone, "America/Halifax")
		self.assertEqual(
			make_clinic(province="ON", time_zone="America/Winnipeg").time_zone, "America/Winnipeg"
		)
		self.assertRaises(frappe.ValidationError, make_clinic, time_zone="Atlantic/Nowhere")

	def test_a_clinic_is_not_deleted_or_moved(self):
		clinic = make_clinic()
		self.assertRaises(frappe.ValidationError, clinic.delete)
		other = frappe.db.get_value("Company", {"name": ("!=", clinic.company)}, "name")
		if other:
			clinic.company = other
			self.assertRaises(frappe.ValidationError, clinic.save)

	def test_province_follows_the_clinic(self):
		nova_scotia = make_clinic(province="NS")
		ontario = make_clinic(province="ON")
		self.assertEqual(provinces.get_clinic_province(nova_scotia.branch), "NS")
		self.assertEqual(provinces.get_clinic_province(ontario.branch), "ON")
		# Unknown branches, and no branch on a site with clinics in two provinces, use the site's.
		self.assertEqual(provinces.get_clinic_province("No Such Branch"), provinces.get_site_province())
		self.assertEqual(provinces.get_clinic_province(), provinces.get_site_province())

	def test_a_site_with_one_clinic_uses_its_province(self):
		frappe.db.delete("Clinic")
		provinces.clear_cache()
		make_clinic(province="NS")
		self.assertEqual(provinces.get_clinic_province(), "NS")

	def test_a_site_without_a_clinic_gets_one_from_its_company(self):
		frappe.db.delete("Clinic")
		clinic = ensure_default_clinic()
		company = clinic.company
		self.assertEqual(clinic.clinic_name, frappe.db.get_value("Company", company, "company_name"))
		self.assertEqual(clinic.province, provinces.get_site_province())
		self.assertIsNone(ensure_default_clinic())

	def test_province_from_address_text(self):
		self.assertEqual(provinces.province_from_text("Nova Scotia"), "NS")
		self.assertEqual(provinces.province_from_text(" on "), "ON")
		self.assertIsNone(provinces.province_from_text("Ohio"))

	def tearDown(self):
		provinces.clear_cache()
