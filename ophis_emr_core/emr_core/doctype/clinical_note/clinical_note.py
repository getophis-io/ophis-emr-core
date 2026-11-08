import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from ophis_emr_core.emr_core.chart import get_practitioner_for_user
from ophis_emr_core.emr_core.events import record as record_event

NOTE_SECTIONS = ("subjective", "objective", "assessment", "plan")


class ClinicalNote(Document):
	"""A clinical note whose signature is the document submit.

	A signed note is never edited or cancelled. Corrections are made by signing a new note
	that points at the original through `amends`; the original stays on the chart, marked
	Amended, with a link to the note that supersedes it.
	"""

	def before_validate(self):
		if not self.practitioner:
			self.practitioner = get_practitioner_for_user()
		if not self.practitioner:
			# Said here, before the generic "Value missing: Author", because the cause is the login.
			frappe.throw(
				_(
					"A clinical note needs an author. This login is not linked to a practitioner record, "
					"so log in as the practitioner writing the note."
				)
			)

	def validate(self):
		self.validate_content()
		self.validate_encounter()
		self.validate_amendment()

	def validate_content(self):
		if not any((self.get(section) or "").strip() for section in NOTE_SECTIONS):
			frappe.throw(_("A clinical note cannot be empty."))

	def validate_encounter(self):
		if self.encounter and frappe.db.get_value("Encounter", self.encounter, "patient") != self.patient:
			frappe.throw(_("Encounter {0} belongs to a different patient.").format(self.encounter))

	def validate_amendment(self):
		if not self.amends:
			return

		if not (self.amendment_reason or "").strip():
			frappe.throw(_("A reason is required when amending a signed note."))

		original = frappe.db.get_value(
			"Clinical Note", self.amends, ["docstatus", "status", "patient"], as_dict=True
		)
		if original.docstatus != 1 or original.status != "Signed":
			frappe.throw(_("Only a signed note that has not already been amended can be amended."))
		if original.patient != self.patient:
			frappe.throw(_("An amendment must be for the same patient as the original note."))

	def before_submit(self):
		self.validate_signer()
		self.status = "Signed"
		self.signed_by = frappe.session.user
		self.signed_on = now_datetime()

	def validate_signer(self):
		author_user = frappe.db.get_value("Practitioner", self.practitioner, "user")
		if author_user != frappe.session.user:
			frappe.throw(
				_("Only the author of a note can sign it. Log in as the user linked to {0}.").format(
					self.practitioner
				),
				frappe.PermissionError,
			)

	def on_submit(self):
		if self.amends:
			original = frappe.get_doc("Clinical Note", self.amends)
			original.db_set({"status": "Amended", "superseded_by": self.name})
			# db_set skips the document hooks that feed the changelog, so record this one by hand.
			record_event(
				original,
				"Updated",
				[
					{"fieldname": "status", "label": "Status", "old": "Signed", "new": "Amended"},
					{"fieldname": "superseded_by", "label": "Superseded By", "old": "", "new": self.name},
				],
			)
			original.add_comment(
				"Info", _("Amended by {0}: {1}").format(self.name, self.amendment_reason)
			)

	def before_cancel(self):
		frappe.throw(_("A signed clinical note cannot be cancelled. Amend it instead."))

	def on_trash(self):
		if self.docstatus != 0:
			frappe.throw(_("A signed clinical note cannot be deleted."))

	@frappe.whitelist()
	def create_amendment(self, reason: str) -> str:
		"""Create a draft copy of this signed note for the author to correct and sign."""
		if self.docstatus != 1 or self.status != "Signed":
			frappe.throw(_("Only a signed note that has not already been amended can be amended."))

		pending = frappe.db.get_value("Clinical Note", {"amends": self.name, "docstatus": 0})
		if pending:
			frappe.throw(_("Draft amendment {0} already exists for this note.").format(pending))

		# By default copy_doc carries over no_copy fields and the source's docstatus, which would
		# create the amendment already signed in the original author's name.
		amendment = frappe.copy_doc(self, ignore_no_copy=False)
		amendment.docstatus = 0
		amendment.status = "Draft"
		amendment.amends = self.name
		amendment.amendment_reason = reason
		amendment.practitioner = get_practitioner_for_user() or self.practitioner
		amendment.note_date = now_datetime()
		amendment.insert()
		return amendment.name
