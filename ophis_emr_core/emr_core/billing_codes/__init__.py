"""The billing code service: fee and diagnosis codes of each province's health plan.

The rest of the record, and the apps built on it, only ever call these functions. They take a
`Jurisdiction` (a province and its health plan, `get_jurisdiction`) and answer as of a service
date in the province-neutral types of `types.py`, whichever province's provider is behind them:

    j = billing_codes.get_jurisdiction(clinic="Downtown")
    billing_codes.search_fee_codes(j, text="assessment")
    billing_codes.get_diagnosis_code(j, "250", service_date)
    ctx = billing_codes.build_context(j, service_date=day, patient=p, practitioner=dr, clinic=c)
    billing_codes.validate_bill_line(j, BillLineDraft("A007", ("250",)), ctx)
    billing_codes.price_bill_line(j, BillLineDraft("A007", ("250",)), ctx)

Code files are loaded through `importing` (upload, stage, review, activate, retire). How a
province plugs in is in `providers`.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import getdate

from ophis_emr_core.emr_core import billing_profiles, provinces
from ophis_emr_core.emr_core.billing_codes import repository
from ophis_emr_core.emr_core.billing_codes.providers import (
	UnsupportedJurisdiction,
	get_provider,
	supported_provinces,
)
from ophis_emr_core.emr_core.billing_codes.types import (
	DIAGNOSIS,
	ERROR,
	FEE,
	BillingContext,
	BillLineDraft,
	CodeRef,
	DiagnosisCode,
	FeeCode,
	Issue,
	Jurisdiction,
	Page,
	PriceResult,
	PriorBilling,
	SearchQuery,
	ValidationResult,
)
from ophis_emr_core.emr_core.doctype.payer.payer import PROVINCIAL, get_health_plan_payer

# Claims in these states do not count towards a code's limits.
NOT_BILLED = ("Cancelled", "Rejected")


def get_jurisdiction(
	province: str | None = None, *, payer: str | None = None, clinic: str | None = None
) -> Jurisdiction:
	"""The jurisdiction of a province's health plan, named by province, health plan or clinic.

	Raises `UnsupportedJurisdiction` where the province has no enabled health plan or no provider.
	"""
	if payer:
		values = frappe.db.get_value("Payer", payer, ["payer_type", "province"], as_dict=True)
		if not values or values.payer_type != PROVINCIAL:
			raise UnsupportedJurisdiction(_("{0} is not a provincial health plan.").format(payer))
		province = values.province
	elif not province:
		province = provinces.get_clinic_province(clinic)
	payer = payer or get_health_plan_payer(province)
	if not payer:
		raise UnsupportedJurisdiction(_("{0} has no enabled health plan.").format(province))
	get_provider(province)
	return Jurisdiction(province, payer)


def supported_jurisdictions() -> list[Jurisdiction]:
	"""Every province with a provider and an enabled health plan."""
	return [
		Jurisdiction(province, payer)
		for province in supported_provinces()
		if (payer := get_health_plan_payer(province))
	]


def _day(service_date) -> datetime.date:
	return getdate(service_date) if service_date else getdate()


def get_fee_code(
	j: Jurisdiction, code: str, service_date=None, language: str | None = None
) -> FeeCode | None:
	"""The fee code as it stood on the service date (today by default), or None."""
	found = repository.get_code(get_provider(j.province), j.payer, FEE, code, _day(service_date))
	return repository.in_language(found, language) if found and language else found


def get_diagnosis_code(
	j: Jurisdiction, code: str, service_date=None, language: str | None = None
) -> DiagnosisCode | None:
	found = repository.get_code(get_provider(j.province), j.payer, DIAGNOSIS, code, _day(service_date))
	return repository.in_language(found, language) if found and language else found


def search_fee_codes(j: Jurisdiction, query: SearchQuery | None = None, **kwargs) -> Page:
	"""Fee codes matching a code or words, as of a date. `kwargs` are `SearchQuery` fields."""
	return repository.search(get_provider(j.province), j.payer, FEE, query or SearchQuery(**kwargs))


def search_diagnosis_codes(j: Jurisdiction, query: SearchQuery | None = None, **kwargs) -> Page:
	return repository.search(get_provider(j.province), j.payer, DIAGNOSIS, query or SearchQuery(**kwargs))


def resolve(ref: CodeRef) -> FeeCode | DiagnosisCode | None:
	"""The code a bill line refers to, as it stood on the line's service date."""
	lookup = get_fee_code if ref.kind == FEE else get_diagnosis_code
	return lookup(ref.jurisdiction, ref.code, ref.service_date)


