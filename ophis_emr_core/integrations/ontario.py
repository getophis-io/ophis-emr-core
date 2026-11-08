"""Ontario's integrations.

The Simulator answers below are invented, and say so: their codes and values are not the
real services' codes or anyone's real data. The notes on `requires` are what each real
connection is understood to need; confirm them with the service's owner before building it.
"""

import re

import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime, today

from ophis_emr_core.integrations import Integration, IntegrationError

SIMULATED = "Simulated"


class HealthCardValidation(Integration):
	key = "hcv"
	code = "ON-HCV"
	title = "Health Card Validation"
	description = "Checks an Ontario health number and version code with the Ministry of Health."
	requires = (
		"registration with the Ministry of Health for its Electronic Business Services, its conformance "
		"testing for Health Card Validation, and the service credentials issued after it"
	)

	def validate(self, patient) -> dict:
		def simulate():
			if not patient.health_card_number:
				return outcome(False, "SIM-NO-NUMBER", "No health number on record.")
			if patient.health_card_province != "ON":
				return outcome(False, "SIM-NOT-ONTARIO", "Not an Ontario health card; this service checks Ontario cards only.")
			if patient.health_card_expiry and getdate(patient.health_card_expiry) < getdate(today()):
				return outcome(False, "SIM-EXPIRED", "The card on record has expired.")
			return outcome(True, "SIM-VALID", "Health card passed validation.")

		def outcome(valid, code, message):
			return {"valid": valid, "code": code, "message": f"{message} ({SIMULATED})"}

		result = self.run("Validate health card", simulate, patient)
		patient.health_card_status = "Valid" if result["valid"] else "Invalid"
		patient.health_card_checked_on = now_datetime()
		patient.health_card_response = f"{result['code']}: {result['message']}"[:140]
		patient.save()
		return result


class ClaimsIntegration(Integration):
	"""Shared by the payers: submit a draft claim, then ask what became of it."""

	payer = ""

	def check(self, claim):
		if claim.payer != self.payer:
			frappe.throw(_("{0} claims are not sent through {1}.").format(claim.payer, self.title), IntegrationError)

	def submit(self, claim) -> dict:
		self.check(claim)
		if claim.status != "Draft":
			frappe.throw(_("Only a draft claim can be submitted."), IntegrationError)

		result = self.run(
			"Submit claim",
			lambda: {"reference": f"SIM-{frappe.generate_hash(length=10).upper()}", "message": f"Received ({SIMULATED})"},
			claim,
		)
		self.write(
			claim,
			status="Submitted",
			submitted_on=now_datetime(),
			submission_reference=result["reference"],
			payer_response=result["message"],
		)
		return result

	def refresh(self, claim) -> dict:
		"""Ask the payer for the outcome of a submitted claim."""
		self.check(claim)
		if claim.status != "Submitted":
			frappe.throw(_("Only a submitted claim has an outcome to check."), IntegrationError)

		result = self.run("Check claim", lambda: self.simulate_outcome(claim), claim)
		self.write(
			claim,
			status=result["status"],
			paid_amount=result.get("paid_amount", 0),
			payer_response=result["message"],
		)
		return result

	def simulate_outcome(self, claim) -> dict:
		return {"status": "Paid", "paid_amount": claim.total, "message": f"Paid in full ({SIMULATED})"}

	def write(self, claim, **values):
		claim.update({**values, "simulated": int(self.simulated)})
		claim.flags.from_integration = True
		claim.save(ignore_permissions=True)


class OhipBilling(ClaimsIntegration):
	key = "ohip"
	code = "ON-MCEDT"
	title = "OHIP Billing (MC EDT)"
	payer = "OHIP"
	description = "Sends OHIP claims to the Ministry of Health and collects remittance advice and rejections."
	requires = (
		"Ministry of Health registration for Medical Claims Electronic Data Transfer, conformance testing, the "
		"service credentials issued after it, and claim files written in the Ministry's claim submission format"
	)

	# Schedule of Benefits fee codes are a letter, three digits and a suffix letter, such as A007A.
	FEE_CODE = re.compile(r"^[A-Z]\d{3}[A-Z]$")

	def simulate_outcome(self, claim) -> dict:
		malformed = [item.service_code for item in claim.items if not self.FEE_CODE.match(item.service_code)]
		if malformed:
			return {
				"status": "Rejected",
				"message": f"Not a fee schedule code: {', '.join(malformed)} ({SIMULATED})",
			}
		return super().simulate_outcome(claim)


