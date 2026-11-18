import frappe
from frappe import _

from ophis_emr_core.emr_core import clinics
from ophis_emr_core.emr_core.clinics import ClinicLink


class PatientClinic(ClinicLink):
	owner_doctype = "Patient"

	def validate(self):
		super().validate()
		self.chart_number = (self.chart_number or "").strip() or None
		if self.chart_number and frappe.db.exists(
			"Patient Clinic",
			{"clinic": self.clinic, "chart_number": self.chart_number, "name": ("!=", self.name)},
		):
			frappe.throw(_("Chart number {0} is already used at {1}.").format(self.chart_number, self.clinic))


def on_doctype_update():
	clinics.add_unique_index("Patient Clinic", ["patient", "clinic"], "unique_patient_clinic")
