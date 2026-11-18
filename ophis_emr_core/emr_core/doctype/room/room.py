import frappe
from frappe import _
from frappe.model.document import Document

from ophis_emr_core.emr_core import clinics
from ophis_emr_core.emr_core.rooms import CLOSED, get_occupants, status_of


class Room(Document):
	"""A room in one clinic. `room_name` is unique within the clinic. The record is named after the
	room, with its clinic added where another clinic already has a room of that name."""

	def before_insert(self):
		# Named after its clinic where the name is taken, so the clinic is settled first.
		clinics.require_clinic(self)

	def autoname(self):
		self.room_name = (self.room_name or "").strip()
		taken = frappe.db.exists("Room", self.room_name)
		self.name = f"{self.room_name} ({self.clinic})" if taken else self.room_name

	def validate(self):
		self.room_name = (self.room_name or "").strip()
		clinics.check_not_moved(self)
		if frappe.db.exists(
			"Room", {"clinic": self.clinic, "room_name": self.room_name, "name": ("!=", self.name)}
		):
			frappe.throw(_("{0} already has a room called {1}.").format(self.clinic, self.room_name))
		# Only Closed is chosen by hand; Available and Occupied follow the visits in the room.
		occupant = get_occupants().get(self.name) if not self.is_new() else None
		if self.status == CLOSED and occupant:
			frappe.throw(
				_("{0} cannot be closed while {1} is in it.").format(self.name, occupant.patient_name)
			)
		self.status = status_of(self.name, self.status, {self.name: occupant} if occupant else {})


def on_doctype_update():
	clinics.add_unique_index("Room", ["clinic", "room_name"], "unique_clinic_room")
