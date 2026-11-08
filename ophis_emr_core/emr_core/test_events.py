import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import phi
from ophis_emr_core.emr_core.events import MODULE, get_tracked_doctypes
from ophis_emr_core.emr_core.testing import make_patient


def last_event(patient: str, doctype: str):
	return frappe.get_last_doc("Patient Event", filters={"patient": patient, "reference_doctype": doctype})


class TestChangelog(IntegrationTestCase):
	def setUp(self):
		self.patient = make_patient().name

	def test_records_of_the_clinical_module_are_tracked(self):
		tracked = get_tracked_doctypes()
		for doctype in ("Patient", "Patient Allergy", "Encounter", "Clinical Note"):
			self.assertIn(doctype, tracked)
		self.assertNotIn("Patient Event", tracked)
		self.assertTrue(all(frappe.db.get_value("DocType", doctype, "module") == MODULE for doctype in tracked))

	def test_insert_and_update_are_logged_with_their_changes(self):
		allergy = frappe.get_doc({"doctype": "Patient Allergy", "patient": self.patient, "allergen": "Sulfa"}).insert()
		self.assertEqual(last_event(self.patient, "Patient Allergy").action, "Created")

		allergy.status = "Entered in Error"
		allergy.save()
		event = last_event(self.patient, "Patient Allergy")
		self.assertEqual(event.action, "Updated")
		change = next(change for change in frappe.parse_json(event.changes) if change["fieldname"] == "status")
		self.assertEqual(change["new"], "Entered in Error")

	def test_encrypted_values_stay_encrypted_in_the_changelog(self):
		patient = frappe.get_doc("Patient", self.patient)
		patient.email = "changed.patient@example.com"
		patient.save()
		changes = frappe.parse_json(last_event(self.patient, "Patient").changes)
		change = next(change for change in changes if change["fieldname"] == "email")
		self.assertTrue(phi.is_encrypted(change["new"]))
		self.assertEqual(phi.decrypt(change["new"]), "changed.patient@example.com")
		self.assertFalse(any(change["fieldname"].endswith("_index") for change in changes))
