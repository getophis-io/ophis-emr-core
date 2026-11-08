from frappe.model.document import Document

from ophis_emr_core.emr_core.chart import validate_encounter


class PatientReferral(Document):
	def validate(self):
		validate_encounter(self)
