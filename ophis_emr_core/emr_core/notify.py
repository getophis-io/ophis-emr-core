"""Telling staff that a visit needs them.

The rules decide when someone is to be told: a patient checks in, a nurse picks a visit up or
hands it over, the front desk assigns it. How they are told belongs to the app on top, which
lists its senders under these hooks:

- `emr_notify_visit`: called as `(encounter, text, nurse=None, to_physicians=False)`. `text` is
  given the patient's name as {0}, the name of `nurse` (a user) as {1} and the practitioner's as
  {2}. The sender chooses the readers: the nurses and the visit's practitioner, or with
  `to_physicians` (a walk-in with no practitioner yet) the physicians working now.
- `emr_notify`: called as `(users, text, doctype, name)` to tell these users about a document.

Without a sender nothing is sent. A sender must not stop the work that called it.
"""

import frappe


def notify_visit(encounter, text: str, nurse: str | None = None, to_physicians: bool = False):
	for method in frappe.get_hooks("emr_notify_visit"):
		frappe.get_attr(method)(encounter, text, nurse=nurse, to_physicians=to_physicians)


def notify(users, text: str, doctype: str, name: str):
	for method in frappe.get_hooks("emr_notify"):
		frappe.get_attr(method)(users, text, doctype, name)
