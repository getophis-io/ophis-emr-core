from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime

from ophis_emr_core.emr_core import availability

# A Tuesday.
NOW = get_datetime("2031-03-11 10:30:00")


def at(clock: str, day: str = "2031-03-11"):
	return get_datetime(f"{day} {clock}")


class TestAvailability(IntegrationTestCase):
	def test_back_to_back_periods_are_one_stretch(self):
		def periods(practitioner, day):
			return [(at("08:00"), at("12:00")), (at("12:00"), at("16:00"))] if str(day) == "2031-03-11" else []

		with patch.object(availability, "get_working_periods", side_effect=periods):
			row = availability.get_availability("any", NOW)
		self.assertEqual((row["online"], row["until"]), (True, at("16:00")))

	def test_off_duty_shows_when_next_in(self):
		def periods(practitioner, day):
			return [(at("09:00", "2031-03-12"), at("17:00", "2031-03-12"))] if str(day) == "2031-03-12" else []

		with patch.object(availability, "get_working_periods", side_effect=periods):
			row = availability.get_availability("any", NOW)
		self.assertEqual((row["online"], row["next_available"]), (False, at("09:00", "2031-03-12")))

	def test_inactive_practitioners_are_off(self):
		rows = [{"name": "X", "status": "Inactive", "employee": None}]
		self.assertEqual(availability.get_practitioner_availability(rows, NOW)["X"], availability.OFF)


class TestPractitionerTimeClock(IntegrationTestCase):
	"""Practitioners who are employees are in while clocked in, whatever the roster says."""

	def setUp(self):
		if not availability.has_time_clock():
			self.skipTest("Frappe HR is not installed")

	def test_checked_in_without_a_shift_is_online(self):
		with patch.object(availability, "get_working_periods", return_value=[]):
			row = availability.get_availability("any", NOW, uses_time_clock=True, since=at("09:15"))
		self.assertEqual((row["online"], row["checked_in_since"], row["until"]), (True, at("09:15"), None))

	def test_on_shift_but_not_checked_in_is_offline(self):
		def shift(practitioner, day):
			return [(at("09:00"), at("17:00"))] if str(day) == "2031-03-11" else []

		with patch.object(availability, "get_working_periods", side_effect=shift):
			row = availability.get_availability("any", NOW, uses_time_clock=True, since=None)
		self.assertEqual((row["online"], row["on_shift"], row["until"]), (False, True, at("17:00")))

	def test_latest_entry_decides(self):
		employee = frappe.db.get_value("Employee", {}, "name")
		if not employee:
			self.skipTest("No employee to clock in")
		now = frappe.utils.now_datetime()

		def log(log_type, minutes_ago):
			frappe.get_doc(
				{"doctype": "Employee Checkin", "employee": employee, "log_type": log_type, "time": frappe.utils.add_to_date(now, minutes=-minutes_ago)}
			).insert(ignore_permissions=True)

		log("IN", 30)
		self.assertIn(employee, availability.get_checked_in([employee], now))
		log("OUT", 10)
		self.assertNotIn(employee, availability.get_checked_in([employee], now))
