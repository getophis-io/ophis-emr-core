import csv
import datetime
import io
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import billing_codes
from ophis_emr_core.emr_core.billing_codes import importing, repository, standard
from ophis_emr_core.emr_core.billing_codes.contract import JAN, JUL, ProviderContract, clear_code_sets
from ophis_emr_core.emr_core.billing_codes.providers import ParseError, SourceFile, UnsupportedJurisdiction
from ophis_emr_core.emr_core.billing_codes.rules import RuleEngine
from ophis_emr_core.emr_core.billing_codes.types import (
	DIAGNOSIS,
	ERROR,
	FEE,
	WARNING,
	BillingContext,
	BillingRule,
	BillLineDraft,
	FeeCode,
	PriorBilling,
	get_currency,
)
from ophis_emr_core.emr_core.testing import make_patient, make_practitioner, make_user
from ophis_emr_core.install import ensure_payers


def fee_csv(rows: list[dict], name="fees.csv") -> SourceFile:
	columns = list(dict.fromkeys(column for row in rows for column in row))
	out = io.StringIO()
	writer = csv.DictWriter(out, columns)
	writer.writeheader()
	writer.writerows(rows)
	return SourceFile(name, out.getvalue().encode())


class TestStandardProviderContract(ProviderContract, IntegrationTestCase):
	province = "ON"
	fee_codes = ("A001", "A003", "K013")
	diagnosis_codes = ("250", "799")

	def render_fee_file(self, rows):
		return fee_csv(
			[
				{
					"code": row["code"],
					"description": row["description"],
					"fee": "" if row["fee_cents"] is None else f"{row['fee_cents'] / 100:.2f}",
					"requires_diagnosis": "Y" if row["requires_diagnosis"] else "N",
				}
				for row in rows
			]
		)

	def render_diagnosis_file(self, rows):
		return fee_csv(
			[
				{
					"code": row["code"],
					"description": row["description"],
					"billable": "Y" if row["billable"] else "N",
				}
				for row in rows
			],
			"diagnoses.csv",
		)


