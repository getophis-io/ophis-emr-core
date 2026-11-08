"""Ontario Ministry of Health Laboratory Requisition, form 4422-84 (2026/04).

`field` on a test is the checkbox it ticks on the PDF template; `buttons` ticks more than one.
The template's own field names are kept as they are, including the misnamed ones: the
"Other Tests" lines carry the names of microbiology rows, and `y_birth2` is the date of birth.
"""

import frappe
from frappe import _
from frappe.utils import getdate

CHECKED = "/Yes"
# The hematology, immunology and microbiology checkboxes all use this state name on the template.
COLUMN_2 = "/fasting"


def test(code, label, field=None, state=CHECKED, **extra):
	return {"code": code, "label": label, "buttons": {field: state} if field else {}, **extra}


FORM = {
	"code": "ON-4422-84",
	"title": "Ontario Laboratory Requisition",
	"template": "on_4422_84.pdf",
	"other_test_lines": 11,
	"sections": [
		{
			"title": "Biochemistry",
			"tests": [
				test("glucose_random", "Glucose, random", "row1.0", buttons={"row1.0": CHECKED, "biochemistry": "/ran"}),
				test("glucose_fasting", "Glucose, fasting", "row1.0", buttons={"row1.0": CHECKED, "biochemistry": "/fasting"}),
				test("hba1c", "HbA1C", "row1.1"),
				test("creatinine", "Creatinine (eGFR)", "row1.2"),
				test("uric_acid", "Uric Acid", "row1.3"),
				test("sodium", "Sodium", "row1.4"),
				test("potassium", "Potassium", "row1.5"),
				test("alt", "ALT", "row1.6"),
				test("alk_phosphatase", "Alk. Phosphatase", "row1.7"),
				test("bilirubin", "Bilirubin", "row1.8"),
				test("albumin", "Albumin", "row1.9"),
				test("lipid_assessment", "Lipid Assessment", "row1.13"),
				test("acr_urine", "Albumin / Creatinine Ratio, Urine", "row1.14"),
				test("urinalysis", "Urinalysis (Chemical)", "row1.15"),
				test("neonatal_bilirubin", "Neonatal Bilirubin", "row1.16"),
				test("tdm", "Therapeutic Drug Monitoring", "row1.20"),
			],
		},
		{
			"title": "Hematology",
			"tests": [
				test("cbc", "CBC", "hermatology1.0", COLUMN_2),
				test("inr", "Prothrombin Time (INR)", "hermatology1.1", COLUMN_2),
			],
		},
		{
			"title": "Immunology",
			"tests": [
				test("pregnancy_test", "Pregnancy Test (Urine)", "hermatology1.3", COLUMN_2),
				test("mono_screen", "Mononucleosis Screen", "hermatology1.4", COLUMN_2),
				test("rubella", "Rubella", "hermatology1.5", COLUMN_2),
				test("prenatal_screen", "Prenatal: ABO, RhD, Antibody Screen", "hermatology1.6", COLUMN_2),
				test("repeat_prenatal_antibodies", "Repeat Prenatal Antibodies", "hermatology1.7", COLUMN_2),
			],
		},
		{
			"title": "Microbiology ID & Sensitivities",
			"note": "If warranted",
			"tests": [
				test("micro_cervical", "Cervical", "hermatology1.8", COLUMN_2, summary="Cervical culture"),
				test("micro_vaginal", "Vaginal", "hermatology1.9", COLUMN_2, summary="Vaginal culture"),
				test("micro_gbs", "Vaginal / Rectal – Group B Strep", "hermatology1.10", COLUMN_2),
				test("micro_chlamydia", "Chlamydia", "hermatology1.11", COLUMN_2),
				test("micro_gc", "GC", "hermatology1.12", COLUMN_2),
				test("micro_sputum", "Sputum", "hermatology1.13", COLUMN_2, summary="Sputum culture"),
				test("micro_throat", "Throat", "hermatology1.14", COLUMN_2, summary="Throat culture"),
				test("micro_wound", "Wound", "hermatology1.15", COLUMN_2, summary="Wound culture"),
				test("micro_urine", "Urine", "hermatology1.16", COLUMN_2, summary="Urine culture"),
				test("micro_stool_culture", "Stool Culture", "hermatology1.17", COLUMN_2),
				test("micro_stool_op", "Stool Ova & Parasites", "hermatology1.18", COLUMN_2),
				test("micro_other_swab", "Other Swabs / Pus", "hermatology1.19", COLUMN_2),
			],
		},
		{
			"title": "Viral Hepatitis",
			"note": "Acute, chronic or immune status: one only",
			"tests": [
				test("hepatitis_acute", "Acute Hepatitis", "acuteHep", "/ahep"),
				test("hepatitis_chronic", "Chronic Hepatitis", "acuteHep", "/ch"),
				test("hepatitis_immune_a", "Immune Status: Hepatitis A", buttons={"acuteHep": "/imm", "viralHepatits.3a": "/a"}),
				test("hepatitis_immune_b", "Immune Status: Hepatitis B", buttons={"acuteHep": "/imm", "viralHepatits.3b": "/b"}),
				test("hepatitis_immune_c", "Immune Status: Hepatitis C", buttons={"acuteHep": "/imm", "viralHepatits.3c": "/c"}),
			],
		},
		{
			"title": "Prostate Specific Antigen (PSA)",
			"tests": [
				test("psa_total", "Total PSA", "TotalPSA", "/T"),
				test("psa_free", "Free PSA", "FreePSA", "/F"),
			],
		},
		{
			"title": "Vitamin D (25-Hydroxy)",
			"tests": [
				test(
					"vitamin_d_insured",
					"Insured: meets OHIP eligibility criteria",
					"insuredvitD",
					"/i",
					summary="Vitamin D (25-Hydroxy), insured",
				),
				test(
					"vitamin_d_uninsured",
					"Uninsured: patient responsible for payment",
					"insuredvitD",
					"/u",
					summary="Vitamin D (25-Hydroxy), uninsured",
				),
			],
		},
	],
	# Questions the form asks beyond which tests. `tests` limits one to orders with those tests.
	"details": [
		{
			"key": "payer",
			"label": "Payer",
			"options": ["OHIP/Insured", "Third Party / Uninsured", "WSIB"],
			"default": "OHIP/Insured",
		},
		{
			"key": "psa_coverage",
			"label": "PSA coverage",
			"tests": ["psa_total", "psa_free"],
			"options": [
				"Insured: suspected or diagnosed prostate cancer",
				"Uninsured: asymptomatic screening, patient pays",
			],
			"required": True,
		},
		{"key": "chlamydia_source", "label": "Chlamydia: source", "tests": ["micro_chlamydia"]},
		{"key": "gc_source", "label": "GC: source", "tests": ["micro_gc"]},
		{"key": "wound_source", "label": "Wound: source", "tests": ["micro_wound"]},
		{"key": "swab_source", "label": "Other swabs / pus: source", "tests": ["micro_other_swab"]},
		{"key": "child_age_days", "label": "Child's age: days", "tests": ["neonatal_bilirubin"]},
		{"key": "child_age_hours", "label": "Child's age: hours", "tests": ["neonatal_bilirubin"]},
		{"key": "drug_1", "label": "Name of drug #1", "tests": ["tdm"]},
		{"key": "drug_2", "label": "Name of drug #2", "tests": ["tdm"]},
	],
}