class BlueCrossBilling(ClaimsIntegration):
	key = "bluecross"
	code = "ON-BLUECROSS"
	title = "Blue Cross Billing"
	payer = "Blue Cross"
	description = "Sends claims for privately insured services to Blue Cross and collects the outcome."
	requires = (
		"a provider agreement with Blue Cross and access to the electronic claims channel it nominates, "
		"which may be a third-party claims network rather than a service of its own"
	)


class Olis(Integration):
	key = "olis"
	code = "ON-OLIS"
	title = "OLIS Lab Results"
	description = "Retrieves laboratory results from the Ontario Laboratories Information System for the clinic's lab orders."
	requires = (
		"onboarding with Ontario Health for OLIS, conformance testing, the issued certificates, and "
		"an HL7 message parser for its queries and results"
	)

	# Made-up results for the simulator: test name fragment → (result, units, reference range, flag).
	SAMPLES = {
		"albumin / creatinine": ("1.2", "mg/mmol", "< 2.0", ""),
		"hba1c": ("6.8", "%", "< 6.0", "High"),
		"glucose": ("5.4", "mmol/L", "3.6 – 6.0", ""),
		"creatinine": ("78", "µmol/L", "45 – 110", ""),
		"lipid": ("LDL-C 2.9", "mmol/L", "< 3.5", ""),
		"tsh": ("2.1", "mIU/L", "0.3 – 4.0", ""),
		"ferritin": ("18", "µg/L", "30 – 300", "Low"),
		"sodium": ("140", "mmol/L", "135 – 145", ""),
		"potassium": ("4.2", "mmol/L", "3.5 – 5.0", ""),
		"alt": ("24", "U/L", "< 40", ""),
		"b12": ("310", "pmol/L", "> 220", ""),
		"cbc": ("Within normal limits", "", "", ""),
	}

	def fetch(self, order) -> dict:
		def simulate():
			if order.status in ("Cancelled", "Entered in Error"):
				return {"results": [], "message": "Nothing to retrieve for a cancelled order."}
			if frappe.db.exists("Lab Result", {"order": order.name}):
				return {"results": [], "message": f"No new results ({SIMULATED})"}

			items = []
			for test in [line.strip() for line in (order.tests or "").splitlines() if line.strip()]:
				sample = next((value for key, value in self.SAMPLES.items() if key in test.lower()), None)
				value, units, reference_range, flag = sample or ("Result on file with the laboratory", "", "", "")
				items.append({"test_name": test, "value": value, "units": units, "reference_range": reference_range, "flag": flag})
			result = frappe.get_doc(
				{
					"doctype": "Lab Result",
					"patient": order.patient,
					"order": order.name,
					"source": f"OLIS ({SIMULATED})",
					"laboratory": "Simulated Laboratory",
					"status": "Final",
					"collected_on": now_datetime(),
					"reported_on": now_datetime(),
					"items": items,
					"simulated": 1,
				}
			).insert(ignore_permissions=True)
			return {"results": [result.name], "message": f"1 result received ({SIMULATED})"}

		result = self.run("Retrieve lab results", simulate, order)
		if result["results"] and order.status == "Ordered":
			order.status = "Completed"
			order.save(ignore_permissions=True)
		return result


class HospitalReportManager(Integration):
	key = "hrm"
	code = "ON-HRM"
	title = "Hospital Report Manager"
	description = "Receives hospital and specialist reports from OntarioMD's Hospital Report Manager."
	requires = (
		"an HRM subscription and onboarding with OntarioMD, its certification, and the delivery account and "
		"decryption key it issues. Real reports arrive per patient, not per referral, and must be matched"
	)

	def fetch(self, referral) -> dict:
		def simulate():
			if referral.status == "Draft":
				return {"reports": [], "message": "Nothing received: the referral has not been sent."}
			if referral.status in ("Cancelled", "Entered in Error") or frappe.db.exists(
				"Received Report", {"referral": referral.name}
			):
				return {"reports": [], "message": f"No new reports ({SIMULATED})"}

			report = frappe.get_doc(
				{
					"doctype": "Received Report",
					"patient": referral.patient,
					"referral": referral.name,
					"source": f"HRM ({SIMULATED})",
					"report_type": "Consultation",
					"report_date": today(),
					"sending_facility": referral.referred_to_clinic or "Simulated Hospital",
					"author": referral.referred_to,
					"content": (
						"SIMULATED REPORT. Not a real consultation.\n\n"
						f"Re: {referral.reason}\n\n"
						"Thank you for referring this patient. The patient was seen and assessed. "
						"Findings and recommendations would appear here."
					),
					"simulated": 1,
				}
			).insert(ignore_permissions=True)
			return {"reports": [report.name], "message": f"1 report received ({SIMULATED})"}

		result = self.run("Retrieve reports", simulate, referral)
		if result["reports"] and referral.status == "Sent":
			referral.status = "Completed"
			referral.save(ignore_permissions=True)
		return result


