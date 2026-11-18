import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import patient_billing, phi
from ophis_emr_core.emr_core.patient_billing import (
	OUT_OF_POCKET,
	PRIVATE,
	PROVINCIAL,
	PUBLIC,
	DuplicateHealthCardError,
)
from ophis_emr_core.emr_core.testing import make_health_card_number, make_patient, make_payer
from ophis_emr_core.install import ensure_payers


class TestPatientBillingProfile(IntegrationTestCase):
	def setUp(self):
		ensure_payers()

	def coverages(self, patient, active_only=True):
		"""[(type, payer, primary)] of a patient's profiles, primary first."""
		return [
			(row.coverage_type, row.payer, row.is_primary)
			for row in patient_billing.get_profiles(patient.name, active_only=active_only)
		]

	def profile(self, patient, coverage_type, status="Active"):
		name = frappe.db.get_value(
			"Patient Billing Profile",
			{"patient": patient.name, "coverage_type": coverage_type, "status": status},
		)
		return frappe.get_doc("Patient Billing Profile", name)

	def values(self, profile):
		return {row.key: row.value for row in profile.plan_values}

	def test_a_patient_without_a_health_card_pays_out_of_pocket(self):
		patient = make_patient(health_card_number=None)
		self.assertEqual(self.coverages(patient), [(OUT_OF_POCKET, None, 1)])
		self.assertEqual(self.profile(patient, OUT_OF_POCKET).plan_values, [])

	def test_an_ontario_health_card_is_the_primary_ohip_coverage(self):
		patient = make_patient(health_card_version="ab", health_card_expiry="2030-06-30")
		self.assertEqual(self.coverages(patient), [(PROVINCIAL, "OHIP", 1), (OUT_OF_POCKET, None, 0)])
		ohip = self.profile(patient, PROVINCIAL)
		self.assertEqual(
			self.values(ohip), {"health_card_number": patient.health_card.number, "version_code": "AB"}
		)
		self.assertEqual(
			(ohip.issuing_province, ohip.expiry_date, ohip.verification_status),
			("ON", "2030-06-30", "Not Checked"),
		)
		self.assertEqual(ohip.patient_name, patient.patient_name)

	def test_get_profiles_gives_each_profiles_details(self):
		patient = make_patient(health_card_version="K")
		ohip = patient_billing.get_primary_profile(patient.name)
		self.assertEqual(
			ohip.plan_values, {"health_card_number": patient.health_card.number, "version_code": "K"}
		)

	def test_a_health_card_added_later_becomes_primary(self):
		patient = make_patient(health_card_number=None)
		patient_billing.set_health_card(patient.name, "ON", make_health_card_number())
		self.assertEqual(self.coverages(patient), [(PROVINCIAL, "OHIP", 1), (OUT_OF_POCKET, None, 0)])

	def test_a_changed_card_is_a_new_profile(self):
		patient = make_patient(health_card_version="A")
		old = patient.health_card.profile
		number = make_health_card_number()
		patient_billing.set_health_card(patient.name, "ON", number, "B", "2031-01-31")
		card = patient_billing.get_health_card(patient.name)
		self.assertNotEqual(card.profile, old)
		self.assertEqual((card.number, card.version, card.expiry_date), (number, "B", "2031-01-31"))
		self.assertEqual(frappe.db.get_value("Patient Billing Profile", old, "status"), "Inactive")
		self.assertEqual(self.coverages(patient), [(PROVINCIAL, "OHIP", 1), (OUT_OF_POCKET, None, 0)])

	def test_a_card_from_another_province_ends_the_old_plan(self):
		patient = make_patient()
		old = patient.health_card.profile
		patient_billing.set_health_card(patient.name, "BC", "9876543210")
		self.assertEqual(self.coverages(patient), [(PROVINCIAL, "MSP", 1), (OUT_OF_POCKET, None, 0)])
		self.assertEqual(frappe.db.get_value("Patient Billing Profile", old, "status"), "Inactive")

	def test_removing_the_health_card_ends_the_plan(self):
		patient = make_patient()
		number = patient.health_card.number
		patient_billing.set_health_card(patient.name, "ON", None)
		self.assertEqual(self.coverages(patient), [(OUT_OF_POCKET, None, 1)])
		self.assertIsNone(patient_billing.get_health_card(patient.name))
		# An ended coverage keeps the details it had.
		self.assertEqual(
			self.values(self.profile(patient, PROVINCIAL, status="Inactive"))["health_card_number"], number
		)

	def test_every_province_has_its_health_plan(self):
		for province, plan in (("NS", "MSI"), ("QC", "RAMQ"), ("NU", "Nunavut Health Care Plan")):
			patient = make_patient(
				health_card_province=province, health_card_number=f"{province}{make_health_card_number()}"
			)
			self.assertEqual(self.coverages(patient)[0], (PROVINCIAL, plan, 1))
			self.assertEqual(patient.health_card.province, province)

	def test_only_ontario_checks_its_card_format_here(self):
		# Ontario's module checks its format; a province without one takes letters and digits.
		self.assertRaises(frappe.ValidationError, make_patient, health_card_number="1234567890")
		self.assertEqual(
			make_patient(health_card_province="MB", health_card_number="ab 12-34").health_card.number,
			"AB1234",
		)
		self.assertRaises(
			frappe.ValidationError, make_patient, health_card_province="MB", health_card_number="A/1"
		)

	def test_ontario_version_codes_are_one_or_two_letters(self):
		self.assertRaises(frappe.ValidationError, make_patient, health_card_version="A1")

	def test_a_plan_covers_only_its_own_provinces_cards(self):
		patient = make_patient(health_card_number=None)
		profile = patient_billing.add_profile(
			patient.name, PROVINCIAL, payer="MSI", values={"health_card_number": "NS123"}
		)
		self.assertEqual(profile.issuing_province, "NS")

	def test_a_card_is_registered_to_one_patient_per_province(self):
		existing = make_patient()
		self.assertRaises(
			DuplicateHealthCardError, make_patient, health_card_number=existing.health_card.number
		)
		# Once it is ended, the number is free.
		patient_billing.set_health_card(existing.name, "ON", None)
		self.assertTrue(make_patient(health_card_number=existing.health_card.number).health_card)

	def test_patients_are_found_by_their_health_card_number(self):
		patient = make_patient()
		number = patient.health_card.number
		self.assertEqual(
			patient_billing.find_patients_by_health_card(f"{number[:4]} {number[4:]}"), [patient.name]
		)
		self.assertEqual(patient_billing.find_patients_by_health_card(number, province="ON"), [patient.name])
		self.assertEqual(patient_billing.find_patients_by_health_card(number, province="BC"), [])
		self.assertEqual(patient_billing.find_patients_by_health_card(number[:6]), [])

	def test_the_same_card_again_changes_nothing_and_keeps_its_verification(self):
		patient = make_patient(health_card_version="A")
		profile = self.profile(patient, PROVINCIAL)
		profile.db_set({"verification_status": "Valid", "verification_response": "OK"})
		same = patient_billing.set_health_card(patient.name, "ON", patient.health_card.number, "a")
		self.assertEqual(same.name, profile.name)
		self.assertEqual(self.profile(patient, PROVINCIAL).verification_status, "Valid")
		# A new version is a new card: a new profile, not yet verified.
		patient_billing.set_health_card(patient.name, "ON", patient.health_card.number, "B")
		new = self.profile(patient, PROVINCIAL)
		self.assertNotEqual(new.name, profile.name)
		self.assertEqual(new.verification_status, "Not Checked")

	def test_a_profile_is_only_made_active_or_inactive_once_added(self):
		patient = make_patient()
		manulife = patient_billing.add_profile(
			patient.name,
			PRIVATE,
			payer="Manulife",
			values={"membership_number": "M1"},
			expiry_date="2030-01-31",
		)
		for change in (
			lambda doc: setattr(doc.plan_values[0], "value", "M2"),
			lambda doc: doc.append("plan_values", {"key": "employer_name", "value": "Synthetic Employer"}),
			lambda doc: setattr(doc, "expiry_date", "2031-01-31"),
			lambda doc: setattr(doc, "payer", "RBC"),
		):
			doc = frappe.get_doc("Patient Billing Profile", manulife.name)
			change(doc)
			self.assertRaises(patient_billing.ProfileChangedError, doc.save)
		doc = frappe.get_doc("Patient Billing Profile", manulife.name)
		doc.status = "Inactive"
		doc.save()
		doc.status = "Active"
		doc.save()
		self.assertEqual(frappe.db.get_value("Patient Billing Profile", manulife.name, "status"), "Active")

	def test_only_a_health_plan_has_an_issuing_province(self):
		patient = make_patient()
		self.assertEqual(self.profile(patient, PROVINCIAL).issuing_province, "ON")
		blue_cross = patient_billing.add_profile(
			patient.name,
			PUBLIC,
			payer="Blue Cross",
			values={"membership_number": "BC1"},
			expiry_date="2027-12-31",
			issuing_province="NS",
		)
		# Expiry applies to any coverage; an issuing province only to a health plan's card.
		self.assertEqual((blue_cross.expiry_date, blue_cross.issuing_province), ("2027-12-31", None))

	def suggested(self, payer):
		"""The keys the desk offers on a row of a profile with this provider."""
		return frappe.get_list("Plan Detail Key", filters={"plan": payer}, pluck="name")

	def test_each_plans_keys_are_set_in_code(self):
		self.assertEqual(
			list(patient_billing.get_plan_keys(PRIVATE, "Manulife")),
			["plan_contract_number", "membership_number", "owner_name", "employer_name"],
		)
		self.assertEqual(
			list(patient_billing.get_plan_keys(PUBLIC, "Blue Cross")),
			["membership_number", "plan_contract_number", "owner_name"],
		)
		self.assertEqual(
			list(patient_billing.get_plan_keys(PROVINCIAL, "OHIP")), ["health_card_number", "version_code"]
		)
		self.assertEqual(set(patient_billing.PLAN_KEYS_BY_PAYER), {"Blue Cross", "Manulife", "RBC"})

	def test_the_desk_suggests_the_keys_of_the_provider_chosen(self):
		# Answered from the code: Plan Detail Key is virtual, with nothing stored.
		self.assertTrue(frappe.get_meta("Plan Detail Key").is_virtual)
		self.assertEqual(self.suggested("OHIP"), ["health_card_number", "version_code"])
		self.assertEqual(self.suggested("MSI"), ["health_card_number", "version_code"])
		self.assertEqual(
			self.suggested("Manulife"),
			["plan_contract_number", "membership_number", "owner_name", "employer_name"],
		)
		self.assertEqual(
			self.suggested("Blue Cross"), ["membership_number", "plan_contract_number", "owner_name"]
		)
		self.assertEqual(self.suggested(""), [])
		meta = frappe.get_meta("Patient Billing Profile Value")
		self.assertEqual(
			frappe.parse_json(meta.get_field("key").link_filters),
			[["Plan Detail Key", "plan", "=", "eval:parent.payer"]],
		)
		self.assertEqual(meta.get_field("label").fetch_from, "key.label")

	def test_the_desks_search_returns_the_plans_keys_with_their_labels(self):
		from frappe.desk.search import search_link

		results = search_link(
			"Plan Detail Key",
			"",
			filters={"plan": ["=", "OHIP"]},
			reference_doctype="Patient Billing Profile Value",
			link_fieldname="key",
		)
		self.assertEqual(
			[(r["value"], r["description"]) for r in results],
			[("health_card_number", "Health Card Number"), ("version_code", "Version Code")],
		)
		typed = search_link("Plan Detail Key", "employ", filters={"plan": ["=", "Manulife"]})
		self.assertEqual([r["value"] for r in typed], ["employer_name"])

	def test_a_profile_with_plan_details_opens_on_the_desk(self):
		# The form loader looks up linked records' titles in their tables; a virtual DocType has
		# none, so Plan Detail Key must not ask for its title to be shown in links.
		from frappe.desk.form.load import getdoc

		self.assertFalse(frappe.get_meta("Plan Detail Key").show_title_field_in_link)
		patient = make_patient()
		getdoc("Patient Billing Profile", patient.health_card.profile)
		self.assertEqual(frappe.response.docs[0].plan_values[0].label, "Health Card Number")

	def test_a_key_is_checked_and_labelled_through_the_virtual_doctype(self):
		self.assertEqual(frappe.get_doc("Plan Detail Key", "membership_number").label, "Membership Number")
		self.assertRaises(frappe.DoesNotExistError, frappe.get_doc, "Plan Detail Key", "not_a_key")

	def test_a_payer_the_clinic_adds_takes_its_types_keys(self):
		payer = make_payer(payer_type="Private")
		self.assertEqual(
			list(patient_billing.get_plan_keys(PRIVATE, payer.name)),
			list(patient_billing.get_plan_keys(PRIVATE)),
		)
		self.assertEqual(self.suggested(payer.name), list(patient_billing.get_plan_keys(PRIVATE)))

	def test_the_provider_list_follows_the_type(self):
		# The desk offers only enabled payers of the profile's type (link_filters on Provider).
		field = frappe.get_meta("Patient Billing Profile").get_field("payer")
		self.assertEqual(
			frappe.parse_json(field.link_filters),
			[["Payer", "payer_type", "=", "eval:doc.coverage_type"], ["Payer", "enabled", "=", 1]],
		)
		province = frappe.get_meta("Patient Billing Profile").get_field("issuing_province")
		self.assertEqual((province.fetch_from, province.read_only), ("payer.province", 1))

	def test_blue_cross_and_private_plans_are_added_behind_ohip(self):
		patient = make_patient()
		blue_cross = patient_billing.add_profile(
			patient.name, PUBLIC, payer="Blue Cross", values={"membership_number": "bc-1001"}
		)
		manulife = patient_billing.add_profile(
			patient.name,
			PRIVATE,
			payer="Manulife",
			values={
				"membership_number": "m-77",
				"plan_contract_number": "g-55",
				"owner_name": "Pat Spouse",
				"employer_name": "Synthetic Employer Inc.",
			},
		)
		self.assertEqual(
			self.coverages(patient),
			[
				(PROVINCIAL, "OHIP", 1),
				(PUBLIC, "Blue Cross", 0),
				(PRIVATE, "Manulife", 0),
				(OUT_OF_POCKET, None, 0),
			],
		)
		# Identifiers in capitals, names as given, in the type's key order.
		self.assertEqual(
			[(row.key, row.value) for row in manulife.plan_values],
			[
				("plan_contract_number", "G-55"),
				("membership_number", "M-77"),
				("owner_name", "Pat Spouse"),
				("employer_name", "Synthetic Employer Inc."),
			],
		)
		# The owner defaults to the patient.
		self.assertEqual(self.values(blue_cross)["owner_name"], patient.patient_name)

	def test_publicly_funded_and_private_coverage_need_a_membership_number(self):
		patient = make_patient()
		self.assertRaises(
			frappe.ValidationError, patient_billing.add_profile, patient.name, PUBLIC, payer="Blue Cross"
		)
		self.assertRaises(
			frappe.ValidationError,
			patient_billing.add_profile,
			patient.name,
			PRIVATE,
			payer="Manulife",
			values={"plan_contract_number": "G-55"},
		)

	def test_a_key_must_be_one_of_the_types(self):
		patient = make_patient()
		self.assertRaises(
			frappe.ValidationError,
			patient_billing.add_profile,
			patient.name,
			PUBLIC,
			payer="Blue Cross",
			values={"membership_number": "BC1", "employer_name": "Not a Public Funded detail"},
		)
		out_of_pocket = self.profile(patient, OUT_OF_POCKET)
		out_of_pocket.append("plan_values", {"key": "membership_number", "value": "X1"})
		self.assertRaises(frappe.ValidationError, out_of_pocket.save)

	def test_expiry_must_be_a_date(self):
		patient = make_patient()
		self.assertRaises(
			frappe.ValidationError,
			patient_billing.add_profile,
			patient.name,
			PUBLIC,
			payer="Blue Cross",
			values={"membership_number": "BC1"},
			expiry_date="not a date",
		)

	def test_the_provider_must_be_of_the_profiles_type(self):
		patient = make_patient()
		values = {"membership_number": "X1"}
		self.assertRaises(
			frappe.ValidationError,
			patient_billing.add_profile,
			patient.name,
			PRIVATE,
			payer="Blue Cross",
			values=values,
		)
		self.assertRaises(
			frappe.ValidationError, patient_billing.add_profile, patient.name, PRIVATE, values=values
		)

	def test_a_disabled_provider_cannot_be_given_to_a_new_profile(self):
		patient = make_patient()
		payer = make_payer(enabled=0)
		self.assertRaises(
			frappe.ValidationError,
			patient_billing.add_profile,
			patient.name,
			PRIVATE,
			payer=payer.name,
			values={"membership_number": "X1"},
		)

	def test_out_of_pocket_is_kept_once_and_never_ended(self):
		patient = make_patient()
		self.assertRaises(frappe.ValidationError, patient_billing.add_profile, patient.name, OUT_OF_POCKET)
		out_of_pocket = self.profile(patient, OUT_OF_POCKET)
		out_of_pocket.status = "Inactive"
		self.assertRaises(frappe.ValidationError, out_of_pocket.save)

	def test_making_a_profile_primary_replaces_the_old_one(self):
		patient = make_patient()
		blue_cross = patient_billing.add_profile(
			patient.name, PUBLIC, payer="Blue Cross", values={"membership_number": "BC1"}, primary=True
		)
		self.assertEqual(patient_billing.get_primary_profile(patient.name).name, blue_cross.name)
		self.assertFalse(self.profile(patient, PROVINCIAL).is_primary)
		blue_cross.reload()
		blue_cross.is_primary = 0
		self.assertRaises(frappe.ValidationError, blue_cross.save)

	def test_when_the_primary_coverage_ends_the_next_in_billing_order_takes_over(self):
		patient = make_patient()
		patient_billing.add_profile(
			patient.name, PRIVATE, payer="Manulife", values={"membership_number": "M1"}
		)
		patient_billing.add_profile(
			patient.name, PUBLIC, payer="Blue Cross", values={"membership_number": "BC1"}
		)
		patient_billing.set_health_card(patient.name, "ON", None)
		self.assertEqual(patient_billing.get_primary_profile(patient.name).payer, "Blue Cross")

	def test_the_primary_profile_cannot_be_deleted(self):
		patient = make_patient()
		self.assertRaises(
			frappe.ValidationError, self.profile(patient, PROVINCIAL).delete, ignore_permissions=True
		)

	def test_plan_details_are_stored_encrypted(self):
		patient = make_patient()
		profile = patient_billing.add_profile(
			patient.name, PRIVATE, payer="Manulife", values={"membership_number": "M-77"}
		)
		stored = frappe.db.get_value(
			"Patient Billing Profile Value", {"parent": profile.name, "key": "membership_number"}, "value"
		)
		self.assertNotIn("M-77", stored)
		self.assertEqual(phi.decrypt(stored), "M-77")
		self.assertEqual(
			self.values(frappe.get_doc("Patient Billing Profile", profile.name))["membership_number"], "M-77"
		)

	def test_the_changelog_records_profiles_added_after_registration(self):
		patient = make_patient()

		def logged():
			return frappe.get_all(
				"Patient Event",
				filters={"patient": patient.name},
				fields=["action", "reference_doctype"],
				order_by="creation asc",
			)

		# Registration's own profiles are part of the patient's creation.
		self.assertEqual([(e.action, e.reference_doctype) for e in logged()], [("Created", "Patient")])
		patient_billing.add_profile(
			patient.name, PUBLIC, payer="Blue Cross", values={"membership_number": "BC1"}
		)
		self.assertEqual(
			(logged()[-1].action, logged()[-1].reference_doctype), ("Created", "Patient Billing Profile")
		)

	def test_a_save_that_changes_nothing_logs_nothing(self):
		patient = make_patient()
		profile = patient_billing.add_profile(
			patient.name, PRIVATE, payer="Manulife", values={"membership_number": "M1"}
		)
		count = frappe.db.count("Patient Event", {"patient": patient.name})
		frappe.get_doc("Patient Billing Profile", profile.name).save()
		self.assertEqual(frappe.db.count("Patient Event", {"patient": patient.name}), count)
