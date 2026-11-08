import frappe
from frappe.tests import UnitTestCase

from ophis_emr_core.emr_core.validators import (
	normalize_health_card_number,
	normalize_health_card_version,
	normalize_postal_code,
	passes_luhn,
)


class TestValidators(UnitTestCase):
	def test_luhn(self):
		self.assertTrue(passes_luhn("9876543217"))
		self.assertFalse(passes_luhn("9876543210"))

	def test_ontario_health_card_number_is_normalized(self):
		self.assertEqual(normalize_health_card_number("9876-543-217", "ON"), "9876543217")
		self.assertIsNone(normalize_health_card_number("", "ON"))

	def test_ontario_health_card_number_is_validated(self):
		for invalid in ("9876543210", "987654321", "98765432171", "98765A3217"):
			with self.subTest(invalid=invalid):
				self.assertRaises(frappe.ValidationError, normalize_health_card_number, invalid, "ON")

	def test_other_provinces_are_not_checked_against_the_ontario_format(self):
		self.assertEqual(normalize_health_card_number("tremj 8001 0112", "QC"), "TREMJ80010112")

	def test_health_card_version(self):
		self.assertEqual(normalize_health_card_version(" ab "), "AB")
		self.assertRaises(frappe.ValidationError, normalize_health_card_version, "A1")
		self.assertRaises(frappe.ValidationError, normalize_health_card_version, "ABC")

	def test_postal_code(self):
		self.assertEqual(normalize_postal_code("m5v3l9"), "M5V 3L9")
		self.assertEqual(normalize_postal_code("K1A-0B1"), "K1A 0B1")
		for invalid in ("12345", "D1A 1A1", "M5V 3L"):
			with self.subTest(invalid=invalid):
				self.assertRaises(frappe.ValidationError, normalize_postal_code, invalid)
