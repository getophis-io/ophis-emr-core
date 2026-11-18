"""Factories for synthetic records, shared by the test suite and the demo data."""

import random

import frappe

from ophis_emr_core.emr_core.validators import passes_luhn


def make_health_card_number() -> str:
	"""A random number in the Ontario format with a valid check digit. Not a real card."""
	body = "".join(random.choice("0123456789") for _ in range(9))
	return next(body + check for check in "0123456789" if passes_luhn(body + check))


def make_user(email: str, roles: tuple[str, ...], first_name: str = "Test") -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": first_name, "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	frappe.get_doc("User", email).add_roles(*roles)
	return email


def make_practitioner(
	user: str | None = None, clinic: str | None = None, billing: dict | None = None, **kwargs
):
	"""A practitioner at `clinic` (or the user's own), billing the clinic's province with
	`billing`, province-wide values such as {"billing_number": ..., "specialty_code": ...}."""
	values = {
		"doctype": "Practitioner",
		"first_name": "Test",
		"last_name": frappe.generate_hash(length=6),
		"practitioner_type": "Family Physician",
		"user": user,
	}
	values.update(kwargs)
	doc = frappe.get_doc(values)
	doc.flags.clinic = clinic
	doc.insert(ignore_permissions=True)
	if billing:
		from ophis_emr_core.emr_core import billing_profiles, clinics

		province = frappe.db.get_value(
			"Clinic", clinics.get_primary_clinic("Practitioner", doc.name), "province"
		)
		billing_profiles.set_values(doc.name, province, billing)
	return doc


def make_patient(clinic: str | None = None, **kwargs):
	values = {
		"doctype": "Patient",
		"first_name": "Test",
		"last_name": frappe.generate_hash(length=6),
		"date_of_birth": "1980-01-15",
		"sex": "Female",
		"health_card_province": "ON",
		"health_card_number": make_health_card_number(),
	}
	values.update(kwargs)
	doc = frappe.get_doc(values)
	doc.flags.clinic = clinic
	return doc.insert(ignore_permissions=True)


def make_appointment(patient: str, practitioner: str, **kwargs):
	# Appointments are for rostered patients with their own practitioner, so the patient is
	# rostered with this one first.
	frappe.db.set_value(
		"Patient", patient, {"enrolment_status": "Rostered", "primary_practitioner": practitioner}
	)
	values = {
		"doctype": "Patient Appointment",
		"patient": patient,
		"practitioner": practitioner,
		"appointment_date": "2030-01-07",
		"start_time": "09:00:00",
		"duration": 15,
	}
	values.update(kwargs)
	return frappe.get_doc(values).insert(ignore_permissions=True)


def make_room(clinic: str | None = None, **kwargs):
	"""A room at `clinic`, or the user's own clinic."""
	from ophis_emr_core.emr_core.clinics import get_default_clinic

	values = {
		"doctype": "Room",
		"room_name": f"Room {frappe.generate_hash(length=6)}",
		"clinic": clinic or get_default_clinic(),
	}
	values.update(kwargs)
	return frappe.get_doc(values).insert(ignore_permissions=True)


def make_clinic(**kwargs):
	"""A clinic, and so a Branch, of an existing company."""
	values = {
		"doctype": "Clinic",
		"clinic_name": f"Clinic {frappe.generate_hash(length=6)}",
		"company": frappe.defaults.get_global_default("company")
		or frappe.db.get_value("Company", {}, "name"),
		"province": "ON",
	}
	values.update(kwargs)
	return frappe.get_doc(values).insert(ignore_permissions=True)
