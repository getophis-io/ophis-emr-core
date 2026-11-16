"""A practitioner's billing values: what they bill each province's plan with.

They are kept on `Practitioner Billing Profile`, one per practitioner and province, as key and
value rows whose keys the province defines (`provinces.get_billing_keys`). Billing code asks
here for the value that applies at a clinic.
"""

import frappe


def get_profile_name(practitioner: str, province: str) -> str | None:
	"""The practitioner's active profile for a province."""
	return frappe.db.get_value(
		"Practitioner Billing Profile", {"practitioner": practitioner, "province": province, "status": "Active"}
	)


def get_values(practitioner: str, province: str | None = None, clinic: str | None = None) -> dict[str, str]:
	"""{key: value} the practitioner bills with in a province, as they apply at `clinic`.

	The province is the clinic's when not given. A clinic's own value of a key wins over the
	province-wide one.
	"""
	province = province or (clinic and frappe.db.get_value("Clinic", clinic, "province"))
	profile = province and get_profile_name(practitioner, province)
	if not profile:
		return {}
	rows = frappe.get_all(
		"Practitioner Billing Profile Value",
		filters={"parent": profile, "parenttype": "Practitioner Billing Profile"},
		fields=["key", "value", "clinic"],
	)
	values = {row.key: row.value for row in rows if not row.clinic}
	values.update({row.key: row.value for row in rows if clinic and row.clinic == clinic})
	return values


def get_value(practitioner: str, key: str, province: str | None = None, clinic: str | None = None) -> str | None:
	return get_values(practitioner, province, clinic).get(key)


def set_values(practitioner: str, province: str, values: dict[str, str], clinic: str | None = None):
	"""Write values into the practitioner's profile for a province, making it if need be.

	`clinic` gives the values as that clinic's own; without it they are province-wide.
	"""
	name = frappe.db.get_value("Practitioner Billing Profile", {"practitioner": practitioner, "province": province})
	profile = (
		frappe.get_doc("Practitioner Billing Profile", name)
		if name
		else frappe.get_doc({"doctype": "Practitioner Billing Profile", "practitioner": practitioner, "province": province})
	)
	for key, value in values.items():
		if not value:
			continue
		row = next((r for r in profile.billing_values if r.key == key and (r.clinic or None) == clinic), None)
		if row:
			row.value = value
		else:
			profile.append("billing_values", {"key": key, "value": value, "clinic": clinic})
	return profile.save(ignore_permissions=True)


def delete_profiles(practitioner: str):
	for name in frappe.get_all("Practitioner Billing Profile", filters={"practitioner": practitioner}, pluck="name"):
		frappe.delete_doc("Practitioner Billing Profile", name, ignore_permissions=True)