def validate_bill_line(j: Jurisdiction, line: BillLineDraft, ctx: BillingContext) -> ValidationResult:
	"""Every problem with a bill line: its codes on the service date, then its code's rules."""
	fee = get_fee_code(j, line.fee_code, ctx.service_date)
	if not fee:
		return ValidationResult(
			(
				Issue(
					"unknown_code",
					ERROR,
					_("{0} is not a {1} fee code on {2}.").format(line.fee_code, j.payer, ctx.service_date),
				),
			)
		)
	issues = []
	if line.units is not None and line.units < 1:
		issues.append(Issue("units", ERROR, _("Units must be at least 1.")))
	if fee.requires_diagnosis and not line.diagnosis_codes:
		issues.append(Issue("requires_diagnosis", ERROR, _("{0} needs a diagnosis code.").format(fee.code)))
	for code in line.diagnosis_codes:
		diagnosis = get_diagnosis_code(j, code, ctx.service_date)
		if not diagnosis:
			issues.append(
				Issue(
					"unknown_diagnosis",
					ERROR,
					_("{0} is not a {1} diagnosis code on {2}.").format(code, j.payer, ctx.service_date),
				)
			)
		elif not diagnosis.is_billable:
			issues.append(
				Issue("diagnosis_not_billable", ERROR, _("Diagnosis code {0} cannot be billed.").format(code))
			)
	issues.extend(get_provider(j.province).rules().check(fee, line, ctx))
	return ValidationResult(tuple(issues))


def price_bill_line(j: Jurisdiction, line: BillLineDraft, ctx: BillingContext) -> PriceResult:
	"""What a bill line pays under the schedule in effect on the service date."""
	fee = get_fee_code(j, line.fee_code, ctx.service_date)
	if not fee:
		return PriceResult(
			None,
			message=_("{0} is not a {1} fee code on {2}.").format(line.fee_code, j.payer, ctx.service_date),
		)
	return get_provider(j.province).pricing().price(fee, line, ctx)


def favourites(j: Jurisdiction, practitioner: str, service_date=None) -> list[FeeCode]:
	"""The practitioner's quick-pick fee codes for the health plan that are in effect on the date."""
	day = _day(service_date)
	codes = frappe.get_all(
		"Billing Favourite",
		filters={"practitioner": practitioner, "payer": j.payer},
		pluck="code",
		order_by="sort_order asc, code asc",
	)
	return [fee for code in codes if (fee := get_fee_code(j, code, day))]


def build_context(
	j: Jurisdiction,
	*,
	service_date,
	patient: str | None = None,
	practitioner: str | None = None,
	clinic: str | None = None,
	location: str | None = None,
	other_lines: tuple[BillLineDraft, ...] = (),
	exclude_claim: str | None = None,
) -> BillingContext:
	"""A billing context from the record: the patient's age and sex, the practitioner's billing
	values for the province, and what was already billed for the patient that day and year
	(leaving out `exclude_claim`, the claim being checked)."""
	day = getdate(service_date)
	birth_date = sex = specialty = billing_number = None
	history = PriorBilling()
	if patient:
		birth_date, sex = frappe.db.get_value("Patient", patient, ["date_of_birth", "sex"]) or (None, None)
		history = prior_billing(j, patient, day, exclude_claim)
	if practitioner:
		values = billing_profiles.get_values(practitioner, province=j.province, clinic=clinic)
		specialty, billing_number = get_provider(j.province).practitioner_details(values)
	return BillingContext(
		service_date=day,
		birth_date=getdate(birth_date) if birth_date else None,
		sex=sex or None,
		practitioner=practitioner,
		specialty=specialty,
		billing_number=billing_number,
		location=location,
		other_lines=tuple(other_lines),
		history=history,
	)


def prior_billing(
	j: Jurisdiction, patient: str, day: datetime.date, exclude_claim: str | None = None
) -> PriorBilling:
	rows = frappe.db.sql(
		"""select i.service_code, c.service_date
		from `tabBilling Claim Item` i join `tabBilling Claim` c on i.parent = c.name
		where i.parenttype = 'Billing Claim' and c.patient = %(patient)s and c.payer = %(payer)s
		and c.status not in %(not_billed)s and c.name != %(exclude)s
		and c.service_date between %(year_start)s and %(year_end)s""",
		{
			"patient": patient,
			"payer": j.payer,
			"not_billed": NOT_BILLED,
			"exclude": exclude_claim or "",
			"year_start": day.replace(month=1, day=1),
			"year_end": day.replace(month=12, day=31),
		},
	)
	same_day, same_year = {}, {}
	for code, billed_on in rows:
		code = (code or "").upper()
		same_year[code] = same_year.get(code, 0) + 1
		if getdate(billed_on) == day:
			same_day[code] = same_day.get(code, 0) + 1
	return PriorBilling(same_day=same_day, same_year=same_year)


__all__ = [
	"BillLineDraft",
	"BillingContext",
	"CodeRef",
	"Jurisdiction",
	"SearchQuery",
	"UnsupportedJurisdiction",
	"build_context",
	"favourites",
	"get_diagnosis_code",
	"get_fee_code",
	"get_jurisdiction",
	"price_bill_line",
	"resolve",
	"search_diagnosis_codes",
	"search_fee_codes",
	"supported_jurisdictions",
	"validate_bill_line",
]
