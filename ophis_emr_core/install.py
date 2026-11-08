"""Roles and starting records, made on install and kept on migrate."""

import frappe

from ophis_emr_core import integrations
from ophis_emr_core.emr_core import phi

# Desk roles the DocType permissions refer to. They are created before DocTypes are synced so
# that the permission rows never point at a missing role.
EMR_ROLES = ("EMR Physician", "EMR Nurse", "EMR Reception", "EMR Clinic Admin")

DEFAULT_APPOINTMENT_TYPES = {
	"Office Visit": 15,
	"Annual Physical": 30,
	"Well Baby Visit": 20,
	"Prenatal Visit": 20,
	"Virtual Visit": 10,
	"Minor Procedure": 30,
}

# A clinic starts with these rooms; it can add, change and close rooms afterwards.
EXAM_ROOM = ("exam_table", "exam_light", "diagnostic_set", "bp_monitor", "thermometer", "pulse_oximeter", "sink", "sharps_container")
TRIAGE = ("bp_monitor", "thermometer", "pulse_oximeter", "weight_scale", "stadiometer", "glucometer", "sink")
DEFAULT_ROOMS = (
	("Exam Room 1", "Exam Room", EXAM_ROOM),
	("Exam Room 2", "Exam Room", EXAM_ROOM),
	("Exam Room 3", "Exam Room", (*EXAM_ROOM, "ecg_machine")),
	("Triage", "Triage", TRIAGE),
)


def before_install():
	ensure_roles()


def before_migrate():
	ensure_roles()


def after_install():
	phi.ensure_key()
	ensure_rooms()
	ensure_appointment_types()


def after_migrate():
	phi.ensure_key()
	integrations.ensure_records()


def ensure_roles():
	for role in EMR_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def ensure_appointment_types():
	for appointment_type, duration in DEFAULT_APPOINTMENT_TYPES.items():
		if not frappe.db.exists("Appointment Type", appointment_type):
			frappe.get_doc(
				{"doctype": "Appointment Type", "appointment_type": appointment_type, "default_duration": duration}
			).insert(ignore_permissions=True)


def ensure_rooms():
	"""Give a clinic without rooms the default ones."""
	if frappe.db.count("Room"):
		return
	for room_name, room_type, facilities in DEFAULT_ROOMS:
		frappe.get_doc(
			{"doctype": "Room", "room_name": room_name, "room_type": room_type, **{facility: 1 for facility in facilities}}
		).insert(ignore_permissions=True)
