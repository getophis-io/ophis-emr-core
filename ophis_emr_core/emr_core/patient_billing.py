"""How a patient's care is paid for: their billing profiles, one per coverage.

Every patient has an Out of Pocket profile, made with the patient. A patient's health card is
their profile for the health plan of the province that issued it (`provinces.HEALTH_PLANS`; the
`Payer` of that plan), which is primary when added. Publicly funded and private coverages are
added by the clinic. `set_health_card`, `get_health_card` and `find_patients_by_health_card` work
with the card; `add_profile` adds any coverage.

A profile's details are key and value rows (`Patient Billing Profile Value`, encrypted and
blind-indexed). Which keys a profile takes is set in code against its plan: `PLAN_KEYS_BY_PAYER`;
a provincial health plan's by its province (`provinces.get_plan_keys`: the card's number and
version code); and for a payer the code does not know, its type's (`PLAN_KEYS`). The desk suggests
them through `Plan Detail Key`, a virtual DocType that answers from this code. Expiry, the issuing province (a health plan's)
and verification are fields of the profile.

Eligibility is not checked yet: a health card number is taken as coverage by the plan.
"""

import frappe

from ophis_emr_core.emr_core import phi, provinces
from ophis_emr_core.emr_core.doctype.payer.payer import get_health_plan_payer
from ophis_emr_core.emr_core.provinces import PlanKey

DOCTYPE = "Patient Billing Profile"
VALUE_DOCTYPE = "Patient Billing Profile Value"

PROVINCIAL = "Provincial Health Plan"
PUBLIC = "Public Funded"
PRIVATE = "Private"
OUT_OF_POCKET = "Out of Pocket"

# The order coverages are billed in, and in which one becomes primary when the primary one ends.
COVERAGE_ORDER = (PROVINCIAL, PUBLIC, PRIVATE, OUT_OF_POCKET)

ACTIVE = "Active"
INACTIVE = "Inactive"

NOT_CHECKED = "Not Checked"
CARD_NUMBER = "health_card_number"
CARD_VERSION = "version_code"

MEMBERSHIP_NUMBER = PlanKey("membership_number", "Membership Number", required=True, upper=True)
PLAN_CONTRACT_NUMBER = PlanKey("plan_contract_number", "Plan Contract Number", upper=True)
OWNER_NAME = PlanKey("owner_name", "Owner Name", patient_field="patient_name")
EMPLOYER_NAME = PlanKey("employer_name", "Sponsor / Employer Name")

# Each plan's details, by its `Payer`. A provincial health plan's are its province's
# (`provinces.get_plan_keys`): the health card.
PLAN_KEYS_BY_PAYER = {
	"Blue Cross": [MEMBERSHIP_NUMBER, PLAN_CONTRACT_NUMBER, OWNER_NAME],
	"Manulife": [PLAN_CONTRACT_NUMBER, MEMBERSHIP_NUMBER, OWNER_NAME, EMPLOYER_NAME],
	"RBC": [PLAN_CONTRACT_NUMBER, MEMBERSHIP_NUMBER, OWNER_NAME, EMPLOYER_NAME],
}

# A payer the clinic adds, which the code does not know, takes its type's.
PLAN_KEYS = {
	PUBLIC: [MEMBERSHIP_NUMBER, PLAN_CONTRACT_NUMBER, OWNER_NAME],
	PRIVATE: [PLAN_CONTRACT_NUMBER, MEMBERSHIP_NUMBER, OWNER_NAME, EMPLOYER_NAME],
}


class DuplicateHealthCardError(frappe.ValidationError):
	pass


class ProfileChangedError(frappe.ValidationError):
	"""A profile is only made Active or Inactive once added; any other change is a new profile."""


def get_plan_keys(coverage_type: str, payer: str | None = None) -> dict[str, PlanKey]:
	"""{key: PlanKey} the plan of this payer takes, in order: its own, its province's for a health
	plan, or its type's for a payer the code does not know."""
	if coverage_type == PROVINCIAL:
		return provinces.get_plan_keys(payer and frappe.db.get_value("Payer", payer, "province"))
	plan_keys = PLAN_KEYS_BY_PAYER.get(payer) or PLAN_KEYS.get(coverage_type, ())
	return {key.key: key for key in plan_keys}


