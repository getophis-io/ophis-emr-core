import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import phi
from ophis_emr_core.emr_core.doctype.patient.patient import DuplicateHealthCardError
from ophis_emr_core.emr_core.testing import make_health_card_number, make_patient

PHONE = "+1 416-555-0142"
EMAIL = "synthetic.patient@example.com"
CONTACT_PHONE = "+1 647-555-0123"
EXPIRY = "2031-03-31"


def stored(patient: str, field: str, doctype: str = "Patient") -> str | None:
	"""The value as the database holds it, read past the controller."""
	key = "name" if doctype == "Patient" else "parent"
	return frappe.db.sql(f"select `{field}` from `tab{doctype}` where `{key}` = %s", patient)[0][0]


def find(field: str, value: str) -> list[str]:
	"""Patients whose `field` is exactly `value`, through the blind index."""
	index_field = phi.INDEX_FIELDS["Patient"][field]
	return frappe.get_all("Patient", filters={index_field: ("in", phi.blind_indexes(field, value))}, pluck="name")


class TestPatientEncryption(IntegrationTestCase):
	def setUp(self):
		self.number = make_health_card_number()
		self.patient = make_patient(
			health_card_number=self.number,
			health_card_version="AB",
			health_card_expiry=EXPIRY,
			mobile_phone=PHONE,
			email=EMAIL,
			emergency_contacts=[{"first_name": "Synthetic", "last_name": "Contact", "relationship": "Sibling", "phone": CONTACT_PHONE}],
		)

	def test_identifiers_are_ciphertext_in_the_database(self):
		for field, plain in (
			("health_card_number", self.number),
			("health_card_version", "AB"),
			("health_card_expiry", EXPIRY),
			("mobile_phone", PHONE),
			("email", EMAIL),
		):
			value = stored(self.patient.name, field)
			self.assertTrue(phi.is_encrypted(value), field)
			self.assertEqual(phi.decrypt(value), plain)
		self.assertTrue(phi.is_encrypted(stored(self.patient.name, "phone", "Patient Emergency Contact")))

	def test_document_holds_plain_values(self):
		self.assertEqual(self.patient.health_card_number, self.number)
		doc = frappe.get_doc("Patient", self.patient.name)
		self.assertEqual(doc.health_card_number, self.number)
		self.assertEqual(doc.health_card_version, "AB")
		self.assertEqual(doc.mobile_phone, PHONE)
		self.assertEqual(doc.email, EMAIL)
		self.assertEqual(doc.health_card_expiry, EXPIRY)
		self.assertEqual(doc.emergency_contacts[0].phone, CONTACT_PHONE)

	def test_rows_read_directly_are_decrypted_on_request(self):
		rows = frappe.get_all("Patient", filters={"name": self.patient.name}, fields=["name", "email"])
		self.assertTrue(phi.is_encrypted(rows[0].email))
		self.assertEqual(phi.decrypt_rows("Patient", rows)[0].email, EMAIL)

	def test_expiry_must_be_a_date(self):
		self.patient.health_card_expiry = "soon"
		self.assertRaises(Exception, self.patient.save)

	def test_name_stays_searchable(self):
		self.assertFalse(phi.is_encrypted(stored(self.patient.name, "patient_name")))

	def test_duplicate_health_card_is_found_through_the_index(self):
		self.assertRaises(DuplicateHealthCardError, make_patient, health_card_number=self.number)

	def test_blind_index_matches_exact_normalized_values_only(self):
		formatted = f"{self.number[:4]}-{self.number[4:7]}-{self.number[7:]}"
		self.assertIn(self.patient.name, find("health_card_number", formatted))
		self.assertNotIn(self.patient.name, find("health_card_number", self.number[:6]))
		self.assertIn(self.patient.name, find("mobile_phone", "(416) 555-0142"))

	def test_phone_matches_in_either_field(self):
		# Both phone fields share one index namespace, so one hash finds a number in either column.
		other = make_patient(home_phone="416 555 0199")
		indexes = phi.blind_indexes("mobile_phone", "4165550199")
		self.assertEqual(indexes, phi.blind_indexes("home_phone", "4165550199"))
		self.assertIn(other.name, frappe.get_all("Patient", filters={"home_phone_index": ("in", indexes)}, pluck="name"))

	def test_version_log_redacts_values(self):
		self.patient.mobile_phone = "+1 416-555-0177"
		self.patient.save(ignore_version=False)
		data = frappe.parse_json(
			frappe.get_last_doc("Version", filters={"ref_doctype": "Patient", "docname": self.patient.name}).data
		)
		change = next(change for change in data["changed"] if change[0] == "mobile_phone")
		self.assertEqual(change[1:], [phi.REDACTED, phi.REDACTED])
		self.assertNotIn("0177", frappe.as_json(data))

	def test_version_log_redacts_emergency_contacts(self):
		self.patient.emergency_contacts[0].phone = "+1 647-555-0166"
		self.patient.append("emergency_contacts", {"first_name": "Second", "last_name": "Contact", "phone": "+1 647-555-0177"})
		self.patient.save(ignore_version=False)
		data = frappe.get_last_doc("Version", filters={"ref_doctype": "Patient", "docname": self.patient.name}).data
		self.assertNotIn("0166", data)
		self.assertNotIn("0177", data)
		self.assertNotIn("0123", data)

	def test_deleted_copy_keeps_identifiers_encrypted(self):
		frappe.delete_doc("Patient", self.patient.name, force=True)
		data = frappe.parse_json(
			frappe.get_last_doc("Deleted Document", filters={"deleted_name": self.patient.name}).data
		)
		self.assertTrue(phi.is_encrypted(data["health_card_number"]))
		self.assertTrue(phi.is_encrypted(data["email"]))
		self.assertTrue(phi.is_encrypted(data["emergency_contacts"][0]["phone"]))

	def test_overlong_value_is_rejected(self):
		self.patient.email = "x" * 120 + "@example.com"
		self.assertRaises(frappe.ValidationError, self.patient.save)

	def test_missing_key_stops_reads(self):
		with patch.dict(os.environ, {phi.KEY_VARIABLE: ""}):
			self.assertRaises(phi.MissingKeyError, frappe.get_doc, "Patient", self.patient.name)

	def test_key_rotation(self):
		old_id = phi.key_id(stored(self.patient.name, "email"))
		original_key = os.environ[phi.KEY_VARIABLE]
		new_key = phi.generate_key()
		# Rotate back afterwards: the rotation rewrites every patient on the site, not just this one.
		self.addCleanup(
			lambda: patch.dict(os.environ, {phi.KEY_VARIABLE: original_key, phi.PREVIOUS_KEYS_VARIABLE: new_key})(
				phi.reencrypt_all
			)()
		)
		with patch.dict(os.environ, {phi.KEY_VARIABLE: new_key, phi.PREVIOUS_KEYS_VARIABLE: original_key}):
			# Before re-encryption: old values still read, and old blind indexes still match.
			self.assertEqual(frappe.get_doc("Patient", self.patient.name).email, EMAIL)
			self.assertIn(self.patient.name, find("health_card_number", self.number))
			self.assertRaises(DuplicateHealthCardError, make_patient, health_card_number=self.number)
			self.assertGreater(phi.count_on_old_keys(), 0)

			phi.reencrypt_all()

			self.assertEqual(phi.count_on_old_keys(), 0)
			self.assertNotEqual(phi.key_id(stored(self.patient.name, "email")), old_id)
			self.assertEqual(
				stored(self.patient.name, "health_card_number_index"),
				phi.blind_index("health_card_number", self.number),
			)
			self.assertEqual(frappe.get_doc("Patient", self.patient.name).emergency_contacts[0].phone, CONTACT_PHONE)

			# Step 3: with the old key gone, everything still reads and matches.
			with patch.dict(os.environ, {phi.PREVIOUS_KEYS_VARIABLE: ""}):
				self.assertEqual(frappe.get_doc("Patient", self.patient.name).email, EMAIL)
				self.assertIn(self.patient.name, find("health_card_number", self.number))

	def test_value_under_an_unknown_key_is_refused(self):
		with patch.dict(os.environ, {phi.PREVIOUS_KEYS_VARIABLE: "", phi.KEY_VARIABLE: phi.generate_key()}):
			self.assertRaises(phi.MissingKeyError, frappe.get_doc, "Patient", self.patient.name)


class TestKeyDerivation(IntegrationTestCase):
	def test_context_is_unchanged(self):
		# Every stored value and blind index depends on it; see `KDF_CONTEXT`.
		self.assertEqual(phi.KDF_CONTEXT, b"strmlne_emr ")


class TestEncryptedFieldsOfOtherApps(IntegrationTestCase):
	def test_other_apps_declare_their_fields_through_a_hook(self):
		hooks = {"Some Other Record": ["secret_note"], "Patient": ["email", "extra_identifier"]}
		with patch.object(frappe, "get_hooks", side_effect=lambda name, *args, **kwargs: hooks if name == "phi_encrypted_fields" else []):
			fields = phi.encrypted_fields()
		self.assertEqual(fields["Some Other Record"], ("secret_note",))
		# The core's own fields stay, once each, with the app's added after them.
		self.assertEqual(fields["Patient"], (*phi.ENCRYPTED_FIELDS["Patient"], "extra_identifier"))
