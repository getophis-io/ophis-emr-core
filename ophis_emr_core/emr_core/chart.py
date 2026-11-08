import frappe
from frappe import _

ACTIVE = {"status": "Active"}

# Statuses in which an order or referral is still waiting on something.
OPEN_ORDER_STATUSES = ("Ordered",)
OPEN_REFERRAL_STATUSES = ("Draft", "Sent")


@frappe.whitelist()
def get_chart_summary(patient: str) -> dict:
	"""Active allergies, conditions and medications plus the latest vitals and signed notes.

	Each section is returned only if the user can read that DocType, so front-desk users
	who can open a Patient do not receive clinical details through this endpoint.
	"""
	frappe.has_permission("Patient", "read", patient, throw=True)

	summary = {}
	if frappe.has_permission("Patient Allergy", "read"):
		summary["allergies"] = frappe.get_all(
			"Patient Allergy",
			filters={"patient": patient, **ACTIVE},
			fields=["name", "allergen", "category", "severity", "reaction"],
			order_by="creation desc",
		)
	if frappe.has_permission("Patient Condition", "read"):
		summary["conditions"] = frappe.get_all(
			"Patient Condition",
			filters={"patient": patient, **ACTIVE},
			fields=["name", "condition", "code_system", "code", "onset_date"],
			order_by="onset_date desc",
		)
		# The patient's problem history: what has been resolved, most recent first.
		summary["past_conditions"] = frappe.get_all(
			"Patient Condition",
			filters={"patient": patient, "status": "Resolved"},
			fields=["name", "condition", "code_system", "code", "onset_date", "resolved_date"],
			order_by="resolved_date desc, modified desc",
		)
	if frappe.has_permission("Patient Medication", "read"):
		summary["medications"] = frappe.get_all(
			"Patient Medication",
			filters={"patient": patient, **ACTIVE},
			fields=["name", "medication_name", "strength", "dose", "route", "frequency", "start_date"],
			order_by="start_date desc",
		)
	if frappe.has_permission("Vital Signs", "read"):
		summary["vitals"] = frappe.get_all(
			"Vital Signs",
			filters={"patient": patient},
			fields=[
				"name",
				"recorded_on",
				"height_cm",
				"weight_kg",
				"bmi",
				"temperature_c",
				"bp_systolic",
				"bp_diastolic",
				"heart_rate",
				"respiratory_rate",
				"oxygen_saturation",
			],
			order_by="recorded_on desc",
			limit=1,
		)
	if frappe.has_permission("Clinical Note", "read"):
		summary["notes"] = frappe.get_all(
			"Clinical Note",
			filters={"patient": patient, "docstatus": 1, "status": "Signed"},
			fields=["name", "note_date", "note_type", "practitioner", "assessment"],
			order_by="note_date desc",
			limit=5,
		)
	if frappe.has_permission("Patient Order", "read"):
		summary["orders"] = frappe.get_all(
			"Patient Order",
			filters={"patient": patient, "status": ("in", OPEN_ORDER_STATUSES)},
			fields=["name", "order_type", "tests", "order_date", "priority", "status"],
			order_by="order_date desc, creation desc",
		)
	if frappe.has_permission("Patient Referral", "read"):
		summary["referrals"] = frappe.get_all(
			"Patient Referral",
			filters={"patient": patient, "status": ("in", OPEN_REFERRAL_STATUSES)},
			fields=["name", "referred_to", "specialty", "referral_date", "priority", "status"],
			order_by="referral_date desc, creation desc",
		)
	return summary


def validate_encounter(doc):
	"""A record made during an encounter must belong to that encounter's patient."""
	if doc.encounter and frappe.db.get_value("Encounter", doc.encounter, "patient") != doc.patient:
		frappe.throw(_("Encounter {0} belongs to a different patient.").format(doc.encounter))


def get_ongoing_encounter(patient: str) -> str | None:
	return frappe.db.get_value(
		"Encounter", {"patient": patient, "status": "In Progress"}, "name", order_by="encounter_date desc"
	)


def get_practitioner_for_user(user: str | None = None) -> str | None:
	return frappe.db.get_value("Practitioner", {"user": user or frappe.session.user, "status": "Active"})
