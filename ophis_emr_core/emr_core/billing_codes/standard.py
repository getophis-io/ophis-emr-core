"""The standard provider: fee and diagnosis codes from CSV files in the core's own layout.

It serves every province that has no provider of its own, so a clinic can load its province's
codes from a spreadsheet before anyone writes an importer for the ministry's files. It runs the
core rules and prices fee times units, nothing province-specific.

A file has a header row; column names are case-insensitive, and an unknown column is an error
(a misspelt rule column must not be dropped silently). Dates are YYYY-MM-DD and `effective_to`
is the first day a code no longer applies. Money is in dollars ("33.70", "$1,250.00"); a blank
fee, or "IC", means no set fee. Lists ("A001;A003") are separated by ";", "|" or ",". A
`description_<lang>` column (`description_fr`) gives the text in another language.

Fee codes:

    code, description, fee                          required
    short_description, section, unit_type, requires_diagnosis (Y/N),
    professional_fee, technical_fee, assistant_fee, anaesthesia_fee,
    effective_from, effective_to,
    min_age, max_age, sex (F;M), specialties, locations, max_per_day, max_per_year,
    not_with, requires, min_minutes, max_units      rules (see rules.py)

Diagnosis codes:

    code, description                               required
    category, billable (Y/N, default Y), effective_from, effective_to
"""

import csv
import io
import re
from decimal import Decimal, InvalidOperation

from frappe.utils import getdate

from ophis_emr_core.emr_core.billing_codes.providers import (
	Capabilities,
	CodeProvider,
	CodeSetImporter,
	ParseError,
	SourceFile,
)
from ophis_emr_core.emr_core.billing_codes.types import (
	DIAGNOSIS,
	FEE,
	LANGUAGES,
	BillingRule,
	Description,
	DiagnosisCode,
	FeeCode,
	FeeComponents,
	ParsedCodeSet,
)

FEE_COLUMNS = (
	"code",
	"description",
	"fee",
	"short_description",
	"section",
	"unit_type",
	"requires_diagnosis",
	"professional_fee",
	"technical_fee",
	"assistant_fee",
	"anaesthesia_fee",
	"effective_from",
	"effective_to",
	"min_age",
	"max_age",
	"sex",
	"specialties",
	"locations",
	"max_per_day",
	"max_per_year",
	"not_with",
	"requires",
	"min_minutes",
	"max_units",
)
DIAGNOSIS_COLUMNS = ("code", "description", "category", "billable", "effective_from", "effective_to")
TRANSLATION = re.compile(r"(short_description|description)_([a-z]{2})")
TRUE, FALSE = {"y", "yes", "true", "1"}, {"n", "no", "false", "0", ""}
SEXES = {"f": "Female", "m": "Male"}
NO_SET_FEE = {"", "ic"}


class LineError(Exception):
	pass


def _list(value: str) -> list[str]:
	return [part.strip() for part in re.split(r"[;|,]", value or "") if part.strip()]


def _flag(value: str, column: str) -> bool:
	value = (value or "").strip().lower()
	if value in TRUE:
		return True
	if value in FALSE:
		return False
	raise LineError(f"{column} must be Y or N, not {value!r}")


def _int(value: str, column: str) -> int | None:
	value = (value or "").strip()
	if not value:
		return None
	if not value.isdigit():
		raise LineError(f"{column} must be a whole number, not {value!r}")
	return int(value)


def _date(value: str, column: str):
	value = (value or "").strip()
	if not value:
		return None
	if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
		raise LineError(f"{column} must be a date as YYYY-MM-DD, not {value!r}")
	return getdate(value)


def cents(value: str, column: str = "fee") -> int | None:
	"""Dollars as written in a spreadsheet, in cents. None for a blank or "IC"."""
	text = (value or "").strip().replace("$", "").replace(",", "").replace(" ", "")
	if text.lower() in NO_SET_FEE:
		return None
	try:
		amount = Decimal(text)
	except InvalidOperation:
		raise LineError(f"{column} must be an amount in dollars, not {value!r}") from None
	if amount < 0 or amount != amount.quantize(Decimal("0.01")):
		raise LineError(f"{column} must be a positive amount with at most 2 decimal places, not {value!r}")
	return int(amount * 100)


def _translations(row: dict) -> tuple[Description, ...]:
	languages = sorted({m.group(2) for column in row if (m := TRANSLATION.fullmatch(column))})
	return tuple(
		Description(
			language,
			row[f"description_{language}"].strip(),
			(row.get(f"short_description_{language}") or "").strip() or None,
		)
		for language in languages
		if (row.get(f"description_{language}") or "").strip()
	)


