"""Move practitioners' billing numbers to their billing profiles.

They were on Practitioner (`ohip_billing_number`, `ohip_specialty_code`, `ohip_group_number`),
and for a while on Practitioner Clinic (`billing_number`, `specialty_code`, `group_number`).
Frappe keeps a column after its field is removed, so both are read here where they exist.
OHIP numbers go to the practitioner's Ontario profile; a group number becomes the value of the
clinic it was given for, or province-wide where it came from Practitioner.
"""

import frappe

from ophis_emr_core.emr_core import billing_profiles

KEYS = ("billing_number", "specialty_code", "group_number")


def execute():
	if all(frappe.db.has_column("Practitioner", f"ohip_{key}") for key in KEYS):
		for row in frappe.db.sql(
			"select name, ohip_billing_number, ohip_specialty_code, ohip_group_number from `tabPractitioner`"
			" where ifnull(ohip_billing_number, '') != ''",
			as_dict=True,
		):
			move(row.name, "ON", {key: row[f"ohip_{key}"] for key in KEYS})

	if all(frappe.db.has_column("Practitioner Clinic", key) for key in KEYS):
		for row in frappe.db.sql(
			"""select link.practitioner, link.clinic, clinic.province, link.billing_number, link.specialty_code,
				link.group_number
			from `tabPractitioner Clinic` link join `tabClinic` clinic on clinic.name = link.clinic
			where ifnull(link.billing_number, '') != ''""",
			as_dict=True,
		):
			group = row.pop("group_number")
			move(row.practitioner, row.province, {key: row[key] for key in KEYS if key != "group_number"})
			if group:
				move(row.practitioner, row.province, {"group_number": group}, clinic=row.clinic)


def move(practitioner: str, province: str, values: dict, clinic: str | None = None):
	frappe.db.savepoint("move_billing")
	try:
		billing_profiles.set_values(practitioner, province, values, clinic=clinic)
	except frappe.ValidationError:
		# A value the province's format rejects, or a province not billed yet, is left in the
		# old column for someone to enter by hand, rather than stopping the migration.
		frappe.db.rollback(save_point="move_billing")
		frappe.log_error(f"Billing values for {practitioner} in {province} not moved", str(values))
