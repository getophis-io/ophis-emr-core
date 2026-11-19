"""Roles and starting records, made on install and kept on migrate."""

import frappe

from ophis_emr_core import integrations
from ophis_emr_core.emr_core import phi, provinces

# Desk roles the DocType permissions refer to. They are created before DocTypes are synced so
# that the permission rows never point at a missing role.
# EMR Billing Admin loads and activates billing code files (`billing_codes.importing`).
EMR_ROLES = ("EMR Physician", "EMR Nurse", "EMR Reception", "EMR Clinic Admin", "EMR Billing Admin")

DEFAULT_APPOINTMENT_TYPES = {
	"Office Visit": 15,
	"Annual Physical": 30,
	"Well Baby Visit": 20,
	"Prenatal Visit": 20,
	"Virtual Visit": 10,
	"Minor Procedure": 30,
}

# The payers a site starts with, besides each province's health plan (from `provinces/`).
# The clinic changes the list afterwards: a payer removed here is not removed from a site.
DEFAULT_PAYERS = {
	"Blue Cross": "Public Funded",
	"Manulife": "Private",
	"RBC": "Private",
}

# A clinic starts with these rooms; it can add, change and close rooms afterwards.
EXAM_ROOM = (
	"exam_table",
	"exam_light",
	"diagnostic_set",
	"bp_monitor",
	"thermometer",
	"pulse_oximeter",
	"sink",
	"sharps_container",
)
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
	ensure_default_clinic()
	ensure_rooms()
	ensure_appointment_types()
	ensure_payers()


def after_migrate():
	phi.ensure_key()
	ensure_default_clinic()
	ensure_payers()
	integrations.ensure_records()


def ensure_roles():
	for role in EMR_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def ensure_payers():
	"""Each province's health plan, and the default payers, where the site does not have them."""
	payers = {
		plan: ("Provincial Health Plan", province) for province, plan in provinces.get_health_plans().items()
	}
	payers.update({name: (payer_type, None) for name, payer_type in DEFAULT_PAYERS.items()})
	for name, (payer_type, province) in payers.items():
		if not frappe.db.exists("Payer", name):
			frappe.get_doc(
				{"doctype": "Payer", "payer_name": name, "payer_type": payer_type, "province": province}
			).insert(ignore_permissions=True)


def ensure_appointment_types():
	for appointment_type, duration in DEFAULT_APPOINTMENT_TYPES.items():
		if not frappe.db.exists("Appointment Type", appointment_type):
			frappe.get_doc(
				{
					"doctype": "Appointment Type",
					"appointment_type": appointment_type,
					"default_duration": duration,
				}
			).insert(ignore_permissions=True)


def ensure_rooms(clinic: str | None = None):
	"""Give each clinic without rooms the default ones: `clinic`, or every clinic."""
	for name in [clinic] if clinic else frappe.get_all("Clinic", pluck="name"):
		if frappe.db.count("Room", {"clinic": name}):
			continue
		for room_name, room_type, facilities in DEFAULT_ROOMS:
			frappe.get_doc(
				{
					"doctype": "Room",
					"room_name": room_name,
					"clinic": name,
					"room_type": room_type,
					**{facility: 1 for facility in facilities},
				}
			).insert(ignore_permissions=True)


def after_setup_wizard(args=None):  # nosemgrep: overusing-args
	"""ERPNext's setup wizard has just made the company: give it its clinic."""
	ensure_default_clinic()


def ensure_default_clinic():
	"""Give a site with no clinic one, copied from its company: a single-clinic site needs no setup.

	The clinic takes the company's name, and its province from the company's address, or the
	site's province. A site with several clinics sets them up itself, and an app's setup screen
	can create the first clinic with its own details before this runs.
	"""
	if frappe.db.count("Clinic"):
		return
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
	if not company:
		return
	return frappe.get_doc(
		{
			"doctype": "Clinic",
			"clinic_name": frappe.db.get_value("Company", company, "company_name"),
			"company": company,
			"province": get_company_province(company) or provinces.get_site_province(),
		}
	).insert(ignore_permissions=True)


def get_company_province(company: str) -> str | None:
	from frappe.contacts.doctype.address.address import get_default_address

	address = get_default_address("Company", company)
	return provinces.province_from_text(frappe.db.get_value("Address", address, "state")) if address else None
