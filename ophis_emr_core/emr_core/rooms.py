"""The clinic's rooms, and which are taken.

A room is occupied while an encounter in it is in progress. That is read from the encounters
whenever it matters, and copied to the room's status as encounters change, so the status on
the room is only ever a reflection: completing a visit frees its room. Closed is the one
status set by hand, to take a room out of use.
"""

import frappe

AVAILABLE, OCCUPIED, CLOSED = "Available", "Occupied", "Closed"


def get_facilities() -> list[dict]:
	"""[{fieldname, label}] for each facility a room can have: every check box on Room."""
	return [{"fieldname": df.fieldname, "label": df.label} for df in frappe.get_meta("Room").fields if df.fieldtype == "Check"]


def get_occupants() -> dict:
	"""{room: the encounter in progress there}."""
	return {
		row.room: row
		for row in frappe.get_all(
			"Encounter",
			filters={"status": "In Progress", "room": ("is", "set")},
			fields=["name", "room", "patient", "patient_name", "practitioner", "stage", "nurse", "visit_type", "picked_up_on", "encounter_date"],
		)
	}


def get_free_rooms(encounter: str | None = None) -> list[dict]:
	"""Rooms open and not occupied, with the room `encounter` is already in, if any. Each carries
	the labels of its facilities, to help choose."""
	occupants = get_occupants()
	facilities = get_facilities()
	rooms = frappe.get_all(
		"Room",
		filters={"status": ("!=", CLOSED)},
		fields=["name", "room_type", *[facility["fieldname"] for facility in facilities]],
		order_by="name asc",
	)
	return [
		{
			"name": room.name,
			"room_type": room.room_type,
			"facilities": [facility["label"] for facility in facilities if room.get(facility["fieldname"])],
		}
		for room in rooms
		if room.name not in occupants or occupants[room.name].name == encounter
	]


def status_of(room: str, current: str | None, occupants: dict | None = None) -> str:
	if current == CLOSED:
		return CLOSED
	occupants = get_occupants() if occupants is None else occupants
	return OCCUPIED if room in occupants else AVAILABLE


def refresh_status(rooms):
	"""Bring the status of `rooms` in line with the visits in them."""
	rooms = [room for room in set(rooms) if room]
	if not rooms:
		return
	occupants = get_occupants()
	for name, current in frappe.get_all("Room", filters={"name": ("in", rooms)}, fields=["name", "status"], as_list=True):
		status = status_of(name, current, occupants)
		if status != current:
			frappe.db.set_value("Room", name, "status", status, update_modified=False)
