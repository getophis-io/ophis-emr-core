"""A second, made-up provider passes the same contract as the standard one, with no core changes.

It reads a fixed-width format nothing else reads, carries a typed extension, adds a rule type
and a premium, and is registered for Yukon through the hook, for these tests only.
"""

from dataclasses import dataclass
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import billing_codes
from ophis_emr_core.emr_core.billing_codes import importing, providers
from ophis_emr_core.emr_core.billing_codes.contract import JAN, ProviderContract
from ophis_emr_core.emr_core.billing_codes.pricing import FeeCalculator
from ophis_emr_core.emr_core.billing_codes.providers import (
	CodeProvider,
	CodeSetImporter,
	ParseError,
	SourceFile,
)
from ophis_emr_core.emr_core.billing_codes.rules import RuleEngine
from ophis_emr_core.emr_core.billing_codes.types import (
	DIAGNOSIS,
	FEE,
	Adjustment,
	BillingContext,
	BillingRule,
	BillLineDraft,
	DiagnosisCode,
	FeeCode,
	ParsedCodeSet,
)

PROVINCE = "YT"


@dataclass(frozen=True)
class TestFeeExtension:
	suffix: str
	night_premium: bool = False


class FixedWidthFeeImporter(CodeSetImporter):
	"""Lines of: code (5), fee in cents (8, blank for none), needs diagnosis (Y/N), night (Y/N), description."""

	kind = FEE
	source_name = "Test fee master"

	def detect(self, file):
		return file.content.startswith(b"TSTFEE")

	def parse(self, file):
		lines = file.text().splitlines()
		parsed = ParsedCodeSet(FEE, source_version=lines[0][6:].strip() or None)
		for number, line in enumerate(lines[1:], 2):
			if len(line) < 16:
				raise ParseError([f"Line {number}: too short"])
			code, fee = line[:5].strip(), line[5:13].strip()
			parsed.fee_codes.append(
				FeeCode(
					code=code,
					description=line[15:].strip(),
					fee_cents=int(fee) if fee else None,
					requires_diagnosis=line[13] == "Y",
					rules=(BillingRule("weekday_only", {}),),
					extensions=TestFeeExtension(suffix=code[-1], night_premium=line[14] == "Y"),
				)
			)
		return parsed


class FixedWidthDiagnosisImporter(CodeSetImporter):
	"""Lines of: code (6), billable (Y/N), description."""

	kind = DIAGNOSIS
	source_name = "Test diagnosis list"

	def detect(self, file):
		return file.content.startswith(b"TSTDX")

	def parse(self, file):
		parsed = ParsedCodeSet(DIAGNOSIS)
		for line in file.text().splitlines()[1:]:
			parsed.diagnosis_codes.append(
				DiagnosisCode(code=line[:6].strip(), is_billable=line[6] == "Y", description=line[7:].strip())
			)
		return parsed


class TestRuleEngine(RuleEngine):
	def __init__(self):
		super().__init__()
		self.handlers["weekday_only"] = lambda rule, fee, line, ctx: (
			"Weekdays only" if ctx.service_date.weekday() >= 5 else None
		)


class TestCalculator(FeeCalculator):
	def adjustments(self, fee, line, ctx, subtotal_cents):
		if fee.extensions and fee.extensions.night_premium and "NIGHT" in line.modifiers:
			return [Adjustment("Night premium", subtotal_cents // 2)]
		return []


class FixedWidthProvider(CodeProvider):
	fee_extension = TestFeeExtension

	def importers(self):
		return [FixedWidthFeeImporter(), FixedWidthDiagnosisImporter()]

	def rules(self):
		return TestRuleEngine()

	def pricing(self):
		return TestCalculator()


real_get_hooks = frappe.get_hooks


def with_test_provider(*args, **kwargs):
	hooks = real_get_hooks(*args, **kwargs)
	if (args[0] if args else kwargs.get("hook")) == providers.HOOK:
		hooks = frappe._dict(hooks or {})
		hooks[PROVINCE] = [f"{__name__}.FixedWidthProvider"]
	return hooks


class TestSecondProvider(ProviderContract, IntegrationTestCase):
	province = PROVINCE
	fee_codes = ("T001A", "T002A", "T003B")
	diagnosis_codes = ("D100", "D200")

	def setUp(self):
		hooks = patch.object(frappe, "get_hooks", side_effect=with_test_provider)
		hooks.start()
		self.addCleanup(hooks.stop)
		super().setUp()

	def render_fee_file(self, rows):
		lines = ["TSTFEE 2026-A"] + [
			f"{row['code']:<5}{'' if row['fee_cents'] is None else row['fee_cents']:>8}"
			f"{'Y' if row['requires_diagnosis'] else 'N'}Y{row['description']}"
			for row in rows
		]
		return SourceFile("fees.dat", "\n".join(lines).encode())

	def render_diagnosis_file(self, rows):
		lines = ["TSTDX"] + [
			f"{row['code']:<6}{'Y' if row['billable'] else 'N'}{row['description']}" for row in rows
		]
		return SourceFile("dx.dat", "\n".join(lines).encode())

	def test_the_province_gets_its_own_provider(self):
		self.assertIsInstance(providers.get_provider(PROVINCE), FixedWidthProvider)
		self.assertNotIsInstance(providers.get_provider("NS"), FixedWidthProvider)

	def test_a_standard_csv_is_not_read_for_this_province(self):
		version = self.upload(FEE, SourceFile("fees.csv", b"code,description,fee\nT001A,x,1\n"), JAN)
		self.assertEqual(version.status, importing.FAILED)

	def test_its_extension_rule_and_premium_come_through_the_service(self):
		version = self.activate_fees()
		self.assertEqual(version.source_version, "2026-A")
		self.assertEqual(version.source_name, FixedWidthFeeImporter.source_name)
		fee = billing_codes.get_fee_code(self.j, "T003B", JAN)
		self.assertEqual(fee.extensions, TestFeeExtension(suffix="B", night_premium=True))

		# 2026-01-03 is a Saturday.
		saturday = BillingContext(service_date=JAN.replace(day=3))
		result = billing_codes.validate_bill_line(self.j, BillLineDraft("T002A"), saturday)
		self.assertEqual([issue.rule_type for issue in result.issues], ["weekday_only"])

		price = billing_codes.price_bill_line(
			self.j, BillLineDraft("T002A", modifiers=("NIGHT",)), BillingContext(service_date=JAN)
		)
		self.assertEqual((price.base_cents, price.amount_cents), (3370, 5055))
		self.assertEqual(price.adjustments[0].label, "Night premium")