def _rules(row: dict) -> tuple[BillingRule, ...]:
	rules = []
	low, high = _int(row.get("min_age"), "min_age"), _int(row.get("max_age"), "max_age")
	if low is not None or high is not None:
		rules.append(
			BillingRule(
				"age", {key: value for key, value in (("min", low), ("max", high)) if value is not None}
			)
		)
	if row.get("sex", "").strip():
		rules.append(
			BillingRule("sex", {"allowed": [SEXES.get(s.lower(), s.title()) for s in _list(row["sex"])]})
		)
	for column, rule_type in (("specialties", "specialty"), ("locations", "location")):
		if row.get(column, "").strip():
			rules.append(BillingRule(rule_type, {"allowed": _list(row[column])}))
	for column, key in (
		("max_per_day", "count"),
		("max_per_year", "count"),
		("min_minutes", "minutes"),
		("max_units", "units"),
	):
		value = _int(row.get(column), column)
		if value is not None:
			rules.append(BillingRule(column, {key: value}))
	for column in ("not_with", "requires"):
		if row.get(column, "").strip():
			rules.append(BillingRule(column, {"codes": [code.upper() for code in _list(row[column])]}))
	return tuple(rules)


class StandardImporter(CodeSetImporter):
	columns: tuple[str, ...]
	required: tuple[str, ...]

	def _reader(self, file: SourceFile):
		reader = csv.DictReader(io.StringIO(file.text()))
		reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames or []]
		return reader

	def detect(self, file: SourceFile) -> bool:
		if file.extension not in ("csv", "txt"):
			return False
		header = set(self._reader(file).fieldnames)
		return set(self.required) <= header and self.matches(header)

	def matches(self, header: set[str]) -> bool:
		return True

	def parse(self, file: SourceFile) -> ParsedCodeSet:
		reader = self._reader(file)
		unknown = [
			column
			for column in reader.fieldnames
			if column not in self.columns
			and not ((m := TRANSLATION.fullmatch(column)) and m.group(2) in LANGUAGES)
		]
		if unknown:
			raise ParseError([f"Unknown column(s): {', '.join(unknown)}"])
		parsed, errors = ParsedCodeSet(kind=self.kind), []
		for line, row in enumerate(reader, 2):
			row = {key: (value or "") for key, value in row.items() if key}
			if not any(value.strip() for value in row.values()):
				continue
			try:
				parsed.codes.append(self.parse_row(row))
			except LineError as error:
				errors.append(f"Line {line}: {error}")
		if errors:
			raise ParseError(errors)
		return parsed

	def parse_row(self, row: dict):
		raise NotImplementedError


class StandardFeeImporter(StandardImporter):
	kind = FEE
	source_name = "Fee codes (standard CSV)"
	columns = FEE_COLUMNS
	required = ("code", "description", "fee")

	def parse_row(self, row: dict) -> FeeCode:
		return FeeCode(
			code=row["code"].strip().upper(),
			description=row["description"].strip(),
			short_description=row.get("short_description", "").strip() or None,
			section=row.get("section", "").strip() or None,
			fee_cents=cents(row["fee"]),
			components=FeeComponents(
				**{
					part: cents(row.get(f"{part}_fee"), f"{part}_fee")
					for part in ("professional", "technical", "assistant", "anaesthesia")
				}
			),
			unit_type=row.get("unit_type", "").strip() or None,
			requires_diagnosis=_flag(row.get("requires_diagnosis"), "requires_diagnosis"),
			effective_from=_date(row.get("effective_from"), "effective_from"),
			effective_to=_date(row.get("effective_to"), "effective_to"),
			rules=_rules(row),
			descriptions=_translations(row),
		)


class StandardDiagnosisImporter(StandardImporter):
	kind = DIAGNOSIS
	source_name = "Diagnosis codes (standard CSV)"
	columns = DIAGNOSIS_COLUMNS
	required = ("code", "description")

	def matches(self, header: set[str]) -> bool:
		return "fee" not in header

	def parse_row(self, row: dict) -> DiagnosisCode:
		billable = row.get("billable", "").strip()
		return DiagnosisCode(
			code=row["code"].strip().upper(),
			description=row["description"].strip(),
			category=row.get("category", "").strip() or None,
			is_billable=_flag(billable, "billable") if billable else True,
			effective_from=_date(row.get("effective_from"), "effective_from"),
			effective_to=_date(row.get("effective_to"), "effective_to"),
			descriptions=_translations(row),
		)


def template(kind: str) -> bytes:
	"""An empty file in the standard layout, header row only, for an app to offer as a download."""
	columns = FEE_COLUMNS if kind == FEE else DIAGNOSIS_COLUMNS
	return (",".join(columns) + "\n").encode()


class StandardProvider(CodeProvider):
	capabilities = Capabilities(pricing=True, premiums=False, languages=LANGUAGES)

	def importers(self) -> list[CodeSetImporter]:
		return [StandardFeeImporter(), StandardDiagnosisImporter()]
