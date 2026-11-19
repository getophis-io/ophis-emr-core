"""The tests every billing code provider must pass, whichever province it serves.

Mix `ProviderContract` into an `IntegrationTestCase` for a provider, set the province and the
sample codes, and write the province's files from the rows the contract asks for:

    class TestMyProvider(ProviderContract, IntegrationTestCase):
        province = "ON"
        fee_codes = ("A001A", "A003A", "A007A")
        diagnosis_codes = ("250", "401")

        def render_fee_file(self, rows): ...        # -> SourceFile in the province's layout
        def render_diagnosis_file(self, rows): ...

Each row is a dict of `code`, `description`, `fee_cents` (None for no set fee) and
`requires_diagnosis` (fee codes), or `code`, `description` and `billable` (diagnosis codes).
The contract only uses the service and the import pipeline, never the provider directly.
"""

import datetime

import frappe

from ophis_emr_core.emr_core import billing_codes
from ophis_emr_core.emr_core.billing_codes import importing, repository
from ophis_emr_core.emr_core.billing_codes.providers import SourceFile
from ophis_emr_core.emr_core.billing_codes.types import DIAGNOSIS, ERROR, FEE, BillingContext, BillLineDraft
from ophis_emr_core.install import ensure_payers

JAN = datetime.date(2026, 1, 1)
JUL = datetime.date(2026, 7, 1)


def clear_code_sets(payer: str):
	"""Remove every code set version of a health plan, inside a test's transaction."""
	for name in frappe.get_all(repository.VERSION, filters={"payer": payer}, pluck="name"):
		importing.delete_rows(name)
	frappe.db.delete(repository.VERSION, {"payer": payer})
	frappe.db.delete("Billing Favourite", {"payer": payer})
	for kind in (FEE, DIAGNOSIS):
		repository.clear_cache(payer, kind)


