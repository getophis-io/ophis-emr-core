import frappe
from frappe import _
from frappe.model.document import Document

from ophis_emr_core.emr_core.billing_codes import importing
from ophis_emr_core.emr_core.doctype.payer.payer import PROVINCIAL

# Fields set by the import pipeline only.
PIPELINE_FIELDS = (
	"status",
	"effective_to",
	"importer",
	"checksum",
	"code_count",
	"imported_by",
	"imported_on",
	"import_error",
	"diff_summary",
	"diff",
	"activated_by",
	"activated_on",
	"retired_by",
	"retired_on",
	"superseded_by",
)
# What the file is: fixed once it is uploaded.
SOURCE_FIELDS = ("payer", "kind", "source_file")


class BillingCodeSetVersion(Document):
	"""One imported fee or diagnosis code file of a province's health plan. See `billing_codes.importing`.

	Its status moves only through the pipeline (`importing.activate`, `importing.retire`). Its
	details can be corrected while it is staged; once it has been in effect it is fixed, and it
	can never be deleted.
	"""

	def before_insert(self):
		self.status = importing.QUEUED
		for field in PIPELINE_FIELDS:
			if field != "status":
				self.set(field, None)
		self.checksum = importing.checksum(importing.read_source_file(self.source_file).content)
		duplicate = importing.find_duplicate(self.payer, self.kind, self.checksum)
		if duplicate:
			frappe.throw(
				_("This file was already imported as {0}.").format(duplicate), frappe.DuplicateEntryError
			)

	def validate(self):
		if frappe.db.get_value("Payer", self.payer, "payer_type") != PROVINCIAL:
			frappe.throw(
				_("Billing codes belong to a provincial health plan; {0} is not one.").format(self.payer)
			)
		before = self.get_doc_before_save()
		if not before or self.flags.from_pipeline:
			return
		changed = [field for field in self.meta.get_valid_columns() if self.get(field) != before.get(field)]
		fixed = PIPELINE_FIELDS + SOURCE_FIELDS
		if before.status in (importing.ACTIVE, importing.RETIRED):
			fixed = changed
		blocked = [field for field in changed if field in fixed and field not in ("modified", "modified_by")]
		if blocked:
			frappe.throw(
				_("{0} cannot be changed here ({1}); upload a new file, or activate or retire it.").format(
					self.name, ", ".join(self.meta.get_label(field) for field in blocked)
				)
			)

	def after_insert(self):
		importing.enqueue_stage(self.name)

	def on_trash(self):
		if self.status in (importing.ACTIVE, importing.RETIRED):
			frappe.throw(
				_("{0} has been in effect and cannot be deleted; retire it instead.").format(self.name)
			)
		importing.delete_rows(self.name)