PAYER_STATES = {"OHIP/Insured": "/ohip", "Third Party / Uninsured": "/un", "WSIB": "/wsib"}

# Each tuple is a set of choices of which the form takes only one.
EXCLUSIVE = (
	("Glucose", ("glucose_random",), ("glucose_fasting",)),
	(
		"Viral Hepatitis",
		("hepatitis_acute",),
		("hepatitis_chronic",),
		("hepatitis_immune_a", "hepatitis_immune_b", "hepatitis_immune_c"),
	),
	("Vitamin D", ("vitamin_d_insured",), ("vitamin_d_uninsured",)),
)

# The template's "Other Tests" lines, top to bottom.
OTHER_TEST_FIELDS = [
	f"Other Tests – one test per line, {name}"
	for name in (
		"GC specify source",
		"Sputum",
		"Throat",
		"Wound specify source",
		"Urine",
		"Stool Culture",
		"Stool Ova & Parasites",
		"Other Swabs  Pus specify source",
		"Row 15",
		"Row 16",
		"Row 17",
	)
]


def validate(selected: list[str], details: dict):
	for title, *groups in EXCLUSIVE:
		if sum(1 for group in groups if set(group) & set(selected)) > 1:
			frappe.throw(_("{0}: the requisition takes only one of these choices.").format(_(title)))
	for detail in FORM["details"]:
		if detail.get("required") and set(detail["tests"]) & set(selected) and not details.get(detail["key"]):
			frappe.throw(_("{0} is required for the tests chosen.").format(detail["label"]))


