import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import billing_profiles, clinics
from ophis_emr_core.emr_core.testing import make_clinic, make_practitioner

IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]


def make_profile(practitioner, province="ON", rows=None):
	return frappe.get_doc(
		{
			"doctype": "Practitioner Billing Profile",
			"practitioner": practitioner,
			"province": province,
			"billing_values": rows if rows is not None else [{"key": "billing_number", "value": "123456"}],
		}
	).insert()


class TestPractitionerBillingProfile(IntegrationTestCase):
	def setUp(self):
		self.north = make_clinic(province="ON")
		self.downtown = make_clinic(province="ON", company=self.north.company)
		self.practitioner = make_practitioner(clinic=self.north.name).name

	def test_one_profile_per_practitioner_and_province(self):
		profile = make_profile(self.practitioner)
		self.assertEqual(profile.name, f"{self.practitioner}-ON")
		self.assertEqual(profile.billing_values[0].label, "OHIP Billing Number")
		self.assertRaises(frappe.DuplicateEntryError, make_profile, self.practitioner)

	def test_values_follow_the_provinces_keys_and_formats(self):
		for rows in (
			[{"key": "billing_number", "value": "12345"}],
			[{"key": "billing_number", "value": "123456"}, {"key": "specialty_code", "value": "0A"}],
			[{"key": "billing_number", "value": "123456"}, {"key": "msi_number", "value": "1"}],
			[{"key": "specialty_code", "value": "00"}],  # the billing number is required
			[{"key": "billing_number", "value": "123456"}, {"key": "billing_number", "value": "654321"}],
		):
			with self.subTest(rows=rows):
				self.assertRaises(frappe.ValidationError, make_profile, self.practitioner, rows=rows)
		profile = make_profile(self.practitioner, rows=[{"key": " Billing_Number ", "value": "123456"}])
		self.assertEqual(profile.billing_values[0].key, "billing_number")

	def test_a_province_not_billed_yet_takes_no_profile(self):
		self.assertRaises(frappe.ValidationError, make_profile, self.practitioner, province="NS")

	def test_only_per_clinic_values_take_a_clinic_of_the_province(self):
		nova_scotia = make_clinic(province="NS", company=self.north.company)
		base = {"key": "billing_number", "value": "123456"}
		for row in (
			{"key": "specialty_code", "value": "00", "clinic": self.north.name},
			{"key": "group_number", "value": "AB12", "clinic": nova_scotia.name},
		):
			with self.subTest(row=row):
				self.assertRaises(frappe.ValidationError, make_profile, self.practitioner, rows=[base, row])

	def test_a_clinics_own_value_wins_over_the_province_wide_one(self):
		make_profile(
			self.practitioner,
			rows=[
				{"key": "billing_number", "value": "123456"},
				{"key": "group_number", "value": "AB12"},
				{"key": "group_number", "value": "CD34", "clinic": self.downtown.name},
			],
		)
		self.assertEqual(
			billing_profiles.get_values(self.practitioner, clinic=self.north.name),
			{"billing_number": "123456", "group_number": "AB12"},
		)
		self.assertEqual(billing_profiles.get_value(self.practitioner, "group_number", clinic=self.downtown.name), "CD34")
		self.assertEqual(billing_profiles.get_value(self.practitioner, "billing_number", province="ON"), "123456")

	def test_an_inactive_profile_is_not_billed_with(self):
		profile = make_profile(self.practitioner)
		profile.status = "Inactive"
		profile.save()
		self.assertEqual(billing_profiles.get_values(self.practitioner, province="ON"), {})

	def test_set_values_makes_or_updates_the_profile(self):
		billing_profiles.set_values(self.practitioner, "ON", {"billing_number": "123456"})
		billing_profiles.set_values(self.practitioner, "ON", {"group_number": "ZZ99"}, clinic=self.downtown.name)
		billing_profiles.set_values(self.practitioner, "ON", {"billing_number": "654321"})
		profile = frappe.get_doc("Practitioner Billing Profile", f"{self.practitioner}-ON")
		self.assertEqual(
			sorted((r.key, r.value, r.clinic) for r in profile.billing_values),
			[("billing_number", "654321", None), ("group_number", "ZZ99", self.downtown.name)],
		)
