import frappe
from frappe import _
from frappe.model.document import Document

from ophis_emr_core.emr_core import requisitions
from ophis_emr_core.emr_core.chart import validate_encounter


class PatientOrder(Document):
	def validate(self):
		validate_encounter(self)
		requisitions.apply(self)
		if not (self.tests or "").strip():
			frappe.throw(_("Choose at least one test."))
