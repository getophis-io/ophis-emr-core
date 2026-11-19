"""The province-neutral types of the billing code service.

Callers only ever see these. What a province adds rides in `extensions`, a dataclass its provider
declares (`CodeProvider.fee_extension`, `diagnosis_extension`), never a free-form dictionary.

Money is integer cents, in the site's currency (`get_currency`). Effective dates are half-open: a code or a code set
applies from `effective_from` up to, but not on, `effective_to`; None means open-ended.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

import frappe

# Only where the site has no default currency yet (before ERPNext's setup wizard).
FALLBACK_CURRENCY = "CAD"


def get_currency() -> str:
	"""The site's default currency, set by ERPNext's setup wizard (Global Defaults)."""
	return frappe.defaults.get_global_default("currency") or FALLBACK_CURRENCY


FEE = "Fee"
DIAGNOSIS = "Diagnosis"
KINDS = (FEE, DIAGNOSIS)

ERROR = "Error"
WARNING = "Warning"

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "fr")


@dataclass(frozen=True)
class Jurisdiction:
	"""Who a code belongs to: a province and the payer whose schedule it is (its health plan)."""

	province: str
	payer: str


@dataclass(frozen=True)
class Description:
	"""A code's text in one language."""

	language: str
	description: str
	short_description: str | None = None


@dataclass(frozen=True)
class FeeComponents:
	"""The parts a fee splits into, in cents, where the schedule gives them."""

	professional: int | None = None
	technical: int | None = None
	assistant: int | None = None
	anaesthesia: int | None = None

	def as_dict(self) -> dict[str, int]:
		return {key: value for key, value in self.__dict__.items() if value is not None}


@dataclass(frozen=True)
class BillingRule:
	"""A machine-readable restriction on billing a fee code.

	`rule_type` is one of `rules.CORE_RULES` (age, sex, limits, combinations...) or a type the
	province's rule engine knows. `parameters` are the rule's values, as documented with its type.
	"""

	rule_type: str
	parameters: dict[str, Any] = field(default_factory=dict)
	severity: str = ERROR
	message: str | None = None


@dataclass(frozen=True)
class FeeCode:
	"""A billable service. `fee_cents` is None where the fee is set case by case (independent
	consideration) or depends on what the provider's calculator knows (specialty)."""

	code: str
	description: str
	short_description: str | None = None
	section: str | None = None
	fee_cents: int | None = None
	components: FeeComponents = field(default_factory=FeeComponents)
	unit_type: str | None = None
	requires_diagnosis: bool = False
	effective_from: datetime.date | None = None
	effective_to: datetime.date | None = None
	rules: tuple[BillingRule, ...] = ()
	descriptions: tuple[Description, ...] = ()
	extensions: Any = None
	# Set when read from a code set; empty while being imported.
	code_set: str | None = None

	@property
	def currency(self) -> str:
		return get_currency()


@dataclass(frozen=True)
class DiagnosisCode:
	"""A diagnosis code a claim can carry. The province's code is the key; any cross-reference
	to ICD-9 or ICD-10-CA belongs in the province's extension."""

	code: str
	description: str
	category: str | None = None
	is_billable: bool = True
	effective_from: datetime.date | None = None
	effective_to: datetime.date | None = None
	descriptions: tuple[Description, ...] = ()
	extensions: Any = None
	code_set: str | None = None


@dataclass(frozen=True)
class CodeRef:
	"""What a bill line keeps, so a claim can always be resolved against the schedule that applied."""

	jurisdiction: Jurisdiction
	kind: str
	code: str
	service_date: datetime.date


@dataclass(frozen=True)
class SearchQuery:
	"""A search by code or words: "A007", "diabetes" and "diab mell" all work.

	Every word must match the code's start or appear in its description (any language).
	"""

	text: str = ""
	service_date: datetime.date | None = None
	section: str | None = None
	billable_only: bool = False
	language: str | None = None
	start: int = 0
	page_length: int = 20


@dataclass(frozen=True)
class Page:
	items: list
	total: int
	start: int
	page_length: int


@dataclass(frozen=True)
class BillLineDraft:
	"""One service on a bill, before it is billed. `modifiers` are the province's own (suffixes,
	premium codes); its provider checks them."""

	fee_code: str
	diagnosis_codes: tuple[str, ...] = ()
	units: int | None = None
	start_time: datetime.time | None = None
	end_time: datetime.time | None = None
	modifiers: tuple[str, ...] = ()

	@property
	def minutes(self) -> int | None:
		if not (self.start_time and self.end_time):
			return None
		start = self.start_time.hour * 60 + self.start_time.minute
		end = self.end_time.hour * 60 + self.end_time.minute
		return end - start if end >= start else None


@dataclass(frozen=True)
class PriorBilling:
	"""How often codes were already billed for the patient: {code: count} on the service date
	and in its calendar year, not counting the lines being checked."""

	same_day: dict[str, int] = field(default_factory=dict)
	same_year: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class BillingContext:
	"""What a bill line is checked and priced against."""

	service_date: datetime.date
	birth_date: datetime.date | None = None
	sex: str | None = None
	practitioner: str | None = None
	specialty: str | None = None
	billing_number: str | None = None
	location: str | None = None
	other_lines: tuple[BillLineDraft, ...] = ()
	history: PriorBilling = field(default_factory=PriorBilling)

	@property
	def age(self) -> int | None:
		"""The patient's age in whole years on the service date."""
		if not self.birth_date:
			return None
		day = self.service_date
		return (
			day.year
			- self.birth_date.year
			- ((day.month, day.day) < (self.birth_date.month, self.birth_date.day))
		)


@dataclass(frozen=True)
class Issue:
	rule_type: str
	severity: str
	message: str


@dataclass(frozen=True)
class ValidationResult:
	issues: tuple[Issue, ...] = ()

	@property
	def ok(self) -> bool:
		return not any(issue.severity == ERROR for issue in self.issues)


@dataclass(frozen=True)
class Adjustment:
	"""A premium, reduction or differential the province's calculator applies, in cents."""

	label: str
	amount_cents: int


@dataclass(frozen=True)
class PriceResult:
	"""What a bill line pays. `amount_cents` is None when it cannot be priced from the schedule;
	`message` says why."""

	amount_cents: int | None
	base_cents: int | None = None
	units: int = 1
	adjustments: tuple[Adjustment, ...] = ()
	message: str | None = None

	@property
	def currency(self) -> str:
		return get_currency()

	@property
	def priced(self) -> bool:
		return self.amount_cents is not None


@dataclass
class ParsedCodeSet:
	"""What an importer reads from a published file, before anything is written.

	The code set's own details are filled in where the file carries them; what the person
	uploading gives on the version wins.
	"""

	kind: str
	fee_codes: list[FeeCode] = field(default_factory=list)
	diagnosis_codes: list[DiagnosisCode] = field(default_factory=list)
	source_name: str | None = None
	source_version: str | None = None
	published_on: datetime.date | None = None
	effective_from: datetime.date | None = None
	language: str = DEFAULT_LANGUAGE

	@property
	def codes(self) -> list:
		return self.fee_codes if self.kind == FEE else self.diagnosis_codes
