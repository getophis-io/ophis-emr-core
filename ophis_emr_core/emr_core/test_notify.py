from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import notify
from ophis_emr_core.emr_core.testing import make_appointment, make_patient, make_practitioner

SENT = []


def record_visit(encounter, text, nurse=None, to_physicians=False):
	SENT.append(("visit", encounter.name, text, nurse, to_physicians))


def record_users(users, text, doctype, name):
	SENT.append(("users", list(users), text, doctype, name))


SENDERS = {
	"emr_notify_visit": [f"{__name__}.record_visit"],
	"emr_notify": [f"{__name__}.record_users"],
}
real_get_hooks = frappe.get_hooks


def with_senders(name=None, *args, **kwargs):
	return SENDERS[name] if name in SENDERS else real_get_hooks(name, *args, **kwargs)


class TestNotify(IntegrationTestCase):
	def setUp(self):
		SENT.clear()

	def test_nothing_is_sent_without_a_sender(self):
		with patch.object(frappe, "get_hooks", side_effect=lambda name=None, *a, **k: [] if name in SENDERS else real_get_hooks(name, *a, **k)):
			notify.notify(["someone@example.com"], "Hello", "Patient", "X")
		self.assertEqual(SENT, [])

	def test_senders_are_called_with_the_document(self):
		with patch.object(frappe, "get_hooks", side_effect=with_senders):
			notify.notify(["someone@example.com"], "Hello", "Patient", "X")
		self.assertEqual(SENT, [("users", ["someone@example.com"], "Hello", "Patient", "X")])

	def test_check_in_tells_the_senders_that_a_visit_is_waiting(self):
		practitioner = make_practitioner()
		appointment = make_appointment(make_patient().name, practitioner.name)
		with patch.object(frappe, "get_hooks", side_effect=with_senders):
			appointment.check_in()
		appointment.reload()
		self.assertEqual(len(SENT), 1)
		kind, encounter, text, nurse, to_physicians = SENT[0]
		self.assertEqual((kind, encounter, nurse, to_physicians), ("visit", appointment.encounter, None, False))
		self.assertIn("{0}", text)