def get_field_values(order) -> tuple[dict, dict]:
	"""(text field values, checkbox and radio states) for the PDF template."""
	from ophis_emr_core.emr_core.requisitions import get_tests, lines

	patient = frappe.get_doc("Patient", order.patient)
	practitioner = frappe.get_doc("Practitioner", order.practitioner) if order.practitioner else frappe._dict()
	selected = frappe.parse_json(order.selected_tests) or []
	details = frappe.parse_json(order.requisition_details) or {}
	other_tests = lines(order.other_tests) if selected or lines(order.other_tests) else lines(order.tests)

	born = getdate(patient.date_of_birth)
	insured_here = patient.health_card_province == "ON"
	text = {
		"name": practitioner.practitioner_name or "",
		"address": get_clinic_address(practitioner.company or patient.company),
		"ClinicianPractitioner Number": practitioner.ohip_billing_number or "",
		"CPSO  Registration No": practitioner.college_registration_number or "",
		"contactNumber1": practitioner.phone or "",
		"Additional Clinical Information eg diagnosis": order.clinical_information or "",
		"Health Number": (patient.health_card_number or "") if insured_here else "",
		"version": (patient.health_card_version or "") if insured_here else "",
		"Province": patient.health_card_province if patient.health_card_number else "",
		"Other Provincial Registration Number": "" if insured_here else (patient.health_card_number or ""),
		"y_birth2": f"{born.year:04d}",
		"m_birth2": f"{born.month:02d}",
		"d_birth2": f"{born.day:02d}",
		"patientTelephone": patient.mobile_phone or patient.home_phone or "",
		"Patient’s Last Name as per OHIP Card": patient.last_name,
		"patienFname": patient.first_name,
		"patienLname": patient.middle_name or "",
		"Patient’s Address including Postal Code": ", ".join(
			filter(None, [patient.address_line1, patient.address_line2, patient.city, patient.province, patient.postal_code])
		),
		"Date": getdate(order.order_date).strftime("%Y/%m/%d"),
		"specify1": details.get("chlamydia_source", ""),
		"specify2": details.get("gc_source", ""),
		"specify3": details.get("wound_source", ""),
		"OtherSwabs": details.get("swab_source", ""),
		"chidAge": details.get("child_age_days", ""),
		"chidHours": details.get("child_age_hours", ""),
		"drugName1": details.get("drug_1", ""),
		"drugName2": details.get("drug_2", ""),
	}
	if "neonatal_bilirubin" in selected:
		text["clinicianTel"] = practitioner.phone or ""
		text["patientTel"] = patient.mobile_phone or patient.home_phone or ""
	text.update(dict(zip(OTHER_TEST_FIELDS, other_tests)))

	buttons = {"checkOne": PAYER_STATES.get(details.get("payer") or "OHIP/Insured", "/ohip")}
	# The form has boxes for M and F only.
	if patient.sex in ("Male", "Female"):
		buttons["sex"] = f"/{patient.sex[0]}"
	if details.get("psa_coverage"):
		buttons["insuredPSA"] = "/i" if details["psa_coverage"].startswith("Insured") else "/u"
	tests = get_tests(FORM)
	for code in selected:
		buttons.update(tests[code]["buttons"])
	return text, buttons


def get_clinic_address(company: str | None) -> str:
	"""The clinic's name and address, as far as they are on record."""
	company = company or frappe.defaults.get_global_default("company")
	if not company:
		return ""
	from frappe.contacts.doctype.address.address import get_default_address

	parts = [frappe.db.get_value("Company", company, "company_name")]
	address = get_default_address("Company", company)
	if address:
		row = frappe.db.get_value(
			"Address", address, ["address_line1", "address_line2", "city", "state", "pincode"], as_dict=True
		)
		parts += [row.address_line1, row.address_line2, row.city, row.state, row.pincode]
	return ", ".join(filter(None, parts))
