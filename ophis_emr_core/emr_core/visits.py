"""Walk-ins, and who may do what with a visit in progress.

The stage changes themselves (pick up, take, hand over, assign, complete) are methods of
`Encounter`; this module holds the rules around them that an interface needs to ask first.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, today

from ophis_emr_core.emr_core import notify, phi
from ophis_emr_core.emr_core.chart import get_practitioner_for_user
from ophis_emr_core.emr_core.doctype.encounter.encounter import (
	NURSE_ROLE,
	WAITING_FOR_NURSE,
	WALK_IN,
	WITH_NURSE,
)

# What is asked of someone who walks in without being registered: enough to find them again and
# to bill the visit. The rest of their record is filled in later.
WALK_IN_REGISTRATION_FIELDS = (
	"first_name",
	"last_name",
	"date_of_birth",
	"sex",
	"health_card_province",
	"health_card_number",
	"health_card_version",
	"mobile_phone",
)

NOT_A_PRACTITIONER = (
	"Only a practitioner can write clinical notes. This login is not linked to a practitioner record."
)


def can_walk_in() -> bool:
	"""Whether this user can check in a patient who has come without an appointment."""
	return frappe.has_permission("Patient Appointment", "write") or frappe.has_permission("Encounter", "create")


def walk_in(patient: str, reason_for_visit: str | None = None):
	"""Check in a patient who has come without an appointment. Returns the encounter opened.

	A walk-in waits for the first nurse free, with no practitioner: at hand-over, or before, any
	physician can take it. A patient is checked in once a day at most.
	"""
	if not can_walk_in():
		frappe.throw(_("You cannot check patients in."), frappe.PermissionError)
	frappe.get_doc("Patient", patient).check_permission("read")
	already = frappe.db.get_value(
		"Encounter",
		{"patient": patient, "status": "In Progress", "encounter_date": ("between", [f"{today()} 00:00:00", f"{today()} 23:59:59"])},
		"name",
	)
	if already:
		frappe.throw(_("This patient is already checked in today ({0}).").format(already))

	# Whoever checks patients in may not otherwise open visits.
	encounter = frappe.get_doc(
		{
			"doctype": "Encounter",
			"patient": patient,
			"visit_type": WALK_IN,
			"encounter_type": "In-person Walk-in",
			"encounter_date": now_datetime(),
			"stage": WAITING_FOR_NURSE,
			"reason_for_visit": reason_for_visit,
		}
	).insert(ignore_permissions=True)
	notify.notify_visit(encounter, _("{0} has walked in and is waiting for a nurse."))
	return encounter


def register_walk_in(values: dict, reason_for_visit: str | None = None, allow_duplicate: bool = False) -> dict:
	"""Register someone who has walked in without being a patient yet, and check them in.

	They are registered Not Rostered: a walk-in patient, until they are rostered with a
	practitioner. If patients with the same name and date of birth exist, nobody is registered
	unless `allow_duplicate`, and {possible_duplicates} is returned to choose from. Otherwise
	returns {patient, encounter}.
	"""
	if not can_walk_in():
		frappe.throw(_("You cannot check patients in."), frappe.PermissionError)
	registration = {field: values.get(field) for field in WALK_IN_REGISTRATION_FIELDS if values.get(field)}
	if not cint(allow_duplicate):
		duplicates = frappe.get_list(
			"Patient",
			filters={field: registration.get(field) for field in ("first_name", "last_name", "date_of_birth")},
			fields=["name", "patient_name", "date_of_birth", "health_card_number", "health_card_version"],
		)
		if duplicates:
			return {"possible_duplicates": phi.decrypt_rows("Patient", duplicates)}

	patient = frappe.get_doc({"doctype": "Patient", **registration, "enrolment_status": "Not Rostered"}).insert()
	# The controller's own duplicate warning is a desk message; they were offered above instead.
	frappe.clear_messages()
	return {"patient": patient.name, "encounter": walk_in(patient.name, reason_for_visit).name}


def can_pick_up(stage: str | None) -> bool:
	"""Whether this user, as a nurse, can pick up a visit at `stage`."""
	return stage == WAITING_FOR_NURSE and NURSE_ROLE in frappe.get_roles()


def can_take(encounter) -> bool:
	"""Whether this user, as a physician, can take a visit: their own patient without waiting for a
	nurse, or any walk-in that has no physician yet, before or after a nurse has seen them."""
	own = get_practitioner_for_user()
	if not own:
		return False
	if not encounter.practitioner:
		return True
	return encounter.practitioner == own and encounter.stage in (WAITING_FOR_NURSE, WITH_NURSE)


def take(encounter: str, room: str | None = None):
	"""A physician takes a visit as theirs."""
	doc = frappe.get_doc("Encounter", encounter)
	if not can_take(doc):
		frappe.throw(_("Only the physician this visit is with can take it from the nurses."), frappe.PermissionError)
	doc.begin(room)


def get_open_encounter(encounter: str, permission: str = "write"):
	"""The encounter, if this user has `permission` on it and it is still in progress."""
	doc = frappe.get_doc("Encounter", encounter)
	doc.check_permission(permission)
	if doc.status != "In Progress":
		frappe.throw(_("This encounter is completed and can no longer be changed."))
	return doc


def get_draft_note(note: str, encounter: str):
	"""A note of this encounter that is not signed yet."""
	doc = frappe.get_doc("Clinical Note", note)
	if doc.encounter != encounter:
		frappe.throw(_("That note belongs to a different encounter."), frappe.PermissionError)
	if doc.docstatus != 0:
		frappe.throw(_("A signed note cannot be changed. Amend it instead."))
	return doc


def save_note(encounter: str, values: dict, fields: tuple[str, ...], note: str | None = None):
	"""Start a note for an encounter, or change one that is not signed yet, with `fields` of `values`."""
	if not get_practitioner_for_user():
		frappe.throw(_(NOT_A_PRACTITIONER), frappe.PermissionError)
	if note:
		doc = get_draft_note(note, encounter)
	else:
		visit = get_open_encounter(encounter, "read")
		doc = frappe.new_doc("Clinical Note")
		doc.update({"patient": visit.patient, "encounter": visit.name})

	for field in fields:
		if field in values:
			doc.set(field, values[field] or None)
	if note:
		doc.save()
	else:
		doc.insert()
	return doc


def amend_note(encounter: str, note: str, reason: str) -> str:
	"""Open a draft correction of a signed note. The signed note stays on the record."""
	doc = frappe.get_doc("Clinical Note", note)
	doc.check_permission("read")
	if doc.encounter != encounter:
		frappe.throw(_("That note belongs to a different encounter."), frappe.PermissionError)
	return doc.create_amendment((reason or "").strip())


def cancel_appointment(appointment: str, reason: str):
	"""Cancel a booking. A reason is required."""
	if not (reason or "").strip():
		frappe.throw(_("A reason is required to cancel an appointment."))
	frappe.get_doc("Patient Appointment", appointment).cancel_appointment(reason.strip())