class TestStandardFiles(IntegrationTestCase):
	"""Reading the core's CSV layout."""

	def parse_fees(self, rows):
		return standard.StandardFeeImporter().parse(fee_csv(rows))

	def test_money_is_read_into_cents(self):
		self.assertEqual(standard.cents("33.70"), 3370)
		self.assertEqual(standard.cents("$1,250.00"), 125000)
		self.assertEqual(standard.cents("12"), 1200)
		self.assertIsNone(standard.cents(""))
		self.assertIsNone(standard.cents("IC"))
		self.assertRaises(standard.LineError, standard.cents, "12.345")
		self.assertRaises(standard.LineError, standard.cents, "-5")
		self.assertRaises(standard.LineError, standard.cents, "ten")

	def test_rule_columns_become_rules(self):
		fee = self.parse_fees(
			[
				{
					"code": "a100",
					"description": "Periodic health visit",
					"fee": "77.20",
					"min_age": "18",
					"max_age": "64",
					"sex": "F;M",
					"max_per_year": "1",
					"not_with": "a101|a102",
					"professional_fee": "50.00",
					"effective_from": "2026-01-01",
				}
			]
		).fee_codes[0]
		self.assertEqual(fee.code, "A100")
		self.assertEqual(fee.components.professional, 5000)
		self.assertEqual(fee.effective_from, datetime.date(2026, 1, 1))
		rules = {rule.rule_type: rule.parameters for rule in fee.rules}
		self.assertEqual(rules["age"], {"min": 18, "max": 64})
		self.assertEqual(rules["sex"], {"allowed": ["Female", "Male"]})
		self.assertEqual(rules["max_per_year"], {"count": 1})
		self.assertEqual(rules["not_with"], {"codes": ["A101", "A102"]})

	def test_every_bad_line_is_reported_with_its_number(self):
		with self.assertRaises(ParseError) as raised:
			self.parse_fees(
				[
					{"code": "A1", "description": "Good", "fee": "1.00", "requires_diagnosis": "Y"},
					{"code": "A2", "description": "Bad fee", "fee": "abc", "requires_diagnosis": "N"},
					{"code": "A3", "description": "Bad flag", "fee": "1.00", "requires_diagnosis": "maybe"},
				]
			)
		self.assertEqual(len(raised.exception.errors), 2)
		self.assertTrue(raised.exception.errors[0].startswith("Line 3:"))
		self.assertTrue(raised.exception.errors[1].startswith("Line 4:"))

	def test_an_unknown_column_is_an_error(self):
		with self.assertRaises(ParseError) as raised:
			self.parse_fees([{"code": "A1", "description": "x", "fee": "1", "max_per_dy": "1"}])
		self.assertIn("max_per_dy", str(raised.exception))

	def test_translations(self):
		fee = self.parse_fees(
			[{"code": "A1", "description": "Assessment", "fee": "1", "description_fr": "Évaluation"}]
		).fee_codes[0]
		self.assertEqual(fee.descriptions[0].language, "fr")
		self.assertEqual(fee.descriptions[0].description, "Évaluation")

	def test_detect_tells_fee_files_from_diagnosis_files(self):
		fees = fee_csv([{"code": "A1", "description": "x", "fee": "1"}])
		diagnoses = fee_csv([{"code": "250", "description": "Diabetes"}])
		self.assertTrue(standard.StandardFeeImporter().detect(fees))
		self.assertFalse(standard.StandardDiagnosisImporter().detect(fees))
		self.assertTrue(standard.StandardDiagnosisImporter().detect(diagnoses))
		self.assertFalse(standard.StandardFeeImporter().detect(diagnoses))
		self.assertFalse(standard.StandardFeeImporter().detect(SourceFile("fees.pdf", fees.content)))

	def test_a_spreadsheet_saved_as_windows_1252_is_read(self):
		file = SourceFile("fees.csv", "code,description,fee\nA1,Évaluation,1\n".encode("cp1252"))
		self.assertEqual(standard.StandardFeeImporter().parse(file).fee_codes[0].description, "Évaluation")

	def test_the_template_is_a_header_row(self):
		self.assertEqual(
			standard.template(FEE).decode().strip().split(",")[:3], ["code", "description", "fee"]
		)


class TestRules(IntegrationTestCase):
	"""The core rule types, checked without the database."""

	def check(self, rule, line=None, **ctx):
		fee = FeeCode("A100", "Test", fee_cents=1000, rules=(rule,))
		context = BillingContext(service_date=JAN, **ctx)
		return RuleEngine().check(fee, line or BillLineDraft("A100"), context)

	def test_age(self):
		rule = BillingRule("age", {"min": 18, "max": 64})
		self.assertEqual(self.check(rule, birth_date=datetime.date(1990, 6, 1)), [])
		self.assertEqual(self.check(rule, birth_date=datetime.date(2010, 6, 1))[0].severity, ERROR)
		# Turning 65 on the service date is 65.
		self.assertTrue(self.check(rule, birth_date=datetime.date(1961, 1, 1)))
		self.assertTrue(self.check(rule))

	def test_sex(self):
		rule = BillingRule("sex", {"allowed": ["Female"]})
		self.assertEqual(self.check(rule, sex="Female"), [])
		self.assertTrue(self.check(rule, sex="Male"))

	def test_limits_count_history_and_the_same_visit(self):
		rule = BillingRule("max_per_day", {"count": 1})
		self.assertEqual(self.check(rule), [])
		self.assertTrue(self.check(rule, history=PriorBilling(same_day={"A100": 1})))
		self.assertTrue(self.check(rule, other_lines=(BillLineDraft("A100"),)))
		yearly = BillingRule("max_per_year", {"count": 2})
		self.assertEqual(self.check(yearly, history=PriorBilling(same_year={"A100": 1})), [])
		self.assertTrue(self.check(yearly, history=PriorBilling(same_year={"A100": 2})))

	def test_combinations(self):
		self.assertTrue(
			self.check(BillingRule("not_with", {"codes": ["A200"]}), other_lines=(BillLineDraft("a200"),))
		)
		self.assertEqual(
			self.check(BillingRule("requires", {"codes": ["E400"]}), other_lines=(BillLineDraft("E400"),)), []
		)
		self.assertTrue(self.check(BillingRule("requires", {"codes": ["E400"]})))

	def test_time_and_units(self):
		rule = BillingRule("min_minutes", {"minutes": 20})
		long = BillLineDraft("A100", start_time=datetime.time(9, 0), end_time=datetime.time(9, 25))
		short = BillLineDraft("A100", start_time=datetime.time(9, 0), end_time=datetime.time(9, 10))
		self.assertEqual(self.check(rule, long), [])
		self.assertTrue(self.check(rule, short))
		self.assertTrue(self.check(rule))
		self.assertTrue(self.check(BillingRule("max_units", {"units": 2}), BillLineDraft("A100", units=3)))

	def test_a_warning_rule_stays_a_warning_and_its_message_is_used(self):
		rule = BillingRule("sex", {"allowed": ["Female"]}, WARNING, "Usually for female patients")
		issue = self.check(rule, sex="Male")[0]
		self.assertEqual((issue.severity, issue.message), (WARNING, "Usually for female patients"))

	def test_a_rule_nobody_knows_is_reported_not_passed(self):
		issue = self.check(BillingRule("lunar_phase", {}))[0]
		self.assertEqual((issue.rule_type, issue.severity), ("lunar_phase", WARNING))


