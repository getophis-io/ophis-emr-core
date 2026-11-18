"""Ontario: what the province's plan (OHIP) needs from a practitioner to pay them."""

from ophis_emr_core.emr_core.provinces import BillingKey

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
