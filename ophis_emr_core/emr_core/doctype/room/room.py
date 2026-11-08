import frappe
from frappe import _
from frappe.model.document import Document

from ophis_emr_core.emr_core.rooms import CLOSED, get_occupants, status_of


class Room(Document):
	def validate(self):
		# Only Closed is chosen by hand; Available and Occupied follow the visits in the room.
		occupant = get_occupants().get(self.name) if not self.is_new() else None
		if self.status == CLOSED and occupant:
			frappe.throw(_("{0} cannot be closed while {1} is in it.").format(self.name, occupant.patient_name))
		self.status = status_of(self.name, self.status, {self.name: occupant} if occupant else {})
