import re

import frappe
from frappe import _

POSTAL_CODE_PATTERN = re.compile(r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\d[ABCEGHJ-NPRSTV-Z]\d$")
HEALTH_CARD_VERSION_PATTERN = re.compile(r"^[A-Z]{1,2}$")


def passes_luhn(digits: str) -> bool:
	"""Mod-10 check used by the Ontario health number's final digit."""
	total = 0
	for position, char in enumerate(reversed(digits)):
		value = int(char)
		if position % 2 == 1:
			value *= 2
			if value > 9:
				value -= 9
		total += value
	return total % 10 == 0


def normalize_health_card_number(number: str | None, province: str | None) -> str | None:
	"""Strip formatting and validate the parts of a health card number we can check offline.

	Only the Ontario format (10 digits, mod-10 check digit) is validated strictly. This is a
	format check and says nothing about eligibility, which needs Health Card Validation (HCV).
	"""
	if not number:
		return None

	cleaned = re.sub(r"[\s-]", "", number).upper()
	if not cleaned.isalnum():
		frappe.throw(_("Health Card Number can only contain letters and digits."))

	if province == "ON":
		if not (cleaned.isdigit() and len(cleaned) == 10):
			frappe.throw(_("An Ontario Health Card Number must be 10 digits."))
		if not passes_luhn(cleaned):
			frappe.throw(_("Health Card Number {0} is not a valid Ontario health number.").format(cleaned))

	return cleaned


def normalize_health_card_version(version: str | None) -> str | None:
	if not version:
		return None

	cleaned = version.strip().upper()
	if not HEALTH_CARD_VERSION_PATTERN.match(cleaned):
		frappe.throw(_("Version Code must be one or two letters."))
	return cleaned


def normalize_postal_code(postal_code: str | None) -> str | None:
	if not postal_code:
		return None

	cleaned = re.sub(r"[\s-]", "", postal_code).upper()
	if not POSTAL_CODE_PATTERN.match(cleaned):
		frappe.throw(_("{0} is not a valid Canadian postal code.").format(postal_code))
	return f"{cleaned[:3]} {cleaned[3:]}"
