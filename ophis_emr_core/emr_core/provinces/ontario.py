"""Ontario: a patient's OHIP coverage, and what OHIP needs from a practitioner to pay them."""

import frappe
from frappe import _

from ophis_emr_core.emr_core.provinces import BillingKey, PlanKey
from ophis_emr_core.emr_core.validators import passes_luhn

# The health card. Its number is checked by `check_health_card_number`.
PLAN_KEYS = [
	PlanKey("health_card_number", "Health Card Number", required=True, upper=True),
	PlanKey("version_code", "Version Code", upper=True, pattern=r"[A-Z]{1,2}", hint="1 or 2 letters"),
]

BILLING_KEYS = [
	BillingKey("billing_number", "OHIP Billing Number", required=True, pattern=r"\d{6}", hint="6 digits"),
	BillingKey("specialty_code", "OHIP Specialty Code", pattern=r"\d{2}", hint="2 digits"),
	BillingKey(
		"group_number",
		"OHIP Group Number",
		pattern=r"[A-Z0-9]{4}",
		hint="4 letters or digits",
		per_clinic=True,
	),
]


def check_health_card_number(number: str):
	"""An Ontario health number: 10 digits, the last a mod-10 check digit."""
	if not (number.isdigit() and len(number) == 10):
		frappe.throw(_("An Ontario Health Card Number must be 10 digits."))
	if not passes_luhn(number):
		frappe.throw(_("Health Card Number {0} is not a valid Ontario health number.").format(number))
