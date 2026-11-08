import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core.testing import make_patient, make_practitioner, make_user

# Tests build their own records with the factories in ophis_emr_core.emr_core.testing, so Frappe does not
# need to generate ERPNext test fixtures for the standard DocTypes this one links to.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Company", "Employee"]

AUTHOR = "author.physician@example.com"
COLLEAGUE = "colleague.physician@example.com"


class TestClinicalNote(IntegrationTestCase):
	def setUp(self):
		make_user(AUTHOR, ("EMR Physician",))
		make_user(COLLEAGUE, ("EMR Physician",))
		self.author = frappe.db.get_value("Practitioner", {"user": AUTHOR}) or make_practitioner(AUTHOR).name
		self.patient = make_patient()
		frappe.set_user(AUTHOR)

	def tearDown(self):
		frappe.set_user("Administrator")

	def draft(self, **kwargs):
		values = {
			"doctype": "Clinical Note",
			"patient": self.patient.name,
			"subjective": "Sore throat for three days.",
			"objective": "Afebrile. Pharynx erythematous, no exudate.",
			"assessment": "Viral pharyngitis.",
			"plan": "Supportive care. Return if not improving in one week.",
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert()

	def test_author_defaults_to_the_practitioner_of_the_user(self):
		self.assertEqual(self.draft().practitioner, self.author)

	def test_note_from_a_login_without_a_practitioner_says_so(self):
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError) as refused:
			self.draft().insert()
		self.assertIn("not linked to a practitioner record", str(refused.exception))
		self.assertNotIsInstance(refused.exception, frappe.MandatoryError)

	def test_empty_note_is_rejected(self):
		self.assertRaises(
			frappe.ValidationError, self.draft, subjective=" ", objective="", assessment="", plan=""
		)

	def test_signing_records_who_and_when(self):
		note = self.draft()
		self.assertEqual(note.status, "Draft")
		note.submit()
		self.assertEqual(note.status, "Signed")
		self.assertEqual(note.signed_by, AUTHOR)
		self.assertIsNotNone(note.signed_on)

	def test_only_the_author_can_sign(self):
		note = self.draft()
		frappe.set_user(COLLEAGUE)
		self.assertRaises(frappe.PermissionError, frappe.get_doc("Clinical Note", note.name).submit)

	def test_signed_note_cannot_be_changed_cancelled_or_deleted(self):
		note = self.draft()
		note.submit()

		note.reload()
		note.assessment = "Something else entirely."
		self.assertRaises(frappe.ValidationError, note.save)

		# Physicians hold neither cancel nor delete, and the controller refuses even for Administrator.
		note.reload()
		self.assertRaises(frappe.PermissionError, note.cancel)
		self.assertRaises(frappe.PermissionError, frappe.delete_doc, "Clinical Note", note.name)
		frappe.set_user("Administrator")
		note.reload()
		self.assertRaises(frappe.ValidationError, note.cancel)
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, "Clinical Note", note.name)
		self.assertEqual(frappe.db.get_value("Clinical Note", note.name, "assessment"), "Viral pharyngitis.")

	def test_amendment_supersedes_the_original_and_keeps_it(self):
		original = self.draft()
		original.submit()

		amendment = frappe.get_doc("Clinical Note", original.create_amendment("Wrong assessment recorded"))
		self.assertEqual(amendment.docstatus, 0)
		self.assertEqual(amendment.status, "Draft")
		self.assertEqual(amendment.amends, original.name)
		self.assertEqual(amendment.subjective, original.subjective)
		self.assertIsNone(amendment.signed_by)
		# The original stays current until the amendment is actually signed.
		self.assertEqual(frappe.db.get_value("Clinical Note", original.name, "status"), "Signed")

		amendment.assessment = "Streptococcal pharyngitis."
		amendment.submit()

		original.reload()
		self.assertEqual(original.status, "Amended")
		self.assertEqual(original.superseded_by, amendment.name)
		self.assertEqual(original.assessment, "Viral pharyngitis.")
		self.assertEqual(original.docstatus, 1)

	def test_note_cannot_be_amended_twice(self):
		original = self.draft()
		original.submit()

		pending = frappe.get_doc("Clinical Note", original.create_amendment("First correction"))
		self.assertRaises(frappe.ValidationError, original.create_amendment, "Second correction")

		pending.submit()
		original.reload()
		self.assertRaises(frappe.ValidationError, original.create_amendment, "Third correction")

	def test_amendment_requires_a_reason(self):
		original = self.draft()
		original.submit()
		self.assertRaises(frappe.ValidationError, original.create_amendment, " ")