class DigitalHealthDrugRepository(Integration):
	"""A patient's dispensed drugs, read from the provincial repository when a prescriber asks.

	Nothing read here is stored: the repository stays the source, and what belongs on the
	chart is added to it by the clinician as a medication.

	Outside Simulator mode this follows the "Search Medication Document Bundle" interaction of
	the Ontario Digital Health Drug Repository HL7 FHIR Implementation Guide (v4.0.3, FHIR R4):
	a search of `Bundle` by the patient's health number and date of birth for dispense
	documents, sent as a POST so that no health information travels in a URL.

	The guide routes this through Ontario Health's ONE Access Gateway, which needs a ONE ID PKI
	certificate and an OAuth2 / OpenID Connect token. Those are in separate specifications and
	are not implemented here: the client sends the stored secret as a bearer token and can
	present a client certificate, and whatever else the gateway requires is supplied through
	the integration's Other Settings:

	- `headers`: additional request headers the gateway requires
	- `client_certificate`: [certificate file, key file] for mutual TLS
	- `history_days`: how far back to ask (the repository's own default is 120 days)
	- `patient_identifier_system`: overrides the health number naming system
	"""

	key = "dhdr"
	code = "ON-DHDR"
	title = "Digital Health Drug Repository"
	description = "Shows a patient's dispensed drug history from Ontario's Digital Health Drug Repository when prescribing."
	requires = (
		"onboarding with Ontario Health for the DHDR service, conformance testing, the issued credentials, and "
		"handling of the patient consent rules that govern what may be shown"
	)

	# An identifier, not an address: it labels the number sent with it as an Ontario health
	# number, is the same in every environment, and is never connected to. The server that is
	# called, staging or production, is the integration record's Endpoint URL.
	HEALTH_NUMBER_SYSTEM = "https://fhir.infoway-inforoute.ca/NamingSystem/ca-on-patient-hcn"
	DIN_SYSTEM = "http://hl7.org/fhir/NamingSystem/ca-hc-din"
	# The LOINC code by which the guide identifies a medication dispense document.
	DISPENSE_DOCUMENT = "http://loinc.org|29550-1"
	PAGE_SIZE = 100  # the most the repository returns in one page
	MAX_PAGES = 5
	TIMEOUT_SECONDS = 20
	SEX_CODES = {"Male": "male", "Female": "female"}

	def history(self, patient) -> dict:
		"""{"events": every dispense, newest first, "drugs": one row per drug, "message"}."""
		result = self.run("Drug history", lambda: self.simulate(patient), patient, live=lambda: self.fetch(patient))
		result["drugs"] = self.summarize(patient, result["events"])
		return result

	def simulate(self, patient) -> dict:
		events = []
		medications = frappe.get_all(
			"Patient Medication",
			filters={"patient": patient.name, "status": ("!=", "Entered in Error")},
			fields=["medication_name", "strength", "din", "dose", "frequency", "prescriber"],
			order_by="start_date desc",
		)
		prescribers = dict(frappe.get_all("Practitioner", fields=["name", "practitioner_name"], as_list=True))
		for medication in medications:
			for days_ago in (12, 42):
				events.append(
					{
						"dispensed_on": str(add_days(today(), -days_ago)),
						"drug": " ".join(filter(None, [medication.medication_name, medication.strength])),
						"din": medication.din or "",
						"quantity": 30,
						"quantity_unit": "",
						"days_supply": 30,
						"pharmacy": "Simulated Pharmacy",
						"prescriber": prescribers.get(medication.prescriber) or "",
						"directions": " ".join(filter(None, [medication.dose, medication.frequency])),
					}
				)
		# One drug from elsewhere, so that reconciling the chart can be tried out.
		events.append(
			{
				"dispensed_on": str(add_days(today(), -20)),
				"drug": "Simulated Drug 10 mg",
				"din": "",
				"quantity": 90,
				"quantity_unit": "",
				"days_supply": 90,
				"pharmacy": "Simulated Pharmacy",
				"prescriber": "Simulated outside prescriber",
				"directions": "1 tablet once daily",
			}
		)
		events.sort(key=lambda event: event["dispensed_on"], reverse=True)
		return {"events": events, "message": f"{len(events)} dispensing events ({SIMULATED})"}

	def fetch(self, patient) -> dict:
		import requests

		if not self.settings.endpoint:
			frappe.throw(_("{0} has no endpoint URL set.").format(self.title), IntegrationError)
		if not patient.health_card_number:
			frappe.throw(_("Drug history is looked up by health number, and this patient has none on record."), IntegrationError)

		extra = self.get_extra_settings()
		headers = {"Accept": "application/fhir+json", **(extra.get("headers") or {})}
		secret = self.get_secret()
		if secret:
			headers["Authorization"] = f"Bearer {secret}"
		certificate = tuple(extra["client_certificate"]) if extra.get("client_certificate") else None

		# The version code is not part of the search; the date of birth is required with a health number.
		system = extra.get("patient_identifier_system") or self.HEALTH_NUMBER_SYSTEM
		search = {
			"composition.type": self.DISPENSE_DOCUMENT,
			"composition.patient.identifier": f"{system}|{patient.health_card_number}",
			"composition.patient.birthdate": str(getdate(patient.date_of_birth)),
			"_sort": "-whenprepared",
			"_count": self.PAGE_SIZE,
		}
		if patient.sex in self.SEX_CODES:
			search["composition.patient.gender"] = self.SEX_CODES[patient.sex]
		if extra.get("history_days"):
			since = add_days(today(), -frappe.utils.cint(extra["history_days"]))
			search["composition.medicationdispense.whenprepared"] = f"ge{since}"

		def send(next_page=None):
			try:
				if next_page:
					# The repository builds this link; it is followed as given.
					return requests.get(next_page, headers=headers, cert=certificate, timeout=self.TIMEOUT_SECONDS)
				return requests.post(
					f"{self.settings.endpoint.rstrip('/')}/Bundle/_search",
					data=search,
					headers=headers,
					cert=certificate,
					timeout=self.TIMEOUT_SECONDS,
				)
			except requests.RequestException as error:
				# The exception text can carry the request, and with it the health number.
				frappe.throw(_("{0} could not be reached ({1}).").format(self.title, type(error).__name__), IntegrationError)

		events, notices, blocked = [], [], False
		next_page = None
		for _page in range(self.MAX_PAGES):
			response = send(next_page)
			if response.status_code == 404:
				frappe.throw(_("{0} does not know this health number.").format(self.title), IntegrationError)
			if response.status_code != 200:
				frappe.throw(_("{0} answered with HTTP {1}.").format(self.title, response.status_code), IntegrationError)

			bundle = response.json()
			page = parse_dispense_search(bundle, self.DIN_SYSTEM)
			events += page["events"]
			notices += [notice for notice in page["notices"] if notice not in notices]
			blocked = blocked or page["blocked"]
			next_page = next((link.get("url") for link in bundle.get("link", []) if link.get("relation") == "next"), None)
			if not next_page:
				break

		events.sort(key=lambda event: event["dispensed_on"], reverse=True)
		message = "Access blocked by the patient" if blocked else f"{len(events)} dispensing events"
		return {"events": events, "notices": notices, "blocked": blocked, "message": message}

	def summarize(self, patient, events: list[dict]) -> list[dict]:
		"""One row per drug, with when it was last dispensed and whether the chart has it."""
		chart = frappe.get_all(
			"Patient Medication",
			filters={"patient": patient.name, "status": ("!=", "Entered in Error")},
			fields=["medication_name", "din", "status"],
		)

		def on_chart(drug):
			for medication in chart:
				name = (medication.medication_name or "").strip().lower()
				if (drug["din"] and drug["din"] == medication.din) or (name and name.split()[0] in drug["drug"].lower()):
					return medication.status
			return None

		drugs = {}
		for event in events:  # newest first, so the first one seen of a drug is its latest
			key = event["din"] or event["drug"].lower()
			drug = drugs.setdefault(key, {**event, "last_dispensed": event["dispensed_on"], "dispenses": 0})
			drug["dispenses"] += 1
		for drug in drugs.values():
			drug.pop("dispensed_on")
			supply = frappe.utils.cint(drug["days_supply"])
			drug["runs_out_on"] = str(add_days(drug["last_dispensed"], supply)) if supply and drug["last_dispensed"] else None
			drug["chart_status"] = on_chart(drug)
		return list(drugs.values())