def get_known_plan_keys() -> dict[str, PlanKey]:
	"""{key: PlanKey} of every key any plan in the code takes, every province's included."""
	keys = {}
	sources = [*PLAN_KEYS_BY_PAYER.values(), *PLAN_KEYS.values()]
	sources += [provinces.get_plan_keys(province).values() for province in provinces.PROVINCES]
	for plan_keys in sources:
		for key in plan_keys:
			keys.setdefault(key.key, key)
	return keys


def get_profiles(patient: str, active_only: bool = True) -> list[dict]:
	"""A patient's profiles, the primary one first, then in billing order. Each has its details,
	decrypted, as `plan_values` {key: value}."""
	filters = {"patient": patient}
	if active_only:
		filters["status"] = ACTIVE
	rows = frappe.get_all(DOCTYPE, filters=filters, fields=["*"], order_by="creation asc")
	rows.sort(key=lambda row: (not row.is_primary, COVERAGE_ORDER.index(row.coverage_type)))
	phi.decrypt_rows(DOCTYPE, rows)
	values = (
		frappe.get_all(
			VALUE_DOCTYPE,
			filters={"parenttype": DOCTYPE, "parent": ("in", [row.name for row in rows])},
			fields=["parent", "key", "value"],
			order_by="idx asc",
		)
		if rows
		else []
	)
	phi.decrypt_rows(VALUE_DOCTYPE, values)
	for row in rows:
		row.plan_values = {value.key: value.value for value in values if value.parent == row.name}
	return rows


def get_primary_profile(patient: str) -> dict | None:
	profiles = get_profiles(patient)
	return profiles[0] if profiles and profiles[0].is_primary else None


def add_profile(
	patient: str,
	coverage_type: str,
	payer: str | None = None,
	values: dict[str, str] | None = None,
	primary: bool = False,
	part_of_creation: bool = False,
	**fields,
):
	"""Give a patient a coverage, with its details as {key: value} (`get_plan_keys`). `fields`:
	expiry_date. A health plan's issuing province is its province.

	`part_of_creation` is for the profiles of a patient being registered: the patient changelog
	records them with the patient instead of as entries of their own.
	"""
	profile = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"patient": patient,
			"coverage_type": coverage_type,
			"payer": payer,
			"is_primary": 1 if primary else 0,
			"plan_values": [{"key": key, "value": value} for key, value in (values or {}).items()],
			**fields,
		}
	)
	profile.flags.part_of_creation = part_of_creation
	return profile.insert(ignore_permissions=True)


def ensure_out_of_pocket(patient: str, part_of_creation: bool = False):
	"""Every patient can pay out of pocket."""
	if not frappe.db.exists(DOCTYPE, {"patient": patient, "coverage_type": OUT_OF_POCKET, "status": ACTIVE}):
		add_profile(patient, OUT_OF_POCKET, part_of_creation=part_of_creation)


# The health card: the patient's active provincial health plan profile.


def get_health_card_profile(patient: str):
	"""The patient's active provincial health plan profile, or None."""
	name = frappe.db.get_value(DOCTYPE, {"patient": patient, "coverage_type": PROVINCIAL, "status": ACTIVE})
	return frappe.get_doc(DOCTYPE, name) if name else None


def get_health_card(patient: str) -> frappe._dict | None:
	"""The patient's health card: {profile, province, number, version, expiry_date,
	verification_status, verified_on, verification_response}, or None."""
	profile = get_health_card_profile(patient)
	if not profile:
		return None
	values = {row.key: row.value for row in profile.plan_values}
	return frappe._dict(
		profile=profile.name,
		province=profile.issuing_province,
		number=values.get(CARD_NUMBER),
		version=values.get(CARD_VERSION),
		expiry_date=profile.expiry_date,
		verification_status=profile.verification_status,
		verified_on=profile.verified_on,
		verification_response=profile.verification_response,
	)


