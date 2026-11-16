"""Put every patient, practitioner and employee in a clinic.

Before clinics, a site was one clinic. It gets that clinic now (copied from its company, as on
install), and everyone on it belongs to it.
"""

import frappe

from ophis_emr_core.emr_core import clinics
from ophis_emr_core.install import ensure_default_clinic


def execute():
	ensure_default_clinic()
	clinic = frappe.db.get_value("Clinic", {"disabled": 0}, "name", order_by="creation asc")
	if not clinic:
		return
	company = frappe.db.get_value("Clinic", clinic, "company")

	for patient in frappe.get_all("Patient", fields=["name", "creation"]):
		clinics.add_to_clinic("Patient", patient.name, clinic, primary=True, registered_on=patient.creation.date())

	for practitioner in frappe.get_all("Practitioner", pluck="name"):
		clinics.add_to_clinic("Practitioner", practitioner, clinic, primary=True)

	# Staff of the clinic's company; one whose Branch is another clinic belongs to that one.
	for employee in frappe.get_all("Employee", filters={"company": company}, fields=["name", "branch", "date_of_joining"]):
		home = employee.branch if employee.branch and frappe.db.exists("Clinic", employee.branch) else clinic
		clinics.add_to_clinic("Employee", employee.name, home, primary=True, from_date=employee.date_of_joining)
