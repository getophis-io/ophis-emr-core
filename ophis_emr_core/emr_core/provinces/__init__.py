"""Provinces and territories, and which one a clinic works in.

A clinic is an ERPNext Branch with a `Clinic` record, and its province decides the health
system it works in. A site can hold clinics in several provinces. Where a document has no
clinic to go by, the site's `clinic_province` (site config) applies, and failing that Ontario.
"""

import importlib
import re
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import getdate

PROVINCES = {
	"AB": "Alberta",
	"BC": "British Columbia",
	"MB": "Manitoba",
	"NB": "New Brunswick",
	"NL": "Newfoundland and Labrador",
	"NS": "Nova Scotia",
	"NT": "Northwest Territories",
	"NU": "Nunavut",
	"ON": "Ontario",
	"PE": "Prince Edward Island",
	"QC": "Quebec",
	"SK": "Saskatchewan",
	"YT": "Yukon",
}

# The time zone most of each province keeps. Parts of some (northwestern Ontario, eastern
# Quebec, most of Nunavut) keep another; such a clinic sets its own on its Clinic record.
TIME_ZONES = {
	"AB": "America/Edmonton",
	"BC": "America/Vancouver",
	"MB": "America/Winnipeg",
	"NB": "America/Moncton",
	"NL": "America/St_Johns",
	"NS": "America/Halifax",
	"NT": "America/Yellowknife",
	"NU": "America/Iqaluit",
	"ON": "America/Toronto",
	"PE": "America/Halifax",
	"QC": "America/Toronto",
	"SK": "America/Regina",
	"YT": "America/Whitehorse",
}

DEFAULT_PROVINCE = "ON"
CACHE_KEY = "ophis_emr_core:clinic_provinces"


def get_site_province() -> str:
	"""The province for documents with no clinic: `clinic_province` in the site config, or Ontario."""
	return frappe.conf.get("clinic_province") or DEFAULT_PROVINCE


def get_clinic_provinces() -> dict[str, str]:
	"""{branch: province} of every clinic, cached until a clinic changes."""
	return frappe.cache.get_value(
		CACHE_KEY,
		lambda: dict(frappe.get_all("Clinic", fields=["branch", "province"], as_list=True)),
	)


def clear_cache():
	frappe.cache.delete_value(CACHE_KEY)


def get_clinic_province(branch: str | None = None) -> str:
	"""The province of the clinic that is this branch.

	Without a branch, the site's only clinic decides when it has just one; otherwise the site's
	province applies.
	"""
	clinics = get_clinic_provinces()
	if branch and branch in clinics:
		return clinics[branch]
	if not branch and len(set(clinics.values())) == 1:
		return next(iter(clinics.values()))
	return get_site_province()


def province_from_text(text: str | None) -> str | None:
	"""A province code from a code or a name, as an Address's state is written."""
	text = (text or "").strip()
	if text.upper() in PROVINCES:
		return text.upper()
	return next((code for code, name in PROVINCES.items() if name.lower() == text.lower()), None)


@dataclass(frozen=True)
class Key:
	"""One value a billing profile holds as a key and value row."""

	key: str
	label: str
	required: bool = False
	pattern: str | None = None
	hint: str = ""

	def check(self, value: str):
		if self.pattern and not re.fullmatch(self.pattern, value):
			frappe.throw(_("{0} {1} is not valid: {2}.").format(self.label, value, self.hint))


@dataclass(frozen=True)
class BillingKey(Key):
	"""One value a practitioner bills a province's plan with.

	`per_clinic` values may differ between the practitioner's clinics in the province: a row
	with a clinic is that clinic's value, a row without one applies to the rest.
	"""

	per_clinic: bool = False


@dataclass(frozen=True)
class PlanKey(Key):
	"""One detail of a patient's coverage by a plan, such as a membership number.

	`patient_field` names the Patient field a value left blank defaults to. `upper` values are
	identifiers, kept in capitals; `date` values are kept as YYYY-MM-DD.
	"""

	patient_field: str | None = None
	upper: bool = False
	date: bool = False

	def normalize(self, value) -> str | None:
		value = str(value).strip() if value else ""
		if not value:
			return None
		if self.date:
			return getdate(value).isoformat()
		return value.upper() if self.upper else value


# Each province's health plan: the `Payer` a patient with a health card from it is covered by.
HEALTH_PLANS = {
	"AB": "AHCIP",
	"BC": "MSP",
	"MB": "Manitoba Health",
	"NB": "NB Medicare",
	"NL": "MCP",
	"NS": "MSI",
	"NT": "NWT Health Care Plan",
	"NU": "Nunavut Health Care Plan",
	"ON": "OHIP",
	"PE": "Health PEI",
	"QC": "RAMQ",
	"SK": "Saskatchewan Health",
	"YT": "YHCIP",
}

# A patient's coverage by any province's health plan: their health card. A province module can
# give its own (`PLAN_KEYS`), with its formats.
HEALTH_CARD_KEYS = [
	PlanKey("health_card_number", "Health Card Number", required=True, upper=True),
	PlanKey("version_code", "Version Code", upper=True),
]

# Provinces the core knows more of, by module in this package: the details of a patient's
# coverage by its plan (`PLAN_KEYS`), and what a practitioner bills it with (`BILLING_KEYS`).
BILLING_MODULES = {"ON": "ontario"}


def get_province_module(province: str | None):
	module = BILLING_MODULES.get(province or "")
	return importlib.import_module(f"{__name__}.{module}") if module else None


def get_billing_keys(province: str | None) -> dict[str, BillingKey]:
	"""{key: BillingKey} for billing a province's plan, or {} where it is not supported yet."""
	module = get_province_module(province)
	return {key.key: key for key in module.BILLING_KEYS} if module else {}


def get_plan_keys(province: str | None) -> dict[str, PlanKey]:
	"""{key: PlanKey} of a patient's coverage by the province's health plan, in order."""
	if province not in PROVINCES:
		return {}
	module = get_province_module(province)
	return {key.key: key for key in getattr(module, "PLAN_KEYS", None) or HEALTH_CARD_KEYS}


def get_health_plan(province: str | None) -> str | None:
	"""The name of the province's health plan (its `Payer`)."""
	return HEALTH_PLANS.get(province or "")


def get_health_plans() -> dict[str, str]:
	"""{province: health plan} of every province."""
	return dict(HEALTH_PLANS)
