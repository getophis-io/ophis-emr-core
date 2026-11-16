"""Put every room, appointment and encounter in a clinic, and make room names unique per clinic.

They were made when a site was one clinic. Rooms, and anything with no better answer, go to
that clinic (the first one); an appointment goes to its practitioner's primary clinic, and an
encounter to its appointment's clinic. Values are written directly, without the checks a new
booking goes through: these records already happened.
"""

import frappe

from ophis_emr_core.emr_core.doctype.room.room import on_doctype_update as room_on_doctype_update
from ophis_emr_core.install import ensure_default_clinic


def execute():
	# Room names were unique across the site; now within a clinic (`unique_clinic_room`). Frappe
	# does not drop the old index when `unique` is taken off a field.
	if frappe.db.sql("show index from `tabRoom` where Key_name = 'room_name'"):
		frappe.db.sql_ddl("alter table `tabRoom` drop index `room_name`")

	ensure_default_clinic()
	clinic = frappe.db.get_value("Clinic", {"disabled": 0}, "name", order_by="creation asc")
	if not clinic:
		return

	frappe.db.sql("update `tabRoom` set clinic = %s where ifnull(clinic, '') = ''", clinic)
	room_on_doctype_update()
	frappe.db.sql(
		"""update `tabPatient Appointment` appointment
		left join `tabPractitioner Clinic` link
			on link.practitioner = appointment.practitioner and link.is_primary = 1
		set appointment.clinic = coalesce(link.clinic, %s)
		where ifnull(appointment.clinic, '') = ''""",
		clinic,
	)
	frappe.db.sql(
		"""update `tabEncounter` encounter
		left join `tabPatient Appointment` appointment on appointment.name = encounter.appointment
		set encounter.clinic = coalesce(appointment.clinic, %s)
		where ifnull(encounter.clinic, '') = ''""",
		clinic,
	)
	frappe.db.sql(
		"""update `tabPatient Appointment` appointment join `tabClinic` clinic on clinic.name = appointment.clinic
		set appointment.company = clinic.company"""
	)
