"""Give every existing patient an Out of Pocket billing profile. New patients get one as they are
registered; health cards are moved by `move_health_cards_to_billing_profiles`."""

import frappe

from ophis_emr_core.emr_core import patient_billing
from ophis_emr_core.install import ensure_payers


def execute():
	# Patches run before after_migrate, which makes the payers on an existing site.
	ensure_payers()
	for name in frappe.get_all("Patient", pluck="name"):
		patient_billing.ensure_out_of_pocket(name, part_of_creation=True)
