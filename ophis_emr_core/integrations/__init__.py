"""Connections to provincial health systems and payers.

Every integration belongs to a province: a clinic gets the ones for the province it
operates in (`clinic_province` in the site config), each switched on or off by its
`EMR Integration` record. A province is added as a module like `ontario` listed in `PROVINCES`.

Each integration runs in one of three modes. In Simulator mode it answers locally with
made-up data, so the workflow around it can be built, demonstrated and tested without
access to the real service. Conformance and Production mean the real service; those
connections need credentials and onboarding from the service's owner and are not built yet,
so they stop with an explanation rather than pretend.
"""

import time

import frappe
from frappe import _

from ophis_emr_core.emr_core.requisitions import get_clinic_province


class IntegrationError(frappe.ValidationError):
	pass


class Integration:
	"""Base for one connection. Subclasses set the attributes and add their operations."""

	key = ""  # short name used by the app, e.g. "hcv"
	code = ""  # the EMR Integration record, e.g. "ON-HCV"
	title = ""
	description = ""
	# What the real connection needs before it can be built and switched on.
	requires = ""

	def __init__(self, settings):
		self.settings = settings

	@property
	def simulated(self) -> bool:
		return self.settings.mode == "Simulator"

	def run(self, action: str, simulate, reference=None, live=None):
		"""Carry out one exchange and log its outcome.

		`simulate` produces the Simulator answer. `live` talks to the real service; an
		integration without one stops outside Simulator mode instead of pretending.
		"""
		started = time.monotonic()
		try:
			if not self.simulated and live:
				result = live()
			elif not self.simulated:
				frappe.throw(
					_("{0} is set to {1}, but the live connection is not built yet. It needs: {2}").format(
						self.title, self.settings.mode, self.requires
					),
					IntegrationError,
				)
			else:
				result = simulate()
		except Exception as error:
			# The failed request is about to be rolled back, and the log entry with it, so the
			# entry is committed on its own. Tests run inside one transaction and must not commit.
			if not frappe.flags.in_test:
				frappe.db.rollback()
			self.log(action, "Failed", reference, str(error), started)
			if not frappe.flags.in_test:
				frappe.db.commit()  # nosemgrep
			raise
		self.log(action, "Success", reference, result.get("message", "") if isinstance(result, dict) else "", started)
		return result

	def get_secret(self) -> str | None:
		return frappe.get_doc("EMR Integration", self.code).get_password("password", raise_exception=False)

	def get_extra_settings(self) -> dict:
		return frappe.parse_json(frappe.db.get_value("EMR Integration", self.code, "settings") or "{}") or {}

	def log(self, action, status, reference, message, started):
		frappe.get_doc(
			{
				"doctype": "EMR Integration Log",
				"integration": self.code,
				"action": action,
				"status": status,
				"mode": self.settings.mode,
				"reference_doctype": reference.doctype if reference else None,
				"reference_name": reference.name if reference else None,
				"user": frappe.session.user,
				"duration_ms": int((time.monotonic() - started) * 1000),
				"message": frappe.utils.strip_html(message or "")[:500],
			}
		).insert(ignore_permissions=True)


def get_classes() -> list[type[Integration]]:
	from ophis_emr_core.integrations import ontario

	provinces = {"ON": ontario.INTEGRATIONS}
	return provinces.get(get_clinic_province(), [])


def ensure_records():
	"""Create the EMR Integration record of each integration of the clinic's province, switched off."""
	for cls in get_classes():
		if frappe.db.exists("EMR Integration", cls.code):
			frappe.db.set_value("EMR Integration", cls.code, {"title": cls.title, "description": cls.description})
			continue
		frappe.get_doc(
			{
				"doctype": "EMR Integration",
				"code": cls.code,
				"title": cls.title,
				"province": get_clinic_province(),
				"description": cls.description,
				"enabled": 0,
				"mode": "Simulator",
			}
		).insert(ignore_permissions=True)


def get_settings() -> dict:
	rows = frappe.get_all(
		"EMR Integration",
		filters={"province": get_clinic_province()},
		fields=["name", "enabled", "mode", "endpoint", "username"],
	)
	return {row.name: row for row in rows}


def get_status() -> dict:
	"""{key: {"enabled": bool, "simulated": bool, "title": str}} for the clinic's province.

	The screens use this to decide which integration actions to offer.
	"""
	settings = get_settings()
	status = {}
	for cls in get_classes():
		row = settings.get(cls.code)
		status[cls.key] = {
			"enabled": bool(row and row.enabled),
			"simulated": bool(row and row.mode == "Simulator"),
			"title": cls.title,
		}
	return status


def get(key: str) -> Integration:
	"""The integration with this key, if the clinic's province has it and it is switched on."""
	cls = next((cls for cls in get_classes() if cls.key == key), None)
	row = get_settings().get(cls.code) if cls else None
	if not row or not row.enabled:
		frappe.throw(_("This integration is not enabled for this clinic."), IntegrationError)
	return cls(row)