CONSENT_BLOCKED = "CONSENT_EXISTS"


def parse_dispense_search(searchset: dict, din_system: str) -> dict:
	"""{"events", "notices", "blocked"} from a DHDR search result.

	The result is a searchset whose entries are document bundles, one per dispense, each
	carrying the dispense with the medication, prescription, pharmacy and prescriber it
	refers to. Messages from the repository arrive as OperationOutcome entries: a warning
	when the patient has blocked access, a note when a block is temporarily lifted.
	"""
	events, notices, blocked = [], [], False

	def read(bundle):
		nonlocal blocked
		entries = bundle.get("entry", [])
		# References inside a document point at other entries of the same document.
		index = {}
		for entry in entries:
			resource = entry.get("resource") or {}
			for key in (entry.get("fullUrl"), f"{resource.get('resourceType')}/{resource.get('id')}"):
				if key:
					index[key] = resource

		for entry in entries:
			resource = entry.get("resource") or {}
			kind = resource.get("resourceType")
			if kind == "Bundle":
				read(resource)
			elif kind == "OperationOutcome":
				for issue in resource.get("issue", []):
					details = issue.get("details") or {}
					codes = [coding.get("code") for coding in details.get("coding", [])]
					consent = any((code or "").startswith("CONSENT") for code in codes)
					blocked = blocked or CONSENT_BLOCKED in codes
					text = details.get("text") or issue.get("diagnostics")
					# "No matches" and the like are information about the search, not something to act on.
					if text and (consent or issue.get("severity") in ("warning", "error", "fatal")) and text not in notices:
						notices.append(text)
			elif kind == "MedicationDispense":
				events.append(read_dispense(resource, index, din_system))

	read(searchset)
	return {"events": events, "notices": notices, "blocked": blocked}


