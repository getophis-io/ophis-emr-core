import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import cint, flt, now_datetime

from ophis_emr_core.emr_core.chart import validate_encounter

# Routes the chart's medication list knows; anything else is recorded as "Other".
CHART_ROUTES = (
	"Oral",
	"Topical",
	"Inhaled",
	"Subcutaneous",
	"Intramuscular",
	"Intravenous",
	"Ophthalmic",
	"Otic",
	"Nasal",
	"Rectal",
	"Vaginal",
)


class Prescription(Document):
	"""A prescription, numbered by its document name (the Rx number).

	A draft can be changed freely. Issuing is the prescriber's signature: from then on the
	prescription is fixed, and the only thing that can happen to it is cancellation, with a
	reason, which leaves it on the record.
	"""

	def autoname(self):
		# Rx numbers run in their own unbroken sequence. Frappe's "format:" naming draws every
		# DocType's counter from one shared series, which would leave gaps between them.
		self.name = f"RX-{getseries('RX-', 7)}"

	def validate(self):
		validate_encounter(self)
		before = self.get_doc_before_save()
		if before and before.status != "Draft" and not self.flags.from_action:
			frappe.throw(_("An issued prescription cannot be changed. Cancel it and write a new one."))

		for item in self.items:
			if flt(item.quantity) <= 0:
				frappe.throw(_("Row {0}: the quantity to dispense must be more than zero.").format(item.idx))
			if cint(item.refills) < 0:
				frappe.throw(_("Row {0}: refills cannot be negative.").format(item.idx))
			item.din = (item.din or "").strip() or None
			if item.din and not (item.din.isdigit() and len(item.din) == 8):
				frappe.throw(_("Row {0}: a DIN is 8 digits.").format(item.idx))
			# Whether a drug is monitored comes from the drug master, not from the form.
			item.monitored = cint(frappe.db.get_value("Drug Product", item.din, "monitored")) if item.din else 0

	def on_trash(self):
		if self.status != "Draft":
			frappe.throw(_("An issued prescription cannot be deleted."), frappe.PermissionError)

	@frappe.whitelist()
	def issue(self):
		self.check_permission("write")
		if self.status != "Draft":
			frappe.throw(_("This prescription has already been issued."))
		prescriber = frappe.db.get_value(
			"Practitioner", self.practitioner, ["user", "college_registration_number"], as_dict=True
		)
		if prescriber.user != frappe.session.user:
			frappe.throw(
				_("Only the prescriber can issue a prescription. Log in as the user linked to {0}.").format(self.practitioner),
				frappe.PermissionError,
			)

		if any(item.monitored for item in self.items):
			# What a pharmacy needs on a prescription for a monitored drug before it can dispense.
			if not prescriber.college_registration_number:
				frappe.throw(_("A prescription for a monitored drug needs the prescriber's college registration number."))
			if not frappe.db.get_value("Patient", self.patient, "health_card_number"):
				frappe.throw(_("A prescription for a monitored drug needs a health card number on the patient's record."))

		for item in self.items:
			item.medication = self.add_to_chart(item)
		self.update({"status": "Issued", "issued_on": now_datetime(), "issued_by": frappe.session.user})
		self.flags.from_action = True
		self.save()

	def add_to_chart(self, item) -> str:
		route = (item.route or "").strip().title()
		return (
			frappe.get_doc(
				{
					"doctype": "Patient Medication",
					"patient": self.patient,
					"medication_name": item.medication_name,
					"din": item.din,
					"strength": item.strength,
					"dose": item.dose,
					"route": (route if route in CHART_ROUTES else "Other") if route else None,
					"frequency": item.frequency,
					"start_date": self.prescription_date,
					"prescriber": self.practitioner,
					"encounter": self.encounter,
					"prescription": self.name,
					"notes": item.instructions,
				}
			)
			.insert()
			.name
		)

	@frappe.whitelist()
	def cancel_prescription(self, reason: str):
		self.check_permission("write")
		if self.status != "Issued":
			frappe.throw(_("Only an issued prescription can be cancelled."))
		if not (reason or "").strip():
			frappe.throw(_("A reason is required to cancel a prescription."))

		self.update({"status": "Cancelled", "cancelled_on": now_datetime(), "cancellation_reason": reason.strip()})
		self.flags.from_action = True
		self.save()
		for item in self.items:
			if item.medication and frappe.db.get_value("Patient Medication", item.medication, "status") == "Active":
				medication = frappe.get_doc("Patient Medication", item.medication)
				medication.status = "Discontinued"
				medication.save()
