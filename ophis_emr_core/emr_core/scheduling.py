"""When a practitioner works, and where an appointment fits.

A practitioner's working hours come from whichever app manages staff schedules, through the
`practitioner_working_periods` hook. Without an answer, the clinic's standard hours apply.
Open times are offered inside those hours, on a quarter-hour grid, around existing bookings.
"""

import frappe
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate, now_datetime

from ophis_emr_core.emr_core.doctype.patient_appointment.patient_appointment import RELEASED_STATUSES

# Standard clinic hours, Monday to Friday, for practitioners with no working hours of their own.
CLINIC_OPENS = "09:00:00"
CLINIC_CLOSES = "17:00:00"
CLINIC_WEEKDAYS = range(5)
SLOT_GRID_MINUTES = 15

# "Next available" is the first few openings on each of the next few days that have any.
SLOTS_PER_DAY = 4
SLOT_DAYS = 3
SLOT_HORIZON_DAYS = 90


def get_working_periods(practitioner: str, day) -> list[tuple]:
	"""[(start, end), …] of the hours a practitioner works on `day`.

	An app that manages staff schedules answers through the `practitioner_working_periods` hook,
	called as `(practitioner, day)`: a list of (start, end) datetimes, or None when it has nothing
	on this practitioner. Where no app answers, the clinic's standard hours apply.
	"""
	for method in frappe.get_hooks("practitioner_working_periods"):
		periods = frappe.get_attr(method)(practitioner, day)
		if periods is not None:
			return sorted(periods)
	if day.weekday() not in CLINIC_WEEKDAYS:
		return []
	return [(get_datetime(f"{day} {CLINIC_OPENS}"), get_datetime(f"{day} {CLINIC_CLOSES}"))]


def get_open_times(opens, closes, minutes: int, busy: list[tuple], not_before) -> list[str]:
	"""Up to SLOTS_PER_DAY start times between `opens` and `closes` at which `minutes` fit between the bookings."""

	def on_grid(moment):
		# The next grid point at or after `moment`, counted from the start of the period.
		passed = (moment - opens).total_seconds() / 60
		return add_to_date(opens, minutes=-(-passed // SLOT_GRID_MINUTES) * SLOT_GRID_MINUTES)

	# Today: not a time that has already passed.
	start = on_grid(not_before) if opens < not_before else opens
	times = []
	while len(times) < SLOTS_PER_DAY and add_to_date(start, minutes=minutes) <= closes:
		end = add_to_date(start, minutes=minutes)
		clash = next((booked_end for booked_start, booked_end in busy if booked_start < end and booked_end > start), None)
		if clash:
			# Jump to the end of the booking in the way, back on the grid.
			start = on_grid(clash)
			continue
		times.append(start.strftime("%H:%M"))
		start = end
	return times


def get_appointment_minutes(practitioner: str, duration: int | None = None) -> int:
	"""The length of a booking: as asked, or the practitioner's default, or one grid step."""
	return (
		cint(duration)
		or cint(frappe.db.get_value("Practitioner", practitioner, "default_appointment_duration"))
		or SLOT_GRID_MINUTES
	)


def find_openings(practitioner: str, duration: int | None = None, after=None) -> dict:
	"""The first openings in a practitioner's schedule, grouped by day. Reads past permissions.

	`after` starts the search on a later date than today. Returns
	{"duration": minutes, "days": [{"date": "YYYY-MM-DD", "times": ["HH:MM", ...]}]}.
	"""
	minutes = get_appointment_minutes(practitioner, duration)
	now = now_datetime()
	first_day = max(getdate(after), now.date()) if after else now.date()
	last_day = add_days(first_day, SLOT_HORIZON_DAYS)
	busy = {}
	for row in frappe.get_all(
		"Patient Appointment",
		filters={
			"practitioner": practitioner,
			"status": ("not in", RELEASED_STATUSES),
			"appointment_date": ("between", [first_day, last_day]),
		},
		fields=["appointment_date", "starts_on", "ends_on"],
		order_by="starts_on asc",
	):
		busy.setdefault(row.appointment_date, []).append((row.starts_on, row.ends_on))

	days = []
	day = first_day
	while day <= last_day and len(days) < SLOT_DAYS:
		times = []
		for opens, closes in get_working_periods(practitioner, day):
			times += get_open_times(opens, closes, minutes, busy.get(day, []), not_before=now)
		if times:
			days.append({"date": str(day), "times": times[:SLOTS_PER_DAY]})
		day = add_days(day, 1)
	return {"duration": minutes, "days": days}
