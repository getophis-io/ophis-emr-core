from ophis_emr_core.emr_core import clinics
from ophis_emr_core.emr_core.clinics import ClinicLink


class PractitionerClinic(ClinicLink):
	owner_doctype = "Practitioner"


def on_doctype_update():
	clinics.add_unique_index("Practitioner Clinic", ["practitioner", "clinic"], "unique_practitioner_clinic")
