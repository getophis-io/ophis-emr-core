import frappe
from frappe.tests import IntegrationTestCase

from ophis_emr_core.emr_core import clinics
from ophis_emr_core.emr_core.rooms import get_free_rooms
from ophis_emr_core.emr_core.testing import (
	make_appointment,
	make_clinic,
	make_patient,
	make_practitioner,
	make_room,
	make_user,
)
from ophis_emr_core.emr_core.visits import walk_in


def make_employee(company: str, **kwargs):
	values = {
		"doctype": "Employee",
		"first_name": "Test",
		"last_name": frappe.generate_hash(length=6),
		"gender": "Female",
		"date_of_birth": "1985-03-01",
		"date_of_joining": "2026-01-05",
		"company": company,
	}
	values.update(kwargs)
	return frappe.get_doc(values).insert(ignore_permissions=True)


class TestClinicLinks(IntegrationTestCase):
	def setUp(self):
		self.north = make_clinic(province="ON")
		self.south = make_clinic(province="NS", company=self.north.company)

	def links(self, doctype, name):
		return {link.clinic: link for link in clinics.get_links(doctype, name, active_only=False)}

	def test_a_new_patient_joins_the_registering_clinic_as_primary(self):
		patient = make_patient(clinic=self.north.name)
		self.assertEqual(clinics.get_clinics("Patient", patient.name), [self.north.name])
		self.assertEqual(clinics.get_primary_clinic("Patient", patient.name), self.north.name)
		self.assertEqual(frappe.db.get_value("Patient", patient.name, "company"), self.north.company)

	def test_on_a_site_with_several_clinics_a_new_patient_needs_one(self):
		self.assertRaises(frappe.ValidationError, make_patient)

	def test_a_patient_can_belong_to_several_clinics_with_one_primary(self):
		patient = make_patient(clinic=self.north.name)
		second = clinics.add_to_clinic("Patient", patient.name, self.south.name)
		self.assertFalse(second.is_primary)
		self.assertEqual(clinics.get_clinics("Patient", patient.name), [self.north.name, self.south.name])
		self.assertIn(patient.name, clinics.get_members("Patient", self.south.name))

		second.is_primary = 1
		second.save()
		links = self.links("Patient", patient.name)
		self.assertTrue(links[self.south.name].is_primary)
		self.assertFalse(links[self.north.name].is_primary)

	def test_the_primary_clinic_is_changed_only_by_choosing_another(self):
		patient = make_patient(clinic=self.north.name)
		primary = frappe.get_doc("Patient Clinic", {"patient": patient.name, "clinic": self.north.name})
		primary.is_primary = 0
		self.assertRaises(frappe.ValidationError, primary.save)

		second = clinics.add_to_clinic("Patient", patient.name, self.south.name)
		primary.reload()
		primary.status = clinics.INACTIVE
		self.assertRaises(frappe.ValidationError, primary.save)
		self.assertRaises(frappe.ValidationError, primary.delete)

		# A clinic that is not primary is simply ended.
		second.status = clinics.INACTIVE
		second.save()
		self.assertEqual(clinics.get_clinics("Patient", patient.name), [self.north.name])

	def test_a_clinic_is_linked_once(self):
		patient = make_patient(clinic=self.north.name)
		self.assertRaises(
			frappe.ValidationError,
			frappe.get_doc(
				{"doctype": "Patient Clinic", "patient": patient.name, "clinic": self.north.name}
			).insert,
		)

	def test_a_chart_number_is_unique_within_its_clinic(self):
		first = make_patient(clinic=self.north.name)
		second = make_patient(clinic=self.north.name)
		frappe.db.set_value("Patient Clinic", {"patient": first.name}, "chart_number", "C-100")
		link = frappe.get_doc("Patient Clinic", {"patient": second.name})
		link.chart_number = "C-100"
		self.assertRaises(frappe.ValidationError, link.save)
		# Another clinic keeps its own numbers.
		clinics.add_to_clinic("Patient", second.name, self.south.name, chart_number="C-100")

	def test_the_changelog_records_clinics_joined_after_registration(self):
		patient = make_patient(clinic=self.north.name)
		events = lambda: frappe.get_all(
			"Patient Event",
			filters={"patient": patient.name},
			fields=["action", "reference_doctype"],
			order_by="creation asc",
		)
		self.assertEqual([(e.action, e.reference_doctype) for e in events()], [("Created", "Patient")])
		clinics.add_to_clinic("Patient", patient.name, self.south.name)
		self.assertEqual(events()[-1].reference_doctype, "Patient Clinic")

	def test_deleting_a_practitioner_deletes_their_links(self):
		practitioner = make_practitioner(clinic=self.north.name)
		clinics.add_to_clinic("Practitioner", practitioner.name, self.south.name)
		practitioner.delete()
		self.assertFalse(frappe.db.exists("Practitioner Clinic", {"practitioner": practitioner.name}))

	def test_a_practitioner_works_at_several_clinics_and_is_the_default_for_their_user(self):
		user = make_user(f"prac-{frappe.generate_hash(length=6)}@example.com", ("EMR Physician",))
		practitioner = make_practitioner(user=user, clinic=self.south.name)
		clinics.add_to_clinic("Practitioner", practitioner.name, self.north.name, billing_number="123456")
		self.assertEqual(
			clinics.get_clinics("Practitioner", practitioner.name), [self.south.name, self.north.name]
		)
		self.assertEqual(clinics.get_default_clinic(user), self.south.name)
		self.assertEqual(
			frappe.db.get_value("Practitioner", practitioner.name, "company"), self.south.company
		)
		# A patient registered by them joins their clinic.
		frappe.set_user(user)
		try:
			self.assertEqual(clinics.get_default_clinic(), self.south.name)
		finally:
			frappe.set_user("Administrator")

	def test_an_employee_follows_their_branch(self):
		employee = make_employee(self.north.company, branch=self.north.branch)
		self.assertEqual(clinics.get_primary_clinic("Employee", employee.name), self.north.name)

		# A primary clinic chosen here becomes the employee's Branch in HRMS.
		link = clinics.add_to_clinic("Employee", employee.name, self.south.name)
		link.is_primary = 1
		link.save()
		self.assertEqual(frappe.db.get_value("Employee", employee.name, "branch"), self.south.branch)

		# And a Branch changed in HRMS becomes the primary clinic.
		employee.reload()
		employee.branch = self.north.branch
		employee.save()
		self.assertEqual(clinics.get_primary_clinic("Employee", employee.name), self.north.name)

	def test_an_employee_works_only_at_their_company_clinics(self):
		other = frappe.db.get_value("Company", {"name": ("!=", self.north.company)}, "name")
		employee = make_employee(other)
		self.assertRaises(
			frappe.ValidationError, clinics.add_to_clinic, "Employee", employee.name, self.north.name
		)


