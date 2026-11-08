"""The patient changelog.

Every insert, update, signature and deletion of a record that belongs to a patient is written
to `Patient Event`. This is wired once, as a wildcard document hook, so a DocType added later
is covered as soon as it has a `patient` link: there is no list of DocTypes to keep in step.

Frappe's own `Version` is not used as the log because it is keyed by document rather than by
patient, does not record creation, and can be deleted. Its diff routine is reused here.
"""

import frappe
from frappe.core.doctype.version.version import get_diff
from frappe.utils import cstr, now_datetime

from ophis_emr_core.emr_core import phi

EVENT_DOCTYPE = "Patient Event"

# The module whose DocTypes make up the clinical record.
MODULE = "EMR Core"

# Stored copies of something recorded elsewhere on the same document.
DERIVED_FIELDS = {"patient_name", "practitioner_name", "starts_on", "ends_on", "bmi"}

# The field that names a record in a summary line, where the DocType has a natural one.
SUBJECT_FIELDS = {
	"Patient Allergy": "allergen",
	"Patient Condition": "condition",
	"Patient Medication": "medication_name",
	"Patient Appointment": "appointment_date",
	"Clinical Note": "note_type",
	"Patient Order": "order_type",
	"Patient Referral": "referred_to",
	"Prescription": "status",
	"Billing Claim": "payer",
	"Lab Result": "source",
	"Received Report": "report_type",
}


def get_patient(doc) -> str | None:
	"""The patient a document belongs to, or None if it is not part of a patient's record."""
	if doc.doctype == EVENT_DOCTYPE or doc.meta.istable or doc.meta.module != MODULE:
		return None
	if doc.doctype == "Patient":
		return doc.name
	return doc.get("patient") if doc.meta.has_field("patient") else None


def on_insert(doc, method=None):
	record(doc, "Created")


def on_update(doc, method=None):
	# Frappe also fires on_update while submitting; that write is logged once, as "Signed".
	if doc.get("_action") == "submit":
		return
	before = doc.get_doc_before_save()
	if before:
		record(doc, "Updated", get_changes(before, doc))


def on_submit(doc, method=None):
	record(doc, "Signed", get_changes(doc.get_doc_before_save(), doc))


def on_trash(doc, method=None):
	record(doc, "Deleted")


def record(doc, action: str, changes: list[dict] | None = None, **overrides):
	"""Write one changelog entry. `overrides` exists for the backfill patch only."""
	patient = get_patient(doc)
	if not patient:
		return
	# A save that changed nothing is not an event.
	if action == "Updated" and not changes:
		return

	frappe.get_doc(
		{
			"doctype": EVENT_DOCTYPE,
			"patient": patient,
			"action": action,
			"summary": get_summary(doc, action, changes)[:255],
			"event_time": now_datetime(),
			"user": frappe.session.user,
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"changes": frappe.as_json(changes or [], indent=None),
			**overrides,
		}
	).insert(ignore_permissions=True, ignore_links=True)


def get_changes(before, after) -> list[dict]:
	"""Field-level differences as [{fieldname, label, old, new}], child tables summarised."""
	if not before:
		return []
	diff = get_diff(before, after) or {}
	meta = after.meta

	def label(fieldname):
		field = meta.get_field(fieldname)
		return field.label if field and field.label else fieldname

	# Encrypted fields keep their old and new values encrypted here too; blind indexes are left out.
	encrypted = set(phi.encrypted_fields().get(after.doctype, ()))
	indexes = set(phi.INDEX_FIELDS.get(after.doctype, {}).values())

	def value(fieldname, value):
		return phi.encrypt(cstr(value)) if fieldname in encrypted else cstr(value)

	changes = [
		{"fieldname": fieldname, "label": label(fieldname), "old": value(fieldname, old), "new": value(fieldname, new)}
		for fieldname, old, new in diff.get("changed", [])
		if fieldname not in DERIVED_FIELDS and fieldname not in indexes
	]

	tables = {}
	for key, verb in (("added", "added"), ("removed", "removed"), ("row_changed", "changed")):
		for row in diff.get(key, []):
			tables.setdefault(row[0], []).append(verb)
	for fieldname, verbs in tables.items():
		counts = ", ".join(f"{verbs.count(verb)} {verb}" for verb in dict.fromkeys(verbs))
		changes.append({"fieldname": fieldname, "label": label(fieldname), "old": "", "new": f"Rows: {counts}"})

	return changes


def get_summary(doc, action: str, changes: list[dict] | None) -> str:
	subject = doc.doctype
	subject_field = SUBJECT_FIELDS.get(doc.doctype)
	if subject_field and doc.get(subject_field):
		subject = f"{doc.doctype}: {doc.get(subject_field)}"

	status = next((change for change in changes or [] if change["fieldname"] == "status"), None)
	if action == "Updated" and status:
		return f"{subject} · {status['old'] or 'None'} → {status['new']}"
	if action == "Updated":
		return f"{subject} · {', '.join(change['label'] for change in changes)}"
	return subject


def get_tracked_doctypes() -> list[str]:
	"""Every DocType whose records belong to a patient, and so appear in the changelog."""
	linked = frappe.get_all(
		"DocField",
		filters={"fieldname": "patient", "fieldtype": "Link", "options": "Patient", "parenttype": "DocType"},
		pluck="parent",
	)
	tracked = frappe.get_all(
		"DocType",
		filters={"module": MODULE, "istable": 0, "name": ("in", [*linked, "Patient"])},
		pluck="name",
		order_by="name",
	)
	return [doctype for doctype in tracked if doctype != EVENT_DOCTYPE]
