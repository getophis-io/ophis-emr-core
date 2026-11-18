import re

import frappe
from frappe import _

POSTAL_CODE_PATTERN = re.compile(r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\d[ABCEGHJ-NPRSTV-Z]\d$")
HEALTH_CARD_VERSION_PATTERN = re.compile(r"^[A-Z]{1,2}$")


def passes_luhn(digits: str) -> bool:
	"""Mod-10 check, used by the final digit of Ontario's health number among others."""
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
	"""Strip formatting, then check the number in the issuing province's format.

	Any province's number is letters and digits. A province module that knows its card's format
	checks it offline (`check_health_card_number` in `provinces/<province>.py`; Ontario's is 10
	digits with a mod-10 check digit). This says nothing about eligibility, which is the
	province's validation service's to say.
	"""
	from ophis_emr_core.emr_core import provinces

	if not number:
		return None

	cleaned = re.sub(r"[\s-]", "", number).upper()
	if not cleaned.isalnum():
		frappe.throw(_("Health Card Number can only contain letters and digits."))

	check = getattr(provinces.get_province_module(province), "check_health_card_number", None)
	if check:
		check(cleaned)
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