class TestBillingCodes(IntegrationTestCase):
	"""The service and the pipeline around the standard provider, on Ontario's health plan."""

	def setUp(self):
		ensure_payers()
		self.j = billing_codes.get_jurisdiction("ON")
		clear_code_sets(self.j.payer)

	def load(self, rows, kind=FEE, effective_from=JAN, activate=True):
		file = fee_csv(rows, "fees.csv" if kind == FEE else "diagnoses.csv")
		version = importing.upload(
			self.j.payer, kind, file.filename, file.content, effective_from=effective_from
		)
		self.assertEqual(version.status, importing.STAGED, version.import_error)
		return importing.activate(version.name) if activate else version

	def test_jurisdictions(self):
		self.assertEqual(self.j, billing_codes.Jurisdiction("ON", "OHIP"))
		self.assertEqual(billing_codes.get_jurisdiction(payer="MSI").province, "NS")
		self.assertIn(billing_codes.Jurisdiction("BC", "MSP"), billing_codes.supported_jurisdictions())
		self.assertRaises(UnsupportedJurisdiction, billing_codes.get_jurisdiction, "XX")
		self.assertRaises(UnsupportedJurisdiction, billing_codes.get_jurisdiction, payer="Manulife")

	def test_currency_is_the_sites(self):
		self.assertEqual(FeeCode("A1", "x").currency, frappe.defaults.get_global_default("currency") or "CAD")
		with patch.object(frappe.defaults, "get_global_default", return_value="USD"):
			self.assertEqual(get_currency(), "USD")

	def test_a_code_with_its_own_end_date_stops_within_its_version(self):
		self.load([{"code": "A1", "description": "Ending", "fee": "1", "effective_to": "2026-03-01"}])
		self.assertIsNotNone(billing_codes.get_fee_code(self.j, "A1", datetime.date(2026, 2, 28)))
		self.assertIsNone(billing_codes.get_fee_code(self.j, "A1", datetime.date(2026, 3, 1)))

	def test_rules_and_translations_survive_the_round_trip(self):
		self.load(
			[
				{
					"code": "A1",
					"description": "Assessment",
					"fee": "10",
					"max_per_day": "1",
					"description_fr": "Évaluation",
				}
			]
		)
		fee = billing_codes.get_fee_code(self.j, "A1", JAN)
		self.assertEqual(fee.rules[0].rule_type, "max_per_day")
		self.assertEqual(fee.rules[0].parameters, {"count": 1})
		self.assertEqual(
			billing_codes.get_fee_code(self.j, "A1", JAN, language="fr").description, "Évaluation"
		)
		self.assertEqual(billing_codes.search_fee_codes(self.j, text="évaluation", service_date=JAN).total, 1)
		self.assertEqual(
			billing_codes.search_fee_codes(self.j, text="A1", service_date=JAN, language="fr")
			.items[0]
			.description,
			"Évaluation",
		)

	def test_search_treats_wildcards_as_text(self):
		self.load([{"code": "A1", "description": "Assessment", "fee": "1"}])
		self.assertEqual(billing_codes.search_fee_codes(self.j, text="%", service_date=JAN).total, 0)
		self.assertEqual(billing_codes.search_fee_codes(self.j, text="", service_date=JAN).total, 1)

	def test_a_file_with_errors_fails_with_the_reasons(self):
		file = fee_csv(
			[{"code": "A1", "description": "x", "fee": "1"}, {"code": "a1", "description": "y", "fee": "2"}]
		)
		version = importing.upload(self.j.payer, FEE, file.filename, file.content)
		self.assertEqual(version.status, importing.FAILED)
		self.assertIn("more than once", version.import_error)
		# A diagnosis file uploaded as fees is not read.
		file = fee_csv([{"code": "250", "description": "Diabetes"}])
		self.assertEqual(
			importing.upload(self.j.payer, FEE, file.filename, file.content).status, importing.FAILED
		)

	def test_activation_needs_an_effective_date(self):
		version = self.load(
			[{"code": "A1", "description": "x", "fee": "1"}], effective_from=None, activate=False
		)
		self.assertRaises(frappe.ValidationError, importing.activate, version.name)
		self.assertEqual(importing.activate(version.name, JUL).effective_from, JUL)

	def test_a_version_is_fixed_once_in_effect_and_never_deleted(self):
		staged = self.load([{"code": "A1", "description": "x", "fee": "1"}], activate=False)
		staged.source_name = "Corrected name"
		staged.save()
		staged.payer = "MSI"
		self.assertRaises(frappe.ValidationError, staged.save)

		active = importing.activate(staged.name)
		active.source_name = "Changed"
		self.assertRaises(frappe.ValidationError, active.save)
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, repository.VERSION, active.name)
		active.reload()
		active.status = importing.RETIRED
		self.assertRaises(frappe.ValidationError, active.save)

	def test_a_staged_version_can_be_discarded_with_its_codes(self):
		version = self.load(
			[{"code": "A1", "description": "x", "fee": "1", "max_per_day": "1"}], activate=False
		)
		fee = frappe.get_all(repository.TABLES[FEE], filters={"code_set": version.name}, pluck="name")[0]
		frappe.delete_doc(repository.VERSION, version.name)
		self.assertFalse(frappe.db.exists(repository.TABLES[FEE], fee))
		self.assertFalse(frappe.db.exists(repository.RULE, {"parent": fee}))

	def test_codes_cannot_be_edited_or_deleted(self):
		self.load([{"code": "A1", "description": "x", "fee": "1"}])
		fee = frappe.get_doc(repository.TABLES[FEE], {"code": "A1"})
		fee.description = "Changed"
		self.assertRaises(frappe.ValidationError, fee.save)
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, repository.TABLES[FEE], fee.name)

	def test_the_desk_upload_path_stages_the_attached_file(self):
		file = fee_csv([{"code": "A1", "description": "Desk", "fee": "1"}])
		attachment = frappe.get_doc(
			{"doctype": "File", "file_name": file.filename, "content": file.content, "is_private": 1}
		).insert()
		version = frappe.get_doc(
			{"doctype": repository.VERSION, "payer": "OHIP", "kind": FEE, "source_file": attachment.file_url}
		).insert()
		version.reload()
		self.assertEqual(version.status, importing.STAGED, version.import_error)
		self.assertEqual(version.checksum, importing.checksum(file.content))
		self.assertEqual(version.source_name, standard.StandardFeeImporter.source_name)
		duplicate = frappe.get_doc(
			{"doctype": repository.VERSION, "payer": "OHIP", "kind": FEE, "source_file": attachment.file_url}
		)
		self.assertRaises(frappe.DuplicateEntryError, duplicate.insert)

	def test_build_context_from_the_record(self):
		patient = make_patient(date_of_birth="1960-05-01", sex="Male")
		practitioner = make_practitioner(billing={"billing_number": "123456", "specialty_code": "00"})
		frappe.get_doc(
			{
				"doctype": "Billing Claim",
				"patient": patient.name,
				"practitioner": practitioner.name,
				"payer": "OHIP",
				"service_date": "2026-01-01",
				"status": "Draft",
				"items": [{"service_code": "A1", "units": 1, "fee": 10}],
			}
		).insert(ignore_permissions=True)
		ctx = billing_codes.build_context(
			self.j, service_date="2026-01-01", patient=patient.name, practitioner=practitioner.name
		)
		self.assertEqual((ctx.age, ctx.sex), (65, "Male"))
		self.assertEqual((ctx.specialty, ctx.billing_number), ("00", "123456"))
		self.assertEqual(ctx.history.same_day, {"A1": 1})
		self.assertEqual(ctx.history.same_year, {"A1": 1})

		self.load([{"code": "A1", "description": "Once a day", "fee": "10", "max_per_day": "1"}])
		result = billing_codes.validate_bill_line(self.j, BillLineDraft("A1"), ctx)
		self.assertEqual([issue.rule_type for issue in result.issues], ["max_per_day"])

	def test_favourites_and_fee_change_notices(self):
		user = make_user("billing-codes-dr@example.com", ("EMR Physician",))
		practitioner = make_practitioner(user=user)
		self.load(
			[
				{"code": "A1", "description": "Kept", "fee": "10"},
				{"code": "A2", "description": "Dropped", "fee": "20"},
			]
		)
		for order, code in enumerate(("a2", "A1")):
			frappe.get_doc(
				{
					"doctype": "Billing Favourite",
					"practitioner": practitioner.name,
					"payer": "OHIP",
					"code": code,
					"sort_order": order,
				}
			).insert()
		self.assertEqual(
			[fee.code for fee in billing_codes.favourites(self.j, practitioner.name, JAN)], ["A2", "A1"]
		)
		duplicate = {
			"doctype": "Billing Favourite",
			"practitioner": practitioner.name,
			"payer": "OHIP",
			"code": "A1",
		}
		self.assertRaises(frappe.ValidationError, frappe.get_doc(duplicate).insert)

		sent = []
		with patch.object(importing.notify, "notify", side_effect=lambda *args: sent.append(args)):
			self.load([{"code": "A1", "description": "Kept", "fee": "12"}], effective_from=JUL)
		self.assertEqual(len(sent), 1)
		users, text, doctype, _name = sent[0]
		self.assertEqual((users, doctype), ([user], repository.VERSION))
		self.assertIn("A1", text)
		# A2 is gone from July; A1 has its new fee.
		self.assertEqual(
			[(fee.code, fee.fee_cents) for fee in billing_codes.favourites(self.j, practitioner.name, JUL)],
			[("A1", 1200)],
		)

	def test_resolving_a_code_reference(self):
		self.load([{"code": "A1", "description": "x", "fee": "10"}])
		self.load([{"code": "250", "description": "Diabetes"}], kind=DIAGNOSIS)
		self.assertEqual(billing_codes.resolve(billing_codes.CodeRef(self.j, FEE, "A1", JAN)).fee_cents, 1000)
		self.assertEqual(
			billing_codes.resolve(billing_codes.CodeRef(self.j, DIAGNOSIS, "250", JAN)).description,
			"Diabetes",
		)
