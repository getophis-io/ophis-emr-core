import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_link_to_form

from ophis_emr_core.emr_core import patient_billing as billing
from ophis_emr_core.emr_core import phi
from ophis_emr_core.emr_core.validators import normalize_health_card_number


class PatientBillingProfile(phi.EncryptedFieldsMixin, Document):
	"""One coverage of a patient: see `emr_core/patient_billing.py`."""

	def validate(self):
		self.patient_name = frappe.db.get_value("Patient", self.patient, "patient_name")

		if self.coverage_type == billing.OUT_OF_POCKET:
			self.payer = self.issuing_province = self.expiry_date = None
			before = self.get_doc_before_save()
			if self.status != billing.ACTIVE and before and before.status == billing.ACTIVE:
				frappe.throw(
					_("A patient can always pay out of pocket: that profile cannot be made Inactive.")
				)
		else:
			self.validate_payer()
		if self.coverage_type == billing.PROVINCIAL:
			# A health plan's cards are issued by its province; only a health plan has one.
			self.issuing_province = frappe.db.get_value("Payer", self.payer, "province")
			if self.is_new() and self.status == billing.ACTIVE:
				# The provincial health plan is billed first.
				self.is_primary = 1
		else:
			self.issuing_province = None
		self.expiry_date = phi.normalize_date(self.expiry_date)
		self.set_plan_values()
		if self.coverage_type == billing.PROVINCIAL:
			self.validate_health_card()
		if self.status == billing.ACTIVE and self.coverage_type in (
			billing.PROVINCIAL,
			billing.OUT_OF_POCKET,
		):
			if self.active_siblings(coverage_type=self.coverage_type):
				frappe.throw(
					_("{0} already has an active {1} profile.").format(
						self.patient_name, _(self.coverage_type)
					)
				)
		phi.validate_lengths(self)
		self.validate_unchanged()
		self.validate_primary()

	def validate_payer(self):
		if not self.payer:
			frappe.throw(_("Provider is required for {0} coverage.").format(_(self.coverage_type)))
		payer_type, enabled = frappe.db.get_value("Payer", self.payer, ["payer_type", "enabled"])
		if payer_type != self.coverage_type:
			frappe.throw(
				_("{0} is a {1} payer, not {2}.").format(self.payer, _(payer_type), _(self.coverage_type))
			)
		if self.is_new() and not enabled:
			frappe.throw(_("{0} is disabled.").format(self.payer))

	def set_plan_values(self):
		"""Check the rows against the keys of the profile's type, and default those that come from
		the patient. Rows are kept in key order; one without a value is dropped."""
		keys = billing.get_plan_keys(self.coverage_type, self.payer)
		active = self.status == billing.ACTIVE
		rows = {}
		for row in self.plan_values:
			row.key = (row.key or "").strip().lower()
			key = keys.get(row.key)
			if not key:
				if not keys:
					frappe.throw(_("{0} coverage takes no plan details.").format(_(self.coverage_type)))
				frappe.throw(
					_("Row {0}: {1} is not a plan detail of {2} coverage. Use one of: {3}.").format(
						row.idx, row.key, _(self.coverage_type), ", ".join(keys)
					)
				)
			if row.key in rows:
				frappe.throw(_("Row {0}: {1} is given twice.").format(row.idx, key.label))
			row.value = key.normalize(row.value)
			rows[row.key] = row

		kept = []
		for key in keys.values():
			row = rows.get(key.key)
			value = row.value if row else None
			if key.patient_field and not value:
				value = key.normalize(frappe.db.get_value("Patient", self.patient, key.patient_field))
			if not value:
				if key.required and active:
					frappe.throw(_("{0} is required.").format(key.label))
				continue
			key.check(value)
			# The same row, updated: a save that changes nothing then logs nothing.
			row = row or self.append("plan_values", {"key": key.key})
			row.update({"label": key.label, "value": value, "idx": len(kept) + 1})
			phi.validate_lengths(row)
			kept.append(row)
		self.plan_values = kept

	def validate_health_card(self):
		"""The card's number in its province's format, registered to one patient only."""
		row = next((row for row in self.plan_values if row.key == billing.CARD_NUMBER), None)
		if not row:
			return
		row.value = normalize_health_card_number(row.value, self.issuing_province)
		if self.status != billing.ACTIVE:
			return
		duplicate = billing.find_profiles_by_health_card(row.value, self.issuing_province, exclude=self.name)
		if duplicate:
			patient = frappe.db.get_value(billing.DOCTYPE, duplicate[0], "patient")
			frappe.throw(
				_("Health Card Number {0} is already registered to {1}.").format(
					row.value, get_link_to_form("Patient", patient)
				),
				billing.DuplicateHealthCardError,
			)

	def validate_unchanged(self):
		"""Once added, a profile is only made Active or Inactive: any other change is a new profile.
		Which profile is primary, and a verification service's result, are kept by the system."""
		before = self.get_doc_before_save()
		if not before:
			return

		def coverage(doc):
			return (
				doc.coverage_type,
				doc.payer,
				doc.expiry_date,
				{row.key: row.value for row in doc.plan_values},
			)

		if coverage(before) != coverage(self):
			frappe.throw(
				_(
					"A billing profile cannot be changed once added, only made Active or Inactive. "
					"Add a new billing profile instead."
				),
				billing.ProfileChangedError,
			)

	def validate_primary(self):
		before = self.get_doc_before_save()
		if self.status != billing.ACTIVE:
			# An ended coverage hands primary on to the next one (`on_update`).
			self.flags.was_primary = bool(self.is_primary or (before and before.is_primary))
			self.is_primary = 0
			return
		if before and before.is_primary and not self.is_primary and not self.flags.replaced_as_primary:
			frappe.throw(_("Make another profile primary instead."))
		if not self.active_siblings(is_primary=1):
			# The first active profile, or the only one left, is the primary one.
			self.is_primary = 1

	def on_update(self):
		if self.is_primary:
			for name in self.active_siblings(is_primary=1):
				profile = frappe.get_doc(self.doctype, name)
				profile.is_primary = 0
				profile.flags.replaced_as_primary = True
				# Handing primary over is part of whatever made this profile.
				profile.flags.part_of_creation = self.flags.part_of_creation
				profile.save(ignore_permissions=True)
		elif self.flags.was_primary:
			billing.choose_primary(self.patient, exclude=self.name)

	def on_trash(self):
		if self.is_primary:
			frappe.throw(_("The primary billing profile cannot be deleted. Make it Inactive instead."))

	def active_siblings(self, **filters) -> list[str]:
		return frappe.get_all(
			self.doctype,
			filters={"patient": self.patient, "status": billing.ACTIVE, "name": ("!=", self.name), **filters},
			pluck="name",
		)