class TestRecordsInsideOneClinic(IntegrationTestCase):
	"""Appointments, encounters and rooms each belong to exactly one clinic."""

	def setUp(self):
		self.north = make_clinic(province="ON")
		self.south = make_clinic(province="NS", company=self.north.company)
		self.practitioner = make_practitioner(clinic=self.north.name)
		self.patient = make_patient(clinic=self.north.name)

	def test_a_new_clinic_gets_the_default_rooms(self):
		rooms = frappe.get_all("Room", filters={"clinic": self.south.name}, pluck="room_name")
		self.assertIn("Exam Room 1", rooms)
		self.assertIn("Triage", rooms)

	def test_room_names_are_unique_within_a_clinic_only(self):
		north_room = make_room(clinic=self.north.name, room_name="Procedure A")
		south_room = make_room(clinic=self.south.name, room_name="Procedure A")
		self.assertNotEqual(north_room.name, south_room.name)
		self.assertEqual(south_room.room_name, "Procedure A")
		self.assertRaises(frappe.ValidationError, make_room, clinic=self.north.name, room_name="Procedure A")

	def test_a_room_stays_in_its_clinic(self):
		room = make_room(clinic=self.north.name)
		room.clinic = self.south.name
		self.assertRaises(frappe.ValidationError, room.save)

	def test_free_rooms_are_those_of_the_clinic(self):
		rooms = {room["clinic"] for room in get_free_rooms(clinic=self.south.name)}
		self.assertEqual(rooms, {self.south.name})

	def test_an_appointment_is_at_the_practitioners_clinic(self):
		appointment = make_appointment(self.patient.name, self.practitioner.name)
		self.assertEqual(appointment.clinic, self.north.name)
		self.assertEqual(appointment.company, self.north.company)

	def test_the_practitioner_has_to_work_at_the_appointments_clinic(self):
		self.assertRaises(
			frappe.ValidationError,
			make_appointment,
			self.patient.name,
			self.practitioner.name,
			clinic=self.south.name,
		)
		clinics.add_to_clinic("Practitioner", self.practitioner.name, self.south.name)
		self.assertEqual(
			make_appointment(self.patient.name, self.practitioner.name, clinic=self.south.name).clinic,
			self.south.name,
		)

	def test_a_patient_booked_at_another_clinic_joins_it(self):
		clinics.add_to_clinic("Practitioner", self.practitioner.name, self.south.name)
		make_appointment(self.patient.name, self.practitioner.name, clinic=self.south.name)
		self.assertEqual(
			clinics.get_clinics("Patient", self.patient.name), [self.north.name, self.south.name]
		)
		self.assertEqual(clinics.get_primary_clinic("Patient", self.patient.name), self.north.name)

	def test_only_a_scheduled_appointment_moves_clinic(self):
		clinics.add_to_clinic("Practitioner", self.practitioner.name, self.south.name)
		appointment = make_appointment(self.patient.name, self.practitioner.name)
		appointment.clinic = self.south.name
		appointment.save()
		self.assertEqual(appointment.company, self.south.company)

		appointment.check_in()
		appointment.reload()
		appointment.clinic = self.north.name
		self.assertRaises(frappe.ValidationError, appointment.save)

	def test_the_visit_is_at_the_appointments_clinic_and_stays_there(self):
		appointment = make_appointment(self.patient.name, self.practitioner.name)
		room = make_room(clinic=self.north.name)
		encounter = frappe.get_doc("Encounter", appointment.start_encounter(room.name))
		self.assertEqual(encounter.clinic, self.north.name)
		encounter.clinic = self.south.name
		self.assertRaises(frappe.ValidationError, encounter.save)

	def test_a_visit_uses_a_room_of_its_clinic(self):
		appointment = make_appointment(self.patient.name, self.practitioner.name)
		other_clinics_room = make_room(clinic=self.south.name)
		self.assertRaises(frappe.ValidationError, appointment.start_encounter, other_clinics_room.name)

	def test_a_walk_in_is_at_the_clinic_checking_them_in(self):
		encounter = walk_in(self.patient.name, clinic=self.south.name)
		self.assertEqual(encounter.clinic, self.south.name)
		self.assertIn(self.south.name, clinics.get_clinics("Patient", self.patient.name))

	def test_on_a_site_with_several_clinics_a_walk_in_needs_one(self):
		self.assertRaises(frappe.ValidationError, walk_in, self.patient.name)
