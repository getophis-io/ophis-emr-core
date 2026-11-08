from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_datetime, getdate

from ophis_emr_core.emr_core import scheduling
from ophis_emr_core.emr_core.testing import make_appointment, make_patient, make_practitioner

# A Monday, well in the future.
DAY = "2031-03-17"

real_get_hooks = frappe.get_hooks


def rostered(practitioner, day):
	return frappe.local.test_rostered(practitioner, day)


def with_roster(hook=None, *args, **kwargs):
	if hook == "practitioner_working_periods":
		return [f"{__name__}.rostered"]
	return real_get_hooks(hook, *args, **kwargs)


class TestScheduling(IntegrationTestCase):
	def setUp(self):
		self.practitioner = make_practitioner().name

	def test_clinic_hours_without_a_staff_schedule(self):
		self.assertEqual(
			scheduling.get_working_periods(self.practitioner, getdate(DAY)),
			[(get_datetime(f"{DAY} 09:00:00"), get_datetime(f"{DAY} 17:00:00"))],
		)
		self.assertEqual(scheduling.get_working_periods(self.practitioner, getdate("2031-03-15")), [])  # a Saturday

	def test_open_times_skip_bookings_and_stay_on_the_grid(self):
		opens, closes = get_datetime(f"{DAY} 09:00:00"), get_datetime(f"{DAY} 11:00:00")
		busy = [(get_datetime(f"{DAY} 09:00:00"), get_datetime(f"{DAY} 09:20:00"))]
		times = scheduling.get_open_times(opens, closes, 30, busy, not_before=get_datetime("2031-01-01 00:00:00"))
		self.assertEqual(times, ["09:30", "10:00", "10:30"])

	def test_openings_follow_the_staff_schedule_and_its_bookings(self):
		frappe.local.test_rostered = lambda practitioner, day: (
			[(get_datetime(f"{day} 13:00:00"), get_datetime(f"{day} 14:00:00"))] if str(day) == DAY else []
		)
		make_appointment(make_patient().name, self.practitioner, appointment_date=DAY, start_time="13:00:00", duration=30)
		with patch.object(frappe, "get_hooks", side_effect=with_roster):
			result = scheduling.find_openings(self.practitioner, duration=15, after="2031-03-10")
		self.assertEqual(result, {"duration": 15, "days": [{"date": DAY, "times": ["13:30", "13:45"]}]})
