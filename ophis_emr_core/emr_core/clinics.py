"""Which clinics a patient, practitioner or employee belongs to.

Each can belong to several clinics, through a link record of its own: `Patient Clinic`,
`Practitioner Clinic` and `Employee Clinic`, one per (record, clinic). Exactly one active link
is primary: the home clinic, which decides defaults and whose company the record follows
(`Patient.company`, `Practitioner.company`; an Employee's `branch`, which HRMS uses).

A link is ended by making it Inactive. The primary link is not deleted, and is made Inactive
only when no other clinic is left to be primary.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

ACTIVE = "Active"
INACTIVE = "Inactive"

# {DocType of the record: (link DocType, its field pointing at the record)}
LINKS = {
	"Patient": ("Patient Clinic", "patient"),
	"Practitioner": ("Practitioner Clinic", "practitioner"),
	"Employee": ("Employee Clinic", "employee"),
}


class ClinicLink(Document):
	"""Shared by the three link DocTypes. Subclasses set `owner_doctype`."""

	owner_doctype = ""

	@property
	def owner_field(self) -> str:
		return LINKS[self.owner_doctype][1]

	@property
	def owner_name(self) -> str:
		return self.get(self.owner_field)

	def siblings(self, **filters) -> list:
		return frappe.get_all(
			self.doctype,
			filters={self.owner_field: self.owner_name, "name": ("!=", self.name), **filters},
			pluck="name",
		)

	def validate(self):
		before = self.get_doc_before_save()
		if before and (before.clinic != self.clinic or before.get(self.owner_field) != self.owner_name):
			frappe.throw(_("A clinic link cannot be moved. End it and add a new one."))
		if self.is_new() and frappe.db.get_value("Clinic", self.clinic, "disabled"):
			frappe.throw(_("{0} is disabled.").format(self.clinic))
		if self.siblings(clinic=self.clinic):
			frappe.throw(_("{0} is already linked to {1}.").format(self.owner_name, self.clinic))

		if before and before.is_primary and not self.is_primary and not self.flags.replaced_as_primary:
			frappe.throw(_("Make another clinic primary instead."))
		if not self.siblings(is_primary=1, status=ACTIVE) and self.status == ACTIVE:
			# The first active clinic, or the only one left, is the primary one.
			self.is_primary = 1
		if self.is_primary and self.status != ACTIVE and self.siblings(status=ACTIVE):
			frappe.throw(_("Make another clinic primary before ending the link to {0}.").format(self.clinic))

	def on_update(self):
		if not self.is_primary:
			return
		for name in self.siblings(is_primary=1):
			link = frappe.get_doc(self.doctype, name)
			link.is_primary = 0
			link.flags.replaced_as_primary = True
			link.save(ignore_permissions=True)
		self.follow_primary(frappe.get_cached_doc("Clinic", self.clinic))

	def follow_primary(self, clinic):
		"""Keep the record in step with its primary clinic."""
		# A field kept in step, not an edit of the record: its modified time stays as it was.
		frappe.db.set_value(self.owner_doctype, self.owner_name, "company", clinic.company, update_modified=False)

	def on_trash(self):
		if self.is_primary and not self.flags.owner_deleted:
			frappe.throw(_("The primary clinic link cannot be deleted. Make it Inactive instead."))


def get_links(doctype: str, name: str, active_only: bool = True) -> list[dict]:
	"""[{clinic, is_primary, status, ...}] of a patient, practitioner or employee, primary first."""
	link_doctype, field = LINKS[doctype]
	filters = {field: name}
	if active_only:
		filters["status"] = ACTIVE
	return frappe.get_all(link_doctype, filters=filters, fields=["*"], order_by="is_primary desc, creation asc")


def get_clinics(doctype: str, name: str) -> list[str]:
	"""The clinics a record is actively linked to, primary first."""
	return [link.clinic for link in get_links(doctype, name)]


def get_primary_clinic(doctype: str, name: str) -> str | None:
	link_doctype, field = LINKS[doctype]
	return frappe.db.get_value(link_doctype, {field: name, "is_primary": 1}, "clinic")


def get_members(doctype: str, clinic: str) -> list[str]:
	"""The patients, practitioners or employees actively linked to a clinic."""
	link_doctype, field = LINKS[doctype]
	return frappe.get_all(link_doctype, filters={"clinic": clinic, "status": ACTIVE}, pluck=field)


def add_to_clinic(doctype: str, name: str, clinic: str, primary: bool = False, part_of_creation: bool = False, **values):
	"""Link a record to a clinic, or return its existing link.

	`part_of_creation` is for the first clinic of a record being created: the patient changelog
	then records the registration once, not the link as well.
	"""
	link_doctype, field = LINKS[doctype]
	existing = frappe.db.get_value(link_doctype, {field: name, "clinic": clinic})
	if existing:
		return frappe.get_doc(link_doctype, existing)
	link = frappe.get_doc({"doctype": link_doctype, field: name, "clinic": clinic, "is_primary": 1 if primary else 0, **values})
	link.flags.part_of_creation = part_of_creation
	return link.insert(ignore_permissions=True)


def get_default_clinic(user: str | None = None) -> str | None:
	"""The clinic a user works from: their own primary clinic, or the site's only active one."""
	user = user or frappe.session.user
	for doctype, field in (("Employee", "user_id"), ("Practitioner", "user")):
		name = frappe.db.get_value(doctype, {field: user})
		clinic = name and get_primary_clinic(doctype, name)
		if clinic:
			return clinic
	clinics = frappe.get_all("Clinic", filters={"disabled": 0}, pluck="name", limit=2)
	return clinics[0] if len(clinics) == 1 else None