class ProviderContract:
	province: str
	fee_codes: tuple[str, str, str]
	diagnosis_codes: tuple[str, str]

	def render_fee_file(self, rows: list[dict]) -> SourceFile:
		raise NotImplementedError

	def render_diagnosis_file(self, rows: list[dict]) -> SourceFile:
		raise NotImplementedError

	# Helpers

	def setUp(self):
		super().setUp()
		ensure_payers()
		self.j = billing_codes.get_jurisdiction(self.province)
		clear_code_sets(self.j.payer)

	def fee_rows(self, first_fee=1000, drop_last=False, tag=""):
		a, b, c = self.fee_codes
		rows = [
			{
				"code": a,
				"description": f"General assessment{tag}",
				"fee_cents": first_fee,
				"requires_diagnosis": True,
			},
			{
				"code": b,
				"description": "Intermediate assessment",
				"fee_cents": 3370,
				"requires_diagnosis": False,
			},
			{"code": c, "description": "Counselling session", "fee_cents": None, "requires_diagnosis": False},
		]
		return rows[:2] if drop_last else rows

	def diagnosis_rows(self):
		billable, other = self.diagnosis_codes
		return [
			{"code": billable, "description": "Diabetes mellitus", "billable": True},
			{"code": other, "description": "Essential hypertension", "billable": False},
		]

	def upload(self, kind, file: SourceFile, effective_from=None):
		return importing.upload(
			self.j.payer, kind, file.filename, file.content, effective_from=effective_from
		)

	def activate_fees(self, effective_from=JAN, **row_options):
		version = self.upload(FEE, self.render_fee_file(self.fee_rows(**row_options)), effective_from)
		self.assertEqual(version.status, importing.STAGED, version.import_error)
		return importing.activate(version.name)

	def activate_diagnoses(self, effective_from=JAN):
		version = self.upload(DIAGNOSIS, self.render_diagnosis_file(self.diagnosis_rows()), effective_from)
		self.assertEqual(version.status, importing.STAGED, version.import_error)
		return importing.activate(version.name)

	# The contract

	def test_contract_a_staged_version_is_not_in_effect(self):
		version = self.upload(FEE, self.render_fee_file(self.fee_rows()), JAN)
		self.assertEqual(version.status, importing.STAGED, version.import_error)
		self.assertEqual(version.code_count, 3)
		self.assertIsNone(billing_codes.get_fee_code(self.j, self.fee_codes[0], JAN))

	def test_contract_codes_resolve_from_the_effective_date(self):
		self.activate_fees()
		fee = billing_codes.get_fee_code(self.j, self.fee_codes[0].lower(), JAN + datetime.timedelta(days=31))
		self.assertEqual(fee.code, self.fee_codes[0])
		self.assertEqual(fee.description, "General assessment")
		self.assertEqual(fee.fee_cents, 1000)
		self.assertTrue(fee.requires_diagnosis)
		self.assertIsNone(billing_codes.get_fee_code(self.j, self.fee_codes[2], JAN).fee_cents)
		self.assertIsNone(
			billing_codes.get_fee_code(self.j, self.fee_codes[0], JAN - datetime.timedelta(days=1))
		)

	def test_contract_a_later_version_takes_over_and_retired_codes_still_resolve(self):
		first = self.activate_fees()
		second = self.upload(FEE, self.render_fee_file(self.fee_rows(first_fee=1200, drop_last=True)), JUL)
		diff = frappe.parse_json(second.diff)
		self.assertEqual(diff["against"], first.name)
		self.assertEqual(diff["retired"], [self.fee_codes[2]])
		self.assertEqual(diff["fee_changed"], [{"code": self.fee_codes[0], "old": 1000, "new": 1200}])
		importing.activate(second.name)

		self.assertEqual(frappe.db.get_value(repository.VERSION, first.name, "effective_to"), JUL)
		before, after = datetime.date(2026, 3, 1), datetime.date(2026, 8, 1)
		self.assertEqual(billing_codes.get_fee_code(self.j, self.fee_codes[0], before).fee_cents, 1000)
		self.assertEqual(billing_codes.get_fee_code(self.j, self.fee_codes[0], after).fee_cents, 1200)
		self.assertIsNotNone(billing_codes.get_fee_code(self.j, self.fee_codes[2], before))
		self.assertIsNone(billing_codes.get_fee_code(self.j, self.fee_codes[2], after))

	def test_contract_retiring_a_version_rolls_back_to_the_one_it_replaced(self):
		first = self.activate_fees()
		second = self.activate_fees(first_fee=1500, tag=" (corrected)")
		self.assertEqual(frappe.db.get_value(repository.VERSION, first.name, "status"), importing.RETIRED)
		self.assertEqual(billing_codes.get_fee_code(self.j, self.fee_codes[0], JAN).fee_cents, 1500)

		importing.retire(second.name)
		self.assertEqual(frappe.db.get_value(repository.VERSION, first.name, "status"), importing.ACTIVE)
		self.assertEqual(billing_codes.get_fee_code(self.j, self.fee_codes[0], JAN).fee_cents, 1000)

		# A retired version can be put back.
		importing.activate(second.name)
		self.assertEqual(billing_codes.get_fee_code(self.j, self.fee_codes[0], JAN).fee_cents, 1500)

	def test_contract_the_same_file_twice_is_one_version(self):
		file = self.render_fee_file(self.fee_rows())
		self.assertEqual(self.upload(FEE, file, JAN).name, self.upload(FEE, file, JAN).name)

	def test_contract_search_by_code_or_words(self):
		self.activate_fees()
		page = billing_codes.search_fee_codes(self.j, text=self.fee_codes[0], service_date=JAN)
		self.assertEqual(page.items[0].code, self.fee_codes[0])
		page = billing_codes.search_fee_codes(self.j, text="ASSESS", service_date=JAN, page_length=1)
		self.assertEqual(page.total, 2)
		self.assertEqual(len(page.items), 1)
		self.assertEqual(
			billing_codes.search_fee_codes(self.j, text="general assess", service_date=JAN).total, 1
		)
		self.assertEqual(
			billing_codes.search_fee_codes(self.j, text="nothing like it", service_date=JAN).total, 0
		)
		self.assertEqual(
			billing_codes.search_fee_codes(
				self.j, text="assessment", service_date=JAN - datetime.timedelta(days=1)
			).total,
			0,
		)

	def test_contract_diagnosis_codes(self):
		self.activate_diagnoses()
		billable, other = self.diagnosis_codes
		self.assertEqual(
			billing_codes.get_diagnosis_code(self.j, billable, JAN).description, "Diabetes mellitus"
		)
		self.assertFalse(billing_codes.get_diagnosis_code(self.j, other, JAN).is_billable)
		self.assertEqual(
			billing_codes.search_diagnosis_codes(self.j, text="diabetes", service_date=JAN).items[0].code,
			billable,
		)
		self.assertEqual(
			billing_codes.search_diagnosis_codes(self.j, text="", billable_only=True, service_date=JAN).total,
			1,
		)

	def test_contract_validation_returns_structured_issues(self):
		self.activate_fees()
		self.activate_diagnoses()
		billable, other = self.diagnosis_codes
		ctx = BillingContext(service_date=JAN)
		self.assertTrue(
			billing_codes.validate_bill_line(self.j, BillLineDraft(self.fee_codes[0], (billable,)), ctx).ok
		)

		def issues(line):
			return {
				issue.rule_type
				for issue in billing_codes.validate_bill_line(self.j, line, ctx).issues
				if issue.severity == ERROR
			}

		self.assertEqual(issues(BillLineDraft("NOPE1")), {"unknown_code"})
		self.assertEqual(issues(BillLineDraft(self.fee_codes[0])), {"requires_diagnosis"})
		self.assertEqual(issues(BillLineDraft(self.fee_codes[0], ("999999",))), {"unknown_diagnosis"})
		self.assertEqual(issues(BillLineDraft(self.fee_codes[0], (other,))), {"diagnosis_not_billable"})

	def test_contract_pricing(self):
		self.activate_fees()
		ctx = BillingContext(service_date=JAN)
		self.assertEqual(
			billing_codes.price_bill_line(
				self.j, BillLineDraft(self.fee_codes[1], units=2), ctx
			).amount_cents,
			6740,
		)
		self.assertFalse(billing_codes.price_bill_line(self.j, BillLineDraft(self.fee_codes[2]), ctx).priced)
		self.assertFalse(billing_codes.price_bill_line(self.j, BillLineDraft("NOPE1"), ctx).priced)

	def test_contract_an_unreadable_file_fails_and_writes_nothing(self):
		version = self.upload(FEE, SourceFile("garbage.bin", b"\x00\x01 not a code file"), JAN)
		self.assertEqual(version.status, importing.FAILED)
		self.assertTrue(version.import_error)
		self.assertFalse(frappe.db.count(repository.TABLES[FEE], {"code_set": version.name}))
