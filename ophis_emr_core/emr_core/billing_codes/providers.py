"""What a province implements to plug into the billing code service, and how it is found.

A province is a `CodeProvider`: the importers that read its published files, the dataclasses
of what its codes carry beyond the core fields, its rule engine and its fee calculator. The
code tables, lookups, search and the import pipeline are shared; a provider never reads or
writes them itself.

Providers register under the `billing_code_providers` hook, by province code, with "*" for
every province that has none of its own. The core registers the standard provider (CSV files
in the core's layout, `standard.py`) as "*". Another app adds or replaces one with:

    billing_code_providers = {"ON": "my_app.billing.OntarioProvider"}

The last app to register a province wins.
"""

import dataclasses
import json

import frappe
from frappe import _

from ophis_emr_core.emr_core import provinces
from ophis_emr_core.emr_core.billing_codes.pricing import FeeCalculator
from ophis_emr_core.emr_core.billing_codes.rules import RuleEngine
from ophis_emr_core.emr_core.billing_codes.types import DEFAULT_LANGUAGE, ParsedCodeSet

HOOK = "billing_code_providers"
ANY_PROVINCE = "*"


class UnsupportedJurisdiction(frappe.ValidationError):
	pass


class ParseError(frappe.ValidationError):
	"""A file an importer recognised but could not read. `errors` lists what was wrong, by line."""

	def __init__(self, errors: list[str]):
		self.errors = errors
		super().__init__(
			"; ".join(errors[:20]) + (f" (and {len(errors) - 20} more)" if len(errors) > 20 else "")
		)


@dataclasses.dataclass(frozen=True)
class SourceFile:
	"""A published file, as uploaded."""

	filename: str
	content: bytes

	def text(self) -> str:
		"""The file as text: UTF-8 (with or without a byte order mark), or Windows-1252 as
		spreadsheets save it."""
		try:
			return self.content.decode("utf-8-sig")
		except UnicodeDecodeError:
			return self.content.decode("cp1252", errors="replace")

	@property
	def extension(self) -> str:
		return self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""


class CodeSetImporter:
	"""Reads one kind of published file into a `ParsedCodeSet`, without writing anything."""

	kind: str
	source_name: str

	@property
	def name(self) -> str:
		return f"{type(self).__module__}.{type(self).__qualname__}"

	def detect(self, file: SourceFile) -> bool:
		"""Whether this importer reads the file."""
		raise NotImplementedError

	def parse(self, file: SourceFile) -> ParsedCodeSet:
		"""The file's codes. Raises `ParseError` listing every bad line."""
		raise NotImplementedError


@dataclasses.dataclass(frozen=True)
class Capabilities:
	pricing: bool = True
	premiums: bool = False
	languages: tuple[str, ...] = (DEFAULT_LANGUAGE,)


class CodeProvider:
	"""One province's codes. Subclass it, set what differs, and register it under the hook."""

	# The dataclasses of the province's `extensions` on fee and diagnosis codes, if it has any.
	fee_extension: type | None = None
	diagnosis_extension: type | None = None
	capabilities = Capabilities()

	def __init__(self, province: str):
		self.province = province

	def importers(self) -> list[CodeSetImporter]:
		return []

	def rules(self) -> RuleEngine:
		return RuleEngine()

	def pricing(self) -> FeeCalculator:
		return FeeCalculator()

	def practitioner_details(self, billing_values: dict[str, str]) -> tuple[str | None, str | None]:
		"""(specialty, billing number) from the practitioner's billing values for the province."""
		return billing_values.get("specialty_code"), billing_values.get("billing_number")

	def find_importer(self, file: SourceFile, kind: str) -> CodeSetImporter:
		for importer in self.importers():
			if importer.kind == kind and importer.detect(file):
				return importer
		frappe.throw(
			_("{0} is not a {1} code file this site can read for {2}.").format(
				file.filename, kind, self.province
			),
			ParseError,
		)

	def encode_extension(self, value) -> str | None:
		if value is None:
			return None
		return json.dumps(dataclasses.asdict(value), sort_keys=True, default=str)

	def decode_extension(self, kind_extension: type | None, raw: str | None):
		if not raw or not kind_extension:
			return None
		return kind_extension(**json.loads(raw))


_instances: dict[tuple[str, str], CodeProvider] = {}


def get_provider_path(province: str | None) -> str | None:
	hooks = frappe.get_hooks(HOOK) or {}
	paths = hooks.get(province) or hooks.get(ANY_PROVINCE)
	return paths[-1] if paths else None


def get_provider(province: str | None) -> CodeProvider:
	"""The provider of a province's codes. Raises `UnsupportedJurisdiction` where there is none."""
	path = get_provider_path(province) if province in provinces.PROVINCES else None
	if not path:
		raise UnsupportedJurisdiction(_("Billing codes are not supported for {0}.").format(province or "-"))
	if (province, path) not in _instances:
		_instances[(province, path)] = frappe.get_attr(path)(province)
	return _instances[(province, path)]


def supported_provinces() -> list[str]:
	return [province for province in provinces.PROVINCES if get_provider_path(province)]