def get_health_cards(patients: list[str]) -> dict[str, frappe._dict]:
	"""{patient: health card} of these patients, for lists. Patients without a card are left out."""
	if not patients:
		return {}
	profiles = frappe.get_all(
		DOCTYPE,
		filters={"patient": ("in", patients), "coverage_type": PROVINCIAL, "status": ACTIVE},
		fields=["name", "patient", "issuing_province", "expiry_date", "verification_status"],
	)
	phi.decrypt_rows(DOCTYPE, profiles)
	values = (
		frappe.get_all(
			VALUE_DOCTYPE,
			filters={"parenttype": DOCTYPE, "parent": ("in", [p.name for p in profiles])},
			fields=["parent", "key", "value"],
		)
		if profiles
		else []
	)
	phi.decrypt_rows(VALUE_DOCTYPE, values)
	cards = {}
	for profile in profiles:
		mine = {v.key: v.value for v in values if v.parent == profile.name}
		cards[profile.patient] = frappe._dict(
			profile=profile.name,
			province=profile.issuing_province,
			number=mine.get(CARD_NUMBER),
			version=mine.get(CARD_VERSION),
			expiry_date=profile.expiry_date,
			verification_status=profile.verification_status,
		)
	return cards


def set_health_card(
	patient: str,
	province: str | None,
	number: str | None,
	version: str | None = None,
	expiry_date: str | None = None,
	part_of_creation: bool = False,
):
	"""Record the patient's health card, or end it (no number).

	A profile is not changed once added, so a card that differs in any way (another number, version,
	expiry or province) ends the current one and starts a new profile, as primary. The same card
	again changes nothing.
	"""
	from ophis_emr_core.emr_core.validators import normalize_health_card_number

	profile = get_health_card_profile(patient)
	if number:
		number = normalize_health_card_number(number, province)
		version = (version or "").strip().upper() or None
		expiry_date = phi.normalize_date(expiry_date)
	if profile:
		current = {row.key: row.value for row in profile.plan_values}
		if (
			profile.issuing_province,
			current.get(CARD_NUMBER),
			current.get(CARD_VERSION),
			profile.expiry_date,
		) == (
			province,
			number,
			version,
			expiry_date,
		):
			return profile
		profile.status = INACTIVE
		profile.save(ignore_permissions=True)
	if not number:
		return None

	values = {CARD_NUMBER: number, CARD_VERSION: version}
	payer = get_health_plan_payer(province)
	if not payer:
		frappe.throw(frappe._("There is no enabled health plan for {0}.").format(province))
	return add_profile(
		patient,
		PROVINCIAL,
		payer=payer,
		values=values,
		primary=True,
		part_of_creation=part_of_creation,
		expiry_date=expiry_date,
	)


def find_profiles_by_health_card(
	number: str, province: str | None = None, exclude: str | None = None
) -> list[str]:
	"""Active provincial health plan profiles holding this card number, matched on its blind index."""
	values = frappe.get_all(
		VALUE_DOCTYPE,
		filters={
			"parenttype": DOCTYPE,
			"key": CARD_NUMBER,
			"value_index": ("in", phi.blind_indexes("value", number)),
		},
		pluck="parent",
	)
	names = [name for name in values if name != exclude]
	if not names:
		return []
	filters = {"name": ("in", names), "coverage_type": PROVINCIAL, "status": ACTIVE}
	if province:
		filters["issuing_province"] = province
	return frappe.get_all(DOCTYPE, filters=filters, pluck="name")


def find_patients_by_health_card(number: str, province: str | None = None) -> list[str]:
	"""Patients whose active health card has this number (any province's, unless `province`)."""
	profiles = find_profiles_by_health_card(number, province)
	if not profiles:
		return []
	return frappe.get_all(DOCTYPE, filters={"name": ("in", profiles)}, pluck="patient", distinct=True)


def choose_primary(patient: str, exclude: str | None = None):
	"""Make the first active profile, in billing order, primary: after the primary one ends."""
	candidates = [row for row in get_profiles(patient) if row.name != exclude]
	if not candidates:
		return
	candidates.sort(key=lambda row: COVERAGE_ORDER.index(row.coverage_type))
	profile = frappe.get_doc(DOCTYPE, candidates[0].name)
	profile.is_primary = 1
	profile.save(ignore_permissions=True)
