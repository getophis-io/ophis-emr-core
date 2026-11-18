"""The keys a plan's details take, for the desk's Key list on plan details.

A virtual DocType: nothing is stored. The keys are set in code against each plan
(`patient_billing.get_plan_keys`), and this answers the desk's search and Frappe's link checks
from there. Filtered by `plan` (a Payer), it lists that plan's keys; unfiltered, every key.
"""

import frappe
from frappe import _
from frappe.model.document import Document


def get_keys(plan: str | None = None) -> list[frappe._dict]:
	"""[{name, key, label}] of a plan's keys in order, or of every key the code knows."""
	from ophis_emr_core.emr_core import patient_billing

	if plan:
		payer_type = frappe.db.get_value("Payer", plan, "payer_type")
		keys = patient_billing.get_plan_keys(payer_type, plan).values() if payer_type else []
	else:
		keys = patient_billing.get_known_plan_keys().values()
	return [frappe._dict(name=key.key, key=key.key, label=key.label) for key in keys]


def matching(filters=None, or_filters=None) -> list[frappe._dict]:
	"""The keys matching the desk's filters: `plan = X`, and text typed (a like on name or label)."""
	plan = next((f.value for f in filters or [] if f.fieldname == "plan" and f.operator == "="), None)
	if plan is not None and not plan:
		# A row of a profile with no provider chosen yet: nothing to suggest.
		return []
	rows = get_keys(plan)
	texts = [
		str(f.value).strip("%").lower()
		for f in [*(filters or []), *(or_filters or [])]
		if f.operator == "like" and f.fieldname in ("name", "key", "label")
	]
	if texts:
		rows = [row for row in rows if any(t in row.key.lower() or t in row.label.lower() for t in texts)]
	names = [f.value for f in filters or [] if f.fieldname == "name" and f.operator == "="]
	if names:
		rows = [row for row in rows if row.name in names]
	return rows


def column(row, field) -> str | None:
	"""A row's value for one of the fields the desk asks for (`label as label`, or an expression)."""
	if not isinstance(field, str):
		return None
	name = field.split(" as ")[-1].strip(" `'").split(".")[-1]
	return row.get(name)


class PlanDetailKey(Document):
	def load_from_db(self):
		row = next((row for row in get_keys() if row.name == self.name), None)
		if not row:
			raise frappe.DoesNotExistError(_("{0} is not a plan detail key.").format(self.name))
		super(Document, self).__init__({"doctype": self.doctype, **row})

	@staticmethod
	def get_list(
		filters=None,
		or_filters=None,
		start=0,
		page_length=20,
		fields=None,
		as_list=False,
		pluck=None,
		**kwargs,
	):
		rows = matching(filters, or_filters)[start : start + page_length]
		if pluck:
			return [row.get(pluck) for row in rows]
		if as_list:
			return [[column(row, field) for field in fields or ["name"]] for row in rows]
		return rows

	@staticmethod
	def get_count(filters=None, **kwargs) -> int:
		return len(matching(filters))

	@staticmethod
	def get_stats(**kwargs) -> dict:
		return {}

	def db_insert(self, *args, **kwargs):
		frappe.throw(_("Plan detail keys are set in code."))

	def db_update(self, *args, **kwargs):
		frappe.throw(_("Plan detail keys are set in code."))

	def delete(self, *args, **kwargs):
		frappe.throw(_("Plan detail keys are set in code."))
