import frappe
from frappe import _
from frappe.utils import getdate

from ophis_emr_core.emr_core import clinics
from ophis_emr_core.emr_core.clinics import ClinicLink


class EmployeeClinic(ClinicLink):
	owner_doctype = "Employee"

	def validate(self):
		super().validate()
		if self.from_date and self.to_date and getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("To cannot be before From."))
		company = frappe.db.get_value("Employee", self.employee, "company")
		if company != frappe.db.get_value("Clinic", self.clinic, "company"):
			frappe.throw(_("{0} is not a clinic of {1}, the employee's company.").format(self.clinic, company))

	def follow_primary(self, clinic):
		# The primary clinic is the employee's Branch, which HRMS uses for shifts and payroll.
		frappe.db.set_value("Employee", self.employee, "branch", clinic.branch, update_modified=False)


def on_doctype_update():
	clinics.add_unique_index("Employee Clinic", ["employee", "clinic"], "unique_employee_clinic")
