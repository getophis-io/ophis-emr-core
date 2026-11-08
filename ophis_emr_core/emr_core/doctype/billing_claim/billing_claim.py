import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from ophis_emr_core.emr_core.chart import validate_encounter


class BillingClaim(Document):
	"""A claim for services, sent to OHIP or to a private insurer through an integration.

	Only a draft can be changed. What happens after submission is written by the integration.
	"""

	def validate(self):
		validate_encounter(self)
		before = self.get_doc_before_save()
		if before and before.status != "Draft" and not self.flags.from_integration:
			frappe.throw(_("A claim that has been submitted cannot be changed."))

		self.total = 0
		for item in self.items:
			if cint(item.units) < 1:
				frappe.throw(_("Row {0}: units must be at least 1.").format(item.idx))
			item.service_code = (item.service_code or "").strip().upper()
			item.diagnostic_code = (item.diagnostic_code or "").strip().upper() or None
			item.amount = flt(item.fee) * cint(item.units)
			self.total += item.amount

		if self.payer == "OHIP" and not frappe.db.get_value("Patient", self.patient, "health_card_number"):
			frappe.throw(_("An OHIP claim needs a health card number on the patient's record."))
		if self.payer != "OHIP" and not (self.policy_number or "").strip():
			frappe.throw(_("A {0} claim needs the policy number.").format(self.payer))

	def on_trash(self):
		if self.status != "Draft":
			frappe.throw(_("A claim that has been submitted cannot be deleted."))