def read_dispense(dispense: dict, index: dict, din_system: str) -> dict:
	contained = {f"#{item.get('id')}": item for item in dispense.get("contained", [])}

	def resolve(reference):
		target = (reference or {}).get("reference") or ""
		return contained.get(target) or index.get(target) or {}

	def person_name(resource):
		name = (resource.get("name") or [{}])[0]
		if isinstance(name, str):
			return name
		return name.get("text") or " ".join([*name.get("given", []), name.get("family") or ""]).strip()

	concept = dispense.get("medicationCodeableConcept") or resolve(dispense.get("medicationReference")).get("code") or {}
	codings = concept.get("coding") or []
	din = next((coding.get("code") for coding in codings if coding.get("system") == din_system), "")

	# The dispenser is a pharmacy, named directly or through the pharmacist's role there.
	actor = ((dispense.get("performer") or [{}])[0]).get("actor") or {}
	performer = resolve(actor)
	if performer.get("resourceType") == "PractitionerRole":
		performer = resolve(performer.get("organization"))
	pharmacy = performer.get("name") if performer.get("resourceType") == "Organization" else ""

	prescription = (dispense.get("authorizingPrescription") or [{}])[0]
	requester = resolve(prescription).get("requester") or {}
	prescriber = resolve(requester)
	if prescriber.get("resourceType") == "PractitionerRole":
		prescriber = resolve(prescriber.get("practitioner"))

	quantity = dispense.get("quantity") or {}
	return {
		"dispensed_on": (dispense.get("whenPrepared") or dispense.get("whenHandedOver") or "")[:10],
		"drug": concept.get("text") or next((coding.get("display") for coding in codings if coding.get("display")), "") or "Unnamed drug",
		"din": din or "",
		"quantity": quantity.get("value"),
		"quantity_unit": quantity.get("unit") or "",
		"days_supply": (dispense.get("daysSupply") or {}).get("value"),
		"pharmacy": pharmacy or actor.get("display") or (dispense.get("location") or {}).get("display") or "",
		"prescriber": person_name(prescriber) or requester.get("display") or prescription.get("display") or "",
		"directions": ((dispense.get("dosageInstruction") or [{}])[0]).get("text") or "",
	}


INTEGRATIONS = [
	HealthCardValidation,
	OhipBilling,
	BlueCrossBilling,
	Olis,
	HospitalReportManager,
	DigitalHealthDrugRepository,
]