def link_new_record(doc, values: dict | None = None):
	"""Give a new patient or practitioner its first clinic.

	The clinic is `doc.flags.clinic` when the caller names one, else the user's own. A site
	with clinics cannot have a record in none of them; a site with none yet is left alone.
	"""
	clinic = doc.flags.clinic or get_default_clinic()
	if clinic:
		add_to_clinic(doc.doctype, doc.name, clinic, primary=True, part_of_creation=True, **(values or {}))
		doc.company = frappe.db.get_value("Clinic", clinic, "company")
	elif frappe.db.count("Clinic", {"disabled": 0}):
		frappe.throw(_("Choose the clinic this {0} belongs to.").format(_(doc.doctype).lower()))


def check_clinic_can_be_found(doc):
	"""Fail before saving a new record that `link_new_record` would have no clinic for."""
	if not doc.flags.clinic and not get_default_clinic() and frappe.db.count("Clinic", {"disabled": 0}):
		frappe.throw(_("Choose the clinic this {0} belongs to.").format(_(doc.doctype).lower()))


def delete_links(doc, method=None):
	"""A deleted practitioner or employee takes its clinic links with it. (A patient is not
	deleted: its changelog keeps it.)"""
	link_doctype, field = LINKS[doc.doctype]
	for name in frappe.get_all(link_doctype, filters={field: doc.name}, pluck="name"):
		link = frappe.get_doc(link_doctype, name)
		link.flags.owner_deleted = True
		link.delete(ignore_permissions=True)


def on_employee_update(doc, method=None):
	"""An employee whose Branch is a clinic belongs to that clinic, as their primary one.

	HR sets an employee's branch in HRMS, and the clinic follows it. Changes made here go the
	other way: a new primary `Employee Clinic` becomes the employee's branch.
	"""
	if not (doc.branch and frappe.db.exists("Clinic", doc.branch)):
		return
	if not (method == "after_insert" or doc.has_value_changed("branch")):
		return
	link = add_to_clinic("Employee", doc.name, doc.branch, primary=True, from_date=doc.date_of_joining or today())
	if not link.is_primary or link.status != ACTIVE:
		link.status = ACTIVE
		link.is_primary = 1
		link.save(ignore_permissions=True)


# Records that happen inside one clinic: an appointment, an encounter, a room. Each has a
# required `clinic`, and what it involves has to be at that clinic.


def works_at(practitioner: str, clinic: str) -> bool:
	return bool(frappe.db.exists("Practitioner Clinic", {"practitioner": practitioner, "clinic": clinic, "status": ACTIVE}))


def check_works_at(practitioner: str | None, clinic: str):
	if practitioner and not works_at(practitioner, clinic):
		frappe.throw(
			_("{0} does not work at {1}.").format(frappe.db.get_value("Practitioner", practitioner, "practitioner_name"), clinic)
		)


def ensure_patient_at(patient: str, clinic: str):
	"""A patient seen at a clinic belongs to it: add the clinic, or take an ended link back up."""
	link = add_to_clinic("Patient", patient, clinic)
	if link.status != ACTIVE:
		link.status = ACTIVE
		link.save(ignore_permissions=True)


def check_clinic_open(clinic: str):
	if frappe.db.get_value("Clinic", clinic, "disabled"):
		frappe.throw(_("{0} is disabled.").format(clinic))


def check_not_moved(doc):
	"""A record made at one clinic stays there."""
	before = doc.get_doc_before_save()
	if before and before.clinic and before.clinic != doc.clinic:
		frappe.throw(_("A {0} cannot move to another clinic.").format(_(doc.doctype).lower()))


def require_clinic(doc, *candidates):
	"""Set `doc.clinic` from the first candidate that names one, or the user's own clinic."""
	if not doc.clinic:
		doc.clinic = next((clinic for clinic in candidates if clinic), None) or get_default_clinic()
	if not doc.clinic:
		frappe.throw(_("Choose the clinic for this {0}.").format(_(doc.doctype).lower()))


def add_unique_index(doctype: str, fields: list[str], name: str):
	"""A unique index on `fields`, if this site's table does not have it yet.

	`frappe.db.add_unique` looks for the index in every database on the server, so on a server
	with several sites it can skip one because another site has it.
	"""
	exists = frappe.db.sql(
		"""select 1 from information_schema.statistics
		where table_schema = database() and table_name = %s and index_name = %s limit 1""",
		(f"tab{doctype}", name),
	)
	if not exists:
		frappe.db.commit()  # nosemgrep: DDL commits on its own; nothing is left half done.
		frappe.db.sql_ddl(f"alter table `tab{doctype}` add unique `{name}` ({', '.join(f'`{field}`' for field in fields)})")
