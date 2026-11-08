"""Whether a practitioner is in now, until when, or when they are next.

A practitioner linked to an `Employee` is in while checked in at the time clock (Frappe HR's
`Employee Checkin`, read by DocType name; HR may not be installed). Anyone else is in during
their working hours (`scheduling.get_working_periods`).
"""

import frappe
from frappe.utils import add_days, add_to_date, now_datetime

from ophis_emr_core.emr_core.scheduling import get_working_periods

# How far ahead to look for the next time an off-duty practitioner works.
LOOKAHEAD_DAYS = 14

# A check-in older than this without a check-out is a forgotten clock-out.
CHECKIN_DOCTYPE = "Employee Checkin"
CHECKIN_HOURS = 16

OFF = {"online": False, "checked_in_since": None, "on_shift": False, "until": None, "next_available": None}


def has_time_clock() -> bool:
	return frappe.db.table_exists(CHECKIN_DOCTYPE)


def get_checked_in(employees: list[str], now) -> dict:
	"""{employee: time of check-in} for those whose latest time clock entry is a check-in."""
	if not employees or not has_time_clock():
		return {}
	latest = {}
	for log in frappe.get_all(
		CHECKIN_DOCTYPE,
		filters={"employee": ("in", employees), "time": (">=", add_to_date(now, hours=-CHECKIN_HOURS))},
		fields=["employee", "log_type", "time"],
		order_by="time asc",
	):
		latest[log.employee] = log
	return {employee: log.time for employee, log in latest.items() if log.log_type == "IN"}


def get_availability(practitioner: str, now, uses_time_clock: bool = False, since=None) -> dict:
	"""{online, checked_in_since, on_shift, until, next_available} for one practitioner at `now`.

	`on_shift` is inside a working period; `until` is when that period ends. With the time clock,
	`online` is being checked in, whatever the roster says; without it, it is being on shift.
	"""
	current, upcoming = None, None
	day = now.date()
	for offset in range(LOOKAHEAD_DAYS + 1):
		periods = get_working_periods(practitioner, add_days(day, offset))
		for index, (starts, ends) in enumerate(periods):
			if starts <= now < ends:
				# Back-to-back periods, such as shifts either side of a handover, are one stretch.
				for next_starts, next_ends in periods[index + 1 :]:
					if next_starts > ends:
						break
					ends = max(ends, next_ends)
				current = ends
			elif starts > now and (current is None or starts > current):
				upcoming = starts
			if upcoming:
				break
		if upcoming:
			break

	online = bool(since) if uses_time_clock else current is not None
	return {
		"online": online,
		"checked_in_since": since if uses_time_clock else None,
		"on_shift": current is not None,
		"until": current,
		"next_available": None if online else upcoming,
	}


def get_practitioner_availability(practitioners: list[dict], now=None) -> dict:
	"""{practitioner name: availability} for rows with `name`, `status` and `employee`."""
	now = now or now_datetime()
	clock = has_time_clock()
	checked_in = get_checked_in([row["employee"] for row in practitioners if row.get("employee")], now)
	return {
		row["name"]: get_availability(
			row["name"], now, uses_time_clock=bool(row.get("employee")) and clock, since=checked_in.get(row.get("employee"))
		)
		if row.get("status") == "Active"
		else dict(OFF)
		for row in practitioners
	}


def get_working_now(now=None) -> list[str]:
	"""The active practitioners who are in now. Reads past permissions."""
	rows = frappe.get_all("Practitioner", filters={"status": "Active"}, fields=["name", "status", "employee"])
	return [name for name, row in get_practitioner_availability(rows, now).items() if row["online"]]
