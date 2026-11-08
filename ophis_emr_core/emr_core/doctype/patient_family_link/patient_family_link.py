import frappe
from frappe import _
from frappe.model.document import Document

# What the patient is to the related patient, for each thing the related patient is to them.
# A child's parent is named by the parent's sex; "Parent" where it is neither.
PARENT_BY_SEX = {"Male": "Father", "Female": "Mother"}


class PatientFamilyLink(Document):
	def validate(self):
		if self.patient == self.related_patient:
			frappe.throw(_("A patient cannot be linked to themselves."))
		existing = frappe.db.get_value(
			"Patient Family Link",
			{
				"name": ("!=", self.name),
				"patient": ("in", [self.patient, self.related_patient]),
				"related_patient": ("in", [self.patient, self.related_patient]),
			},
		)
		if existing:
			frappe.throw(_("These patients are already linked as family."))


def inverse(relationship: str, sex: str | None) -> str:
	"""What the patient is to the related patient, given what the related patient is to them
	and the patient's own sex."""
	if relationship == "Child":
		return PARENT_BY_SEX.get(sex, "Parent")
	if relationship in ("Father", "Mother", "Parent"):
		return "Child"
	return relationship


def get_family(patient: str) -> list[dict]:
	"""The patient's family, from their side: [{link, patient, patient_name, image, relationship}]."""
	links = frappe.get_all(
		"Patient Family Link",
		or_filters={"patient": patient, "related_patient": patient},
		fields=["name", "patient", "related_patient", "relationship"],
		order_by="creation asc",
	)
	others = {link.related_patient if link.patient == patient else link.patient for link in links}
	people = {
		row.name: row
		for row in frappe.get_all(
			"Patient", filters={"name": ("in", list(others))}, fields=["name", "patient_name", "image", "sex"]
		)
	}
	family = []
	for link in links:
		if link.patient == patient:
			other, relationship = people.get(link.related_patient), link.relationship
		else:
			other = people.get(link.patient)
			relationship = other and inverse(link.relationship, other.sex)
		if other:
			family.append(
				{
					"link": link.name,
					"patient": other.name,
					"patient_name": other.patient_name,
					"image": other.image,
					"relationship": relationship,
				}
			)
	return family


def set_family(patient: str, members: list[dict]):
	"""Make a patient's family exactly `members`: [{patient, relationship}], each relationship
	being what that member is to this patient. Links made from the other patient's side are
	kept where they already say the same thing."""
	current = {row["patient"]: row for row in get_family(patient)}
	wanted = {member["patient"]: member.get("relationship") for member in members if member.get("patient")}
	for other, row in current.items():
		if wanted.get(other) != row["relationship"]:
			frappe.get_doc("Patient Family Link", row["link"]).delete()
	for other, relationship in wanted.items():
		if other not in current or current[other]["relationship"] != relationship:
			frappe.get_doc(
				{"doctype": "Patient Family Link", "patient": patient, "related_patient": other, "relationship": relationship}
			).insert()
