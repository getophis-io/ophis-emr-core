import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_fullname, now_datetime

from ophis_emr_core.emr_core import notify
from ophis_emr_core.emr_core.rooms import refresh_status

# Who has the patient. A visit opened when the patient checks in waits for a nurse; the nurse
# who picks it up hands it over to the physician. A visit a physician starts is theirs at once.
WAITING_FOR_NURSE = "Waiting for Nurse"
WITH_NURSE = "With Nurse"
WITH_PHYSICIAN = "With Physician"
NURSE_ROLE = "EMR Nurse"

# A Family Clinic visit is booked with the patient's own practitioner. A walk-in is seen by
# whoever is free: any nurse picks it up, and at hand-over any physician can take it.
FAMILY_CLINIC = "Family Clinic"
WALK_IN = "Walk-in"

# Visits that happen at a distance, without a room.
REMOTE_TYPES = ("Telephone", "Online - Video")

# Who may assign a waiting visit its room, nurse or physician from the front desk.
ASSIGNING_ROLES = {"EMR Reception", "EMR Clinic Admin", "System Manager"}


class RoomOccupiedError(frappe.ValidationError):
	pass


class Encounter(Document):
	def validate(self):
		self.validate_appointment()
		self.validate_practitioner()
		self.validate_room()

	def validate_practitioner(self):
		if not self.practitioner and self.visit_type != WALK_IN:
			frappe.throw(_("A Family Clinic visit is with the patient's own practitioner, so it needs one."))

	def validate_room(self):
		"""A room holds one visit in progress at a time."""
		if not self.room or self.status != "In Progress":
			return
		# Locked for the rest of the transaction, so two people cannot take the same room at once.
		status = frappe.db.get_value("Room", self.room, "status", for_update=True)
		if status == "Closed":
			frappe.throw(_("{0} is closed.").format(self.room))
		other = frappe.db.get_value(
			"Encounter", {"room": self.room, "status": "In Progress", "name": ("!=", self.name)}, "patient_name"
		)
		if other:
			frappe.throw(_("{0} is occupied by {1}.").format(self.room, other), RoomOccupiedError)

	def on_update(self):
		# The rooms this visit has left or entered, or freed by completing it.
		before = self.get_doc_before_save()
		refresh_status([self.room, before.room if before else None])

	def set_room(self, room: str | None):
		"""Put the visit in `room`. The patient is taken into a room as they are picked up, unless
		the front desk already gave them one. Telephone and video visits need none."""
		if room:
			self.room = room
		elif not self.room and self.needs_room():
			frappe.throw(_("Choose the room the patient is being seen in."))

	def needs_room(self) -> bool:
		return self.encounter_type not in REMOTE_TYPES

	def validate_appointment(self):
		if self.appointment and (
			frappe.db.get_value("Patient Appointment", self.appointment, "patient") != self.patient
		):
			frappe.throw(_("Appointment {0} belongs to a different patient.").format(self.appointment))

	def is_for_nurse(self) -> bool:
		"""Whether the user opening this visit is a nurse it is waiting for, not its physician."""
		from ophis_emr_core.emr_core.chart import get_practitioner_for_user

		return (
			self.stage == WAITING_FOR_NURSE
			and NURSE_ROLE in frappe.get_roles()
			and not get_practitioner_for_user()
		)

	def begin(self, room: str | None = None):
		"""Start seeing the patient: a nurse picks the visit up, a physician takes it as theirs.

		A walk-in no physician has yet becomes the visit of the physician who takes it.
		"""
		from ophis_emr_core.emr_core.chart import get_practitioner_for_user

		if self.is_for_nurse():
			return self.pick_up(room)
		self.check_permission("write")
		self.check_open()
		if not self.practitioner:
			self.practitioner = get_practitioner_for_user()
			if not self.practitioner:
				frappe.throw(_("Only a physician can take this visit."), frappe.PermissionError)
		self.set_room(room)
		self.stage = WITH_PHYSICIAN
		self.save()
		self.set_appointment_status("In Progress")

	@frappe.whitelist()
	def pick_up(self, room: str | None = None):
		"""A nurse takes a waiting visit into a room. It is theirs from here until they hand it over."""
		self.check_permission("write")
		self.check_open()
		if NURSE_ROLE not in frappe.get_roles():
			frappe.throw(_("Only a nurse can pick up a waiting visit."), frappe.PermissionError)
		if self.stage != WAITING_FOR_NURSE:
			frappe.throw(
				_("This visit has already been picked up by {0}.").format(get_fullname(self.nurse))
				if self.nurse
				else _("This visit is not waiting for a nurse.")
			)

		self.update({"stage": WITH_NURSE, "nurse": frappe.session.user, "picked_up_on": now_datetime()})
		self.set_room(room)
		self.save()
		self.set_appointment_status("In Progress")
		notify.notify_visit(self, _("{0} is with {1}, before seeing {2}."), self.nurse)

	@frappe.whitelist()
	def hand_over(self):
		"""The nurse is done: the visit goes to its physician."""
		self.check_permission("write")
		self.check_open()
		if self.stage != WITH_NURSE:
			frappe.throw(_("Only a visit a nurse has picked up can be handed over."))
		if self.nurse != frappe.session.user:
			frappe.throw(
				_("{0} picked this visit up, so only they can hand it over.").format(get_fullname(self.nurse)),
				frappe.PermissionError,
			)

		self.update({"stage": WITH_PHYSICIAN, "handed_over_on": now_datetime()})
		self.save()
		# A walk-in without a physician is offered to every physician working now.
		notify.notify_visit(
			self, _("{0} has been seen by {1} and is ready for {2}."), self.nurse, to_physicians=not self.practitioner
		)

	def assign(self, room: str | None = None, nurse: str | None = None, practitioner: str | None = None):
		"""The front desk puts a waiting visit in a room, and gives it to a nurse or a physician.

		The front desk cannot open visits, so this is the one change it may make to one. A
		Family Clinic visit stays with the patient's own practitioner.
		"""
		if not ASSIGNING_ROLES & set(frappe.get_roles()):
			frappe.throw(_("Only the front desk can assign a visit."), frappe.PermissionError)
		self.check_open()
		told = []
		if room:
			self.room = room
		if practitioner and practitioner != self.practitioner:
			if self.visit_type != WALK_IN:
				frappe.throw(_("A Family Clinic visit stays with the patient's own practitioner."))
			if frappe.db.get_value("Practitioner", practitioner, "status") != "Active":
				frappe.throw(_("Practitioner {0} is not active.").format(practitioner))
			self.practitioner = practitioner
			told.append(frappe.db.get_value("Practitioner", practitioner, "user"))
		if nurse and nurse != self.nurse:
			if self.stage != WAITING_FOR_NURSE:
				frappe.throw(_("Only a visit waiting for a nurse can be given to one."))
			if NURSE_ROLE not in frappe.get_roles(nurse):
				frappe.throw(_("{0} is not a nurse.").format(get_fullname(nurse)))
			self.update({"stage": WITH_NURSE, "nurse": nurse, "picked_up_on": now_datetime()})
			told.append(nurse)
		self.save(ignore_permissions=True)
		if self.stage == WITH_NURSE:
			self.set_appointment_status("In Progress")
		where = _(" in {0}").format(self.room) if self.room else ""
		notify.notify(
			[user for user in told if user],
			_("{0} has been assigned to you{1}.").format(self.patient_name, where),
			"Encounter",
			self.name,
		)

	def check_open(self):
		if self.status != "In Progress":
			frappe.throw(_("This encounter is completed and can no longer be changed."))

	def set_appointment_status(self, status: str):
		if not self.appointment or frappe.db.get_value("Patient Appointment", self.appointment, "status") == status:
			return
		# The clinician may not hold write access to the booking itself. This is a normal save,
		# not a direct database write, so it reaches the changelog.
		appointment = frappe.get_doc("Patient Appointment", self.appointment)
		appointment.status = status
		appointment.save(ignore_permissions=True)

	@frappe.whitelist()
	def complete(self):
		self.check_permission("write")
		if self.status == "Completed":
			frappe.throw(_("This encounter is already completed."))

		unsigned_notes = frappe.get_all(
			"Clinical Note", filters={"encounter": self.name, "docstatus": 0}, pluck="name"
		)
		if unsigned_notes:
			frappe.throw(
				_("Sign or delete the draft notes before completing this encounter: {0}").format(
					", ".join(unsigned_notes)
				)
			)

		if not self.practitioner:
			frappe.throw(_("A physician has to take this visit before it can be completed."))

		self.status = "Completed"
		self.completed_on = now_datetime()
		self.save()

		self.set_appointment_status("Completed")
