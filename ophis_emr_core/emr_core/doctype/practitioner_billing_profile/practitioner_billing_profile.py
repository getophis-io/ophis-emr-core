import frappe
from frappe import _
from frappe.model.document import Document

from ophis_emr_core.emr_core import provinces


class PractitionerBillingProfile(Document):
	"""What a practitioner bills one province's plan with, as key and value rows.

	The province sets which keys exist, which are required and their formats
	(`provinces.get_billing_keys`). A value that can differ between clinics may be given per
	clinic; a row without a clinic applies to every other clinic in the province.
	"""

	def validate(self):
		keys = provinces.get_billing_keys(self.province)
		if not keys:
			frappe.throw(
				_("Billing in {0} is not supported yet.").format(
					provinces.PROVINCES.get(self.province, self.province)
				)
			)

		seen = set()
		for row in self.billing_values:
			row.key = (row.key or "").strip().lower()
			row.value = (row.value or "").strip().upper()
			key = keys.get(row.key)
			if not key:
				frappe.throw(
					_("Row {0}: {1} is not a billing key in {2}. Use one of: {3}.").format(
						row.idx, row.key, self.province, ", ".join(keys)
					)
				)
			row.label = key.label
			key.check(row.value)
			if row.clinic:
				if not key.per_clinic:
					frappe.throw(
						_("Row {0}: {1} is the same at every clinic, so it takes no clinic.").format(
							row.idx, key.label
						)
					)
				if frappe.db.get_value("Clinic", row.clinic, "province") != self.province:
					frappe.throw(_("Row {0}: {1} is not in {2}.").format(row.idx, row.clinic, self.province))
			if (row.key, row.clinic or None) in seen:
				if row.clinic:
					frappe.throw(
						_("Row {0}: {1} at {2} is given twice.").format(row.idx, key.label, row.clinic)
					)
				frappe.throw(_("Row {0}: {1} is given twice.").format(row.idx, key.label))
			seen.add((row.key, row.clinic or None))

		for key in keys.values():
			if key.required and not any(
				k == key.key and (key.per_clinic or not clinic) for k, clinic in seen
			):
				frappe.throw(_("{0} is required.").format(key.label))
