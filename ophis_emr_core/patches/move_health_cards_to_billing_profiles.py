"""Move patients' health cards from Patient to their provincial health plan billing profiles.

The card was on Patient (`health_card_province`, `health_card_number`, `health_card_version`,
`health_card_expiry`, the last three encrypted, and `health_card_status`, `health_card_checked_on`,
`health_card_response`). Frappe keeps a column after its field is removed, so they are read here
where they exist. A moved card's columns are cleared, so no identifier is left outside key
rotation; a card that cannot be moved (it fails validation, or another patient holds it) is
left in place and reported.
"""

import frappe

from ophis_emr_core.emr_core import patient_billing, phi, provinces
from ophis_emr_core.install import ensure_payers

COLUMNS = (
	"health_card_province",
	"health_card_number",
	"health_card_version",
	"health_card_expiry",
	"health_card_status",
	"health_card_checked_on",
	"health_card_response",
)


def execute():
	if not all(frappe.db.has_column("Patient", column) for column in COLUMNS):
		return
	ensure_payers()
	rows = frappe.db.sql(
		"""select name, health_card_province, health_card_number, health_card_version, health_card_expiry,
			health_card_status, health_card_checked_on, health_card_response
		from `tabPatient` where ifnull(health_card_number, '') != ''""",
		as_dict=True,
	)
	moved, failed = [], []
	for row in rows:
		frappe.db.savepoint("move_health_card")
		try:
			move(row)
		except frappe.ValidationError as error:
			frappe.db.rollback(save_point="move_health_card")
			failed.append(f"{row.name}: {frappe.utils.strip_html(str(error))}")
		else:
			frappe.db.release_savepoint("move_health_card")
			moved.append(row.name)
	for name in moved:
		frappe.db.sql(
			"""update `tabPatient` set health_card_province = null, health_card_number = null,
				health_card_version = null, health_card_expiry = null, health_card_status = null,
				health_card_checked_on = null, health_card_response = null, health_card_number_index = null
			where name = %s""",
			name,
		)
	print(f"Moved {len(moved)} health cards to billing profiles.")
	if failed:
		print(f"{len(failed)} could not be moved and were left on Patient:\n" + "\n".join(failed))


def move(row):
	profile = patient_billing.set_health_card(
		row.name,
		# A card recorded without its province was taken to be from the site's province.
		row.health_card_province or provinces.get_site_province(),
		phi.decrypt(row.health_card_number),
		phi.decrypt(row.health_card_version),
		phi.decrypt(row.health_card_expiry),
		part_of_creation=True,
	)
	if row.health_card_status and row.health_card_status != patient_billing.NOT_CHECKED:
		# Plain columns, kept as they were checked: not a change to the card.
		profile.db_set(
			{
				"verification_status": row.health_card_status,
				"verified_on": row.health_card_checked_on,
				"verification_response": row.health_card_response,
			},
			update_modified=False,
		)
