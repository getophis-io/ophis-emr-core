import frappe
from frappe import _
from frappe.model.document import Document

from ophis_emr_core.emr_core.doctype.payer.payer import PROVINCIAL


class BillingFavourite(Document):
	"""A fee code in a practitioner's quick-pick list for a health plan. The code is kept as text,
	so a favourite outlives the code set it was picked from; `billing_codes.favourites` returns
	only those in effect."""

	def validate(self):
		self.code = (self.code or "").strip().upper()
		if frappe.db.get_value("Payer", self.payer, "payer_type") != PROVINCIAL:
			frappe.throw(
				_("Favourites are fee codes of a provincial health plan; {0} is not one.").format(self.payer)
			)
		if frappe.db.exists(
			"Billing Favourite",
			{
				"practitioner": self.practitioner,
				"payer": self.payer,
				"code": self.code,
				"name": ("!=", self.name),
			},
		):
			frappe.throw(_("{0} is already a favourite of this practitioner.").format(self.code))
