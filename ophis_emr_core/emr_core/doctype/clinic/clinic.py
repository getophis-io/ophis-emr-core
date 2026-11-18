from zoneinfo import available_timezones

import frappe
from frappe import _
from frappe.model.document import Document

from ophis_emr_core.emr_core import provinces


class Clinic(Document):
	"""One clinic, which is one ERPNext Branch of a Company.

	Branch stays the clinic's identity everywhere ERPNext and HRMS use it (staff, shifts,
	payroll); this record adds what the clinical record needs and Branch does not hold. It is
	named after its Branch, and the Branch is created with it.
	"""

	def validate(self):
		self.clinic_name = (self.clinic_name or "").strip()
		if not self.time_zone:
			self.time_zone = provinces.TIME_ZONES[self.province]
		if self.time_zone not in available_timezones():
			frappe.throw(
				_("{0} is not a time zone. Use a name such as America/Toronto.").format(self.time_zone)
			)

		before = self.get_doc_before_save()
		if before and before.company != self.company:
			frappe.throw(_("A clinic cannot move to another company."))

	def before_insert(self):
		if not frappe.db.exists("Branch", self.clinic_name):
			frappe.get_doc({"doctype": "Branch", "branch": self.clinic_name}).insert(ignore_permissions=True)
		self.branch = self.clinic_name

	def after_insert(self):
		# A new clinic starts with the default rooms; it can change and close them afterwards.
		from ophis_emr_core.install import ensure_rooms

		ensure_rooms(self.name)

	def on_update(self):
		provinces.clear_cache()

	def on_trash(self):
		frappe.throw(_("A clinic is not deleted. Disable it instead."))
