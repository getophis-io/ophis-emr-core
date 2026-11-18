"""Connections on standard DocTypes the core cannot change (`override_doctype_dashboards`)."""


def employee(data):
	"""An employee's practitioner record and clinics, on the Employee form's Connections tab."""
	data.setdefault("transactions", []).append(
		{"label": "Clinical", "items": ["Practitioner", "Employee Clinic"]}
	)
	fieldnames = data.setdefault("non_standard_fieldnames", {})
	fieldnames["Practitioner"] = "employee"
	fieldnames["Employee Clinic"] = "employee"
	return data
