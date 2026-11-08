import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, get_datetime, get_link_to_form, now_datetime

# Appointments in these states no longer hold the practitioner's time.
RELEASED_STATUSES = ("Cancelled", "No Show")

# The encounter type a booked visit starts as, where it is not an in-person appointment.
# The clinician can change it on the encounter, for example to Telephone.
ENCOUNTER_TYPES = {"Virtual Visit": "Online - Video"}


class AppointmentOverlapError(frappe.ValidationError):
	pass


class NotRosteredError(frappe.ValidationError):
	pass


class PatientAppointment(Document):
	def validate(self):
		self.set_duration()
		self.set_time_range()
		self.validate_practitioner()
		self.validate_visit_type()
		self.validate_overlap()

	def set_duration(self):
		# An unset duration falls back to the appointment type's, then the practitioner's default.
		if not cint(self.duration) and self.appointment_type:
			self.duration = frappe.db.get_value("Appointment Type", self.appointment_type, "default_duration")
		if not cint(self.duration):
			self.duration = frappe.db.get_value(
				"Practitioner", self.practitioner, "default_appointment_duration"
			)
		if cint(self.duration) <= 0:
			frappe.throw(_("Duration must be greater than zero."))

	def set_time_range(self):
		self.starts_on = get_datetime(f"{self.appointment_date} {self.start_time}")
		self.ends_on = add_to_date(self.starts_on, minutes=cint(self.duration))

	def validate_practitioner(self):
		if frappe.db.get_value("Practitioner", self.practitioner, "status") != "Active":
			frappe.throw(_("Practitioner {0} is not active.").format(self.practitioner))

	def validate_visit_type(self):
		"""Appointments are booked for rostered patients, with their own practitioner. Everyone
		else, and anyone who simply turns up, is checked in as a walk-in instead.

		Checked on booking and when who it is for changes, so existing bookings can still be
		checked in after a patient leaves the roster.
		"""
		if not (self.is_new() or any(self.has_value_changed(field) for field in ("patient", "practitioner", "visit_type"))):
			return
		if self.visit_type == "Walk-in":
			frappe.throw(_("Walk-ins are checked in when they arrive, not booked."))
		enrolment, primary = frappe.db.get_value("Patient", self.patient, ["enrolment_status", "primary_practitioner"])
		if enrolment != "Rostered":
			frappe.throw(
				_("{0} is not rostered with the clinic, so they are seen as a walk-in rather than booked.").format(
					self.patient_name or self.patient
				),
				NotRosteredError,
			)
		if self.practitioner != primary:
			frappe.throw(
				_("Family Clinic appointments are with the patient's primary practitioner, {0}.").format(
					frappe.db.get_value("Practitioner", primary, "practitioner_name") if primary else _("who is not set")
				),
				NotRosteredError,
			)

	def validate_overlap(self):
		if self.status in RELEASED_STATUSES:
			return

		overlapping = frappe.get_all(
			"Patient Appointment",
			filters={
				"practitioner": self.practitioner,
				"status": ("not in", RELEASED_STATUSES),
				"starts_on": ("<", self.ends_on),
				"ends_on": (">", self.starts_on),
				"name": ("!=", self.name),
			},
			fields=["name", "patient_name", "starts_on"],
			limit=1,
		)
		if overlapping:
			frappe.throw(
				_("{0} already has an appointment at this time: {1}.").format(
					self.practitioner_name or self.practitioner,
					get_link_to_form("Patient Appointment", overlapping[0].name),
				),
				AppointmentOverlapError,
			)

	def change_status(self, status: str, allowed_from: tuple[str, ...]):
		self.check_permission("write")
		if self.status not in allowed_from:
			frappe.throw(_("An appointment that is {0} cannot be marked {1}.").format(self.status, status))
		self.status = status

	@frappe.whitelist()
	def check_in(self):
		"""The patient has arrived. Their visit is opened at once, waiting for a nurse."""
		from ophis_emr_core.emr_core import notify
		from ophis_emr_core.emr_core.doctype.encounter.encounter import WAITING_FOR_NURSE

		self.change_status("Checked In", allowed_from=("Scheduled",))
		self.checked_in_at = now_datetime()
		# The front desk checks patients in but does not otherwise open visits.
		encounter = self.new_encounter(stage=WAITING_FOR_NURSE).insert(ignore_permissions=True)
		self.encounter = encounter.name
		self.save()
		notify.notify_visit(encounter, _("{0} has checked in to see {2} and is waiting for a nurse."))

	@frappe.whitelist()
	def mark_no_show(self):
		self.change_status("No Show", allowed_from=("Scheduled",))
		self.save()

	@frappe.whitelist()
	def cancel_appointment(self, reason: str | None = None):
		self.change_status("Cancelled", allowed_from=("Scheduled", "Checked In"))
		self.cancellation_reason = reason
		self.save()

	@frappe.whitelist()
	def start_encounter(self, room: str | None = None) -> str:
		"""Start seeing the patient in `room`, in the visit opened at check-in or, without one, a new one."""
		self.change_status("In Progress", allowed_from=("Scheduled", "Checked In"))
		if self.encounter:
			# The visit moves the booking on itself, as a nurse picks it up or a physician takes it.
			frappe.get_doc("Encounter", self.encounter).begin(room)
			self.reload()
			return self.encounter

		encounter = self.new_encounter(room=room)
		encounter.set_room(room)
		encounter.insert()
		self.encounter = encounter.name
		self.save()
		return encounter.name

	def new_encounter(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Encounter",
				"patient": self.patient,
				"practitioner": self.practitioner,
				"appointment": self.name,
				"encounter_type": ENCOUNTER_TYPES.get(self.appointment_type, "In-person Appointment"),
				"visit_type": self.visit_type,
				"reason_for_visit": self.reason_for_visit,
				**values,
			}
		)
