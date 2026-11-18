import frappe
from frappe import _
from frappe.model.document import Document

PROVINCIAL = "Provincial Health Plan"


class Payer(Document):
	"""A provincial health plan, publicly funded program or private insurer.

	Every province's health plan is made on install and migrate (`provinces.HEALTH_PLANS`), with
	Blue Cross, Manulife and RBC; the clinic keeps the list from there. The details a plan takes are
	set in code against it (`patient_billing.get_plan_keys`).
	"""

	def validate(self):
		if self.payer_type != PROVINCIAL:
			self.province = None
			return
		if not self.province:
			frappe.throw(_("A provincial health plan needs its province."))
		if self.enabled and frappe.db.exists(
			"Payer",
			{"payer_type": PROVINCIAL, "province": self.province, "enabled": 1, "name": ("!=", self.name)},
		):
			frappe.throw(_("{0} already has an enabled health plan.").format(self.province))


def get_health_plan_payer(province: str | None) -> str | None:
	"""The enabled `Payer` that is the province's health plan, if there is one."""
	if not province:
		return None
	return frappe.db.get_value("Payer", {"payer_type": PROVINCIAL, "province": province, "enabled": 1})
