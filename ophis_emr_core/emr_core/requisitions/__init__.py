"""Laboratory requisition forms.

Each province has its own requisition: its own list of tests, its own questions and its own
paper form. A province is supported by adding a module like `ontario` and listing it in
`FORMS`. Where the clinic's province has no form, orders fall back to free-text tests and a
plain printout.

A form module provides:

- `FORM`: the code, title, sections of tests, extra questions (`details`) and how many
  "other tests" lines the paper form has. This is what the order screen is drawn from.
- `validate(selected, details, other_tests)`: raises if the choices cannot go on one form.
- `get_field_values(order)`: the values for the PDF template's text fields and checkboxes.
"""

import io
import os
import secrets

import frappe
from frappe import _
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject

from ophis_emr_core.emr_core.requisitions import ontario

FORMS = {"ON": ontario}

TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")

# PDF field flags (/Ff) and annotation flags (/F).
READ_ONLY = 1
PUSH_BUTTON = 1 << 16
HIDDEN = 2


def get_clinic_province() -> str:
	"""The province the clinic operates in, which decides the requisition form used.

	Set `clinic_province` in the site config for a clinic outside Ontario.
	"""
	return frappe.conf.get("clinic_province") or "ON"


def get_form_module(code: str | None = None):
	"""The form with this code, or the clinic's own form when no code is given."""
	if code:
		return next((module for module in FORMS.values() if module.FORM["code"] == code), None)
	return FORMS.get(get_clinic_province())


def get_form_definition() -> dict | None:
	"""The clinic's requisition form as the order screen needs it, without the PDF mapping."""
	module = get_form_module()
	if not module:
		return None
	form = module.FORM
	return {
		"code": form["code"],
		"title": form["title"],
		"other_test_lines": form["other_test_lines"],
		"details": form["details"],
		"sections": [
			{
				"title": section["title"],
				"note": section.get("note", ""),
				"tests": [{"code": test["code"], "label": test["label"]} for test in section["tests"]],
			}
			for section in form["sections"]
		],
	}


def get_tests(form: dict) -> dict:
	return {test["code"]: test for section in form["sections"] for test in section["tests"]}


def lines(text: str | None) -> list[str]:
	return [line.strip() for line in (text or "").splitlines() if line.strip()]


def apply(order):
	"""Check an order against its requisition form and write its plain-text list of tests."""
	module = get_form_module(order.requisition_form)
	if not module:
		if order.requisition_form:
			frappe.throw(_("Requisition form {0} is not available.").format(order.requisition_form))
		return

	selected = frappe.parse_json(order.selected_tests) or []
	details = frappe.parse_json(order.requisition_details) or {}
	tests = get_tests(module.FORM)
	if not selected and not lines(order.other_tests):
		# Written as free text, for example from the desk: it goes on the form's "other tests" lines.
		check_other_tests(module.FORM, lines(order.tests))
		return

	unknown = [code for code in selected if code not in tests]
	if unknown:
		frappe.throw(_("{0} is not a test on the {1}.").format(", ".join(unknown), module.FORM["title"]))
	questions = {detail["key"]: detail for detail in module.FORM["details"]}
	for key, value in details.items():
		options = questions.get(key, {}).get("options")
		if key not in questions or (options and value and value not in options):
			frappe.throw(_("{0} is not a valid answer on the {1}.").format(key, module.FORM["title"]))
	check_other_tests(module.FORM, lines(order.other_tests))
	module.validate(selected, details)

	order.tests = "\n".join(
		[tests[code].get("summary", tests[code]["label"]) for code in tests if code in selected]
		+ lines(order.other_tests)
	)


def check_other_tests(form: dict, other_tests: list[str]):
	if len(other_tests) > form["other_test_lines"]:
		frappe.throw(
			_("The {0} has room for {1} other tests. Put the rest on a second order.").format(
				form["title"], form["other_test_lines"]
			)
		)


def render_pdf(order) -> bytes:
	"""The order on its province's requisition form, as a filled PDF that cannot be edited.

	The printed requisition is a copy of the order: a change is made to the order and printed again.
	"""
	module = get_form_module(order.requisition_form)
	if not module:
		frappe.throw(_("This order has no requisition form to print."))

	reader = PdfReader(os.path.join(TEMPLATES, module.FORM["template"]))
	permissions = None
	if reader.is_encrypted:
		# Government forms are locked against editing but open for filling in; the copy
		# produced here carries the same restrictions.
		permissions = reader.user_access_permissions
		reader.decrypt("")

	writer = PdfWriter()
	writer.append(reader)
	text, buttons = module.get_field_values(order)

	for page in writer.pages:
		writer.update_page_form_field_values(page, text, auto_regenerate=False)
		for annotation in page.get("/Annots", []):
			widget = annotation.get_object()
			parent = widget["/Parent"].get_object() if "/Parent" in widget else None
			# A checkbox in a named group is "group.name"; a radio button has only its group's name.
			if "/T" in widget:
				name = f"{parent['/T']}.{widget['/T']}" if parent is not None and "/T" in parent else widget["/T"]
				field = widget
			elif parent is not None:
				name, field = parent.get("/T"), parent
			else:
				continue

			flags = int(widget.get("/Ff", parent.get("/Ff", 0) if parent is not None else 0))
			if flags & PUSH_BUTTON:
				# The template's own "Clear Form" and "Print" buttons have no place on a finished copy.
				widget[NameObject("/F")] = NumberObject(HIDDEN)
				widget.pop("/A", None)
				continue
			for part in filter(None, (widget, parent)):
				part[NameObject("/Ff")] = NumberObject(int(part.get("/Ff", flags)) | READ_ONLY)

			if name not in buttons:
				continue
			states = [state for state in widget["/AP"]["/N"] if state != "/Off"]
			state = buttons[name] if buttons[name] in states else "/Off"
			widget[NameObject("/AS")] = NameObject(state)
			if state != "/Off":
				field[NameObject("/V")] = NameObject(state)
	writer.set_need_appearances_writer(True)

	if permissions is not None:
		writer.encrypt(user_password="", owner_password=secrets.token_urlsafe(24), permissions_flag=permissions)
	output = io.BytesIO()
	writer.write(output)
	return output.getvalue()
