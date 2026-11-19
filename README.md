# Ophis EMR Core

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Open-source clinical record for Canadian family practice. Ontario comes first.

- **Platform:** Frappe v16 and ERPNext v16, Python 3.14
- **License:** GNU AGPL v3.0 or later
- **Contact:** [dev@getophis.io](mailto:dev@getophis.io)

The clinical record for Canadian family practice, as a Frappe app on ERPNext: DocTypes, the
rules that keep them right, field-level encryption of patient identifiers, and provincial
plug-ins for requisitions and provincial systems. It has no user interface of its own; an app
on top of it (CanChart, for one) supplies the screens.

This is a proof of concept. It is not certified by any province, its provincial integrations run
only in Simulator mode, and it must not be used with real patient information.

## What is in it

One module, `EMR Core`. From ERPNext and Frappe it uses only standard DocTypes: `Company`,
`Branch` (a clinic), `Employee`, `User` and `Role`.

| Area | DocTypes |
|---|---|
| Clinics | `Clinic`, `Patient Clinic`, `Practitioner Clinic`, `Employee Clinic` |
| Billing profiles | `Practitioner Billing Profile`, `Practitioner Billing Profile Value`, `Patient Billing Profile`, `Patient Billing Profile Value`, `Payer`, `Plan Detail Key` (virtual) |
| Registration | `Patient`, `Patient Emergency Contact`, `Patient Family Link` |
| Providers and rooms | `Practitioner`, `Room` |
| Scheduling | `Appointment Type`, `Patient Appointment` |
| Visits | `Encounter`, `Vital Signs`, `Clinical Note` |
| Chart | `Patient Condition`, `Patient Allergy`, `Patient Medication` |
| Orders and results | `Patient Order`, `Patient Referral`, `Lab Result`, `Lab Result Item`, `Received Report` |
| Prescribing | `Prescription`, `Prescription Item`, `Drug Product` |
| Billing | `Billing Claim`, `Billing Claim Item` |
| Billing codes | `Billing Code Set Version`, `Billing Fee Code`, `Billing Fee Rule`, `Billing Diagnosis Code`, `Billing Code Description`, `Billing Favourite` |
| Integrations | `EMR Integration`, `EMR Integration Log` |
| Audit | `Patient Event` |

Rules worth knowing:

- **Health cards.** A patient's health card is their billing profile for the health plan of the
  province that issued it (see Patient billing profiles), not a field of `Patient`. Any province's
  card is recorded; a province module that knows its card's format checks it (Ontario: 10 digits
  and a check digit, a 1–2 letter version code). A number is registered to only one patient per
  province. This is not eligibility; that is the province's validation service (Ontario's Health
  Card Validation), whose result is the profile's verification status.
- **Family Clinic and Walk-in.** A Rostered patient is a Family Clinic patient; anyone else is a
  Walk-in patient. Appointments are booked only as Family Clinic visits, for rostered patients
  with their own practitioner. A practitioner cannot be double-booked.
- **Visit flow.** Checking a patient in opens their `Encounter`, waiting for a nurse. A nurse
  picks it up into a room and hands it over to the physician; the visit's physician can take it
  at any point. A room is occupied while an encounter in it is in progress; that is worked out,
  never stored.
- **Signed means immutable.** Submitting a `Clinical Note` signs it, and only its author can. A
  signed note is corrected by an amendment, which leaves the original on the chart marked Amended.
- **Issued means immutable.** A `Prescription` is fixed once issued and can only be cancelled with
  a reason. Its name is the Rx number. Prescriptions copy from a `Drug Product`, never link to it.
- **Chart entries are never deleted.** They are marked `Entered in Error`.
- **Audit.** Every insert, update, signature and deletion of a record that belongs to a patient is
  written to `Patient Event`. Clinical DocTypes also keep Frappe's Version and View Log.

Roles: `EMR Physician`, `EMR Nurse`, `EMR Reception`, `EMR Clinic Admin`, `EMR Billing Admin`. Reception
can register and book but cannot read the clinical chart. Billing Admin loads and activates billing
code files.

## Patient identifiers are encrypted

Phones, email, emergency contact details, and patient billing profiles' plan details (health card
number and version, membership numbers) and expiry are stored encrypted (Fernet, with keys derived by HKDF), and found by exact value through keyed blind
indexes. The key is the `PII_ENCRYPTION_KEY` environment variable of the bench's processes, never
the database or site config; install and migrate stop without it. Make one with:

```
bench --site <site> execute ophis_emr_core.emr_core.phi.generate_key
```

**Masked on the desk.** The same identifiers (phones, email, emergency contact phone and email,
billing profiles' plan details and expiry) are masked with Frappe's field masking (`mask` on the
field): desk forms, lists and reports show them as `XXXXXXXX` (phones and emails partly) to users
without the `mask` permission. The four EMR roles have it; anyone else who can read the records,
such as a System Manager without an EMR role, sees them masked. Administrator always sees them.
Masking is for display only: the core's own reads and validation see real values.

Keys rotate without downtime; the steps are in the docstring of `ophis_emr_core/emr_core/phi.py`.
`Patient` decrypts on load. Code reading these columns with `frappe.get_all` or
`frappe.db.get_value` gets ciphertext and passes rows through `phi.decrypt_rows`.

## Provinces

A clinic is an ERPNext `Branch` of a `Company`, so staff, shifts and payroll in HRMS line up
with it. Its `Clinic` record (named after the Branch, which saving it creates) holds what Branch
does not: the clinic's province, time zone and holiday list. One site can hold clinics in several
provinces, and each clinic works in its own province's health system
(`emr_core/provinces/`). A document with no clinic to go by uses `clinic_province` in the site
config, default `ON`.

A site with no clinic gets one on install, migrate and the end of ERPNext's setup wizard, copied
from its default company: the company's name, and the province of its address or the site's. A
single-clinic site needs nothing more; a clinic in another branch is added as a `Clinic`.

**Who belongs to a clinic.** Patients, practitioners and employees each belong to one or more
clinics, through `Patient Clinic`, `Practitioner Clinic` and `Employee Clinic` (one record per
person and clinic). Exactly one active clinic is primary: the home clinic, whose company
`Patient.company` and `Practitioner.company` follow, and which is an employee's `branch` in HRMS
(a branch changed in HRMS becomes the primary clinic in turn). A new patient or practitioner joins
the clinic the caller names (`doc.flags.clinic`) or the user's own; on a site with several clinics
one of them is required. A link is ended by making it Inactive. `emr_core/clinics.py` has the
lookups (`get_clinics`, `get_primary_clinic`, `get_members`, `add_to_clinic`, `get_default_clinic`).

**What happens inside one clinic.** `Patient Appointment`, `Encounter` and `Room` each belong to
exactly one clinic (`clinic`, required). An appointment is booked at a clinic its practitioner
works at, by default the user's own or the practitioner's primary one, and moves clinic only while
Scheduled. Its encounter is at the same clinic, a walk-in at the clinic checking them in, and
neither moves. A visit's room is one of its clinic's rooms, and a room stays in its clinic. A
patient booked or seen at a clinic joins it. Room names are unique within a clinic; a room's
record name gets its clinic added in brackets where another clinic already uses the name. A new
clinic starts with the default rooms. `visits.walk_in`, `visits.register_walk_in` and
`rooms.get_free_rooms` take a `clinic`.

**Billing profiles.** What a practitioner bills a province's plan with is on their `Practitioner
Billing Profile` for that province (one per practitioner and province, named
`<practitioner>-<province>`), as key and value rows (`Practitioner Billing Profile Value`). Each
province defines its keys in code, in `emr_core/provinces/<province>.py` (`BILLING_KEYS`: label,
required, format, and whether the value can differ by clinic). Ontario's are `billing_number`
(required), `specialty_code` and `group_number`; the group number can be given per clinic, and a
row without a clinic applies to the rest of the province. A province with no keys yet takes no
profile. Billing code reads values through `emr_core/billing_profiles.py`
(`get_values(practitioner, clinic=...)`).

**Patient billing profiles.** How a patient's care is paid for is one `Patient Billing Profile`
per coverage, of one of four types: Provincial Health Plan, Public Funded, Private or Out of
Pocket. Each but Out of Pocket names its `Payer` (shown as Provider): the site's list of payers,
each of one type, which the clinic keeps; the desk offers only the enabled payers of the profile's
type. A site starts with the health plan of every province and
territory (`provinces.HEALTH_PLANS`: OHIP, MSI, MSP, RAMQ…), Blue Cross (Public Funded), Manulife and
RBC (Private).

A Provincial Health Plan profile has an issuing province, the plan's own, set from it. Every
profile but Out of Pocket has an expiry date and a verification status (Not Checked, Valid, Invalid, with when and the service's
response), set by a verification service and back to Not Checked when the number, version or
expiry changes. Its other details are key and value rows (`Patient Billing Profile Value`), like a
practitioner's, whose keys are set in code against each plan (`PlanKey`: label, required, format):

| Plan | Keys |
|---|---|
| A province's health plan (OHIP, MSI, …) | `health_card_number` (required), `version_code`; a province module can give its own (`PLAN_KEYS` in `provinces/<province>.py`; Ontario checks its formats) |
| Blue Cross | `membership_number` (required), `plan_contract_number`, `owner_name` (default: the patient) |
| Manulife, RBC | `plan_contract_number`, `membership_number` (required), `owner_name` (default: the patient), `employer_name` |
| A payer the clinic adds | its type's (`PLAN_KEYS`: Public Funded as Blue Cross, Private as Manulife) |
| Out of Pocket | none |

Plans and their keys are in `PLAN_KEYS_BY_PAYER` in `emr_core/patient_billing.py`. The desk suggests
the keys of the Provider chosen: a row's Key links to `Plan Detail Key`, a virtual DocType (no
table, nothing stored or synced) whose controller answers from this code, filtered by the
profile's provider. Only the plan's keys are accepted, whatever is typed.

Every patient has an Out of Pocket profile, made with the patient, which cannot be ended. A health
card added is primary. Exactly one active profile is primary; when it ends, the next in billing
order (provincial, public, private, out of pocket) takes over. Values are encrypted and blind-indexed.
`emr_core/patient_billing.py` has the health card (`set_health_card`, `get_health_card`,
`get_health_cards` for lists, `find_patients_by_health_card` for lookups by number) and any
coverage (`get_profiles`, each with its `plan_values`; `get_primary_profile`; `add_profile`).

**Health card moved off Patient.** `Patient.health_card_province`, `health_card_number`,
`health_card_version`, `health_card_expiry`, `health_card_status`, `health_card_checked_on`,
`health_card_response` and `health_card_number_index` are gone. Apps read the card with
`patient_billing.get_health_card(patient)`, write it with `set_health_card`, and find patients by
number with `find_patients_by_health_card` instead of `health_card_number_index`. The patch
`move_health_cards_to_billing_profiles` moves each patient's card, verification included, to their
health plan's profile and clears the old columns; a card it cannot move is left and reported.
`visits.register_walk_in` still takes `health_card_province`, `health_card_number` and
`health_card_version`, and `testing.make_patient` still takes them (as `health_card_*` keyword
arguments) and sets `patient.health_card`.

**Moved fields.** `Practitioner.ohip_billing_number`, `ohip_specialty_code` and
`ohip_group_number` are gone; the patch `move_billing_numbers_to_profiles` moves their values to
the practitioner's Ontario billing profile. `testing.make_practitioner` takes them as
`billing={"billing_number": ...}`.

- **Requisitions** (`emr_core/requisitions/`): one module per province with its test catalogue
  and the mapping onto its PDF form. Ontario's is form 4422-84. Register a province in `FORMS`.
- **Integrations** (`integrations/`): connections to provincial systems and payers, built on the
  `Integration` base class. Each is switched on per site by its `EMR Integration` record and runs
  in Simulator mode (made-up answers, always labelled) until a live connection is built. Ontario
  has Health Card Validation, OHIP and Blue Cross claims, OLIS, HRM and DHDR, all simulated.
- **Drug products** are loaded from Health Canada's Drug Product Database:
  `bench --site <site> execute ophis_emr_core.emr_core.drug_data.import_dpd`.

## Billing codes

Fee codes and billing diagnosis codes of each province's health plan, behind one province-neutral
service (`emr_core/billing_codes/`). Callers pass a jurisdiction (a province and its health plan
`Payer`: `billing_codes.get_jurisdiction(clinic=...)`) and get the codes as of a service date:
`get_fee_code`, `search_fee_codes`, `get_diagnosis_code`, `search_diagnosis_codes`,
`validate_bill_line`, `price_bill_line`, `favourites`, `resolve` (a stored `CodeRef`), and
`build_context` (the patient's age and sex, the practitioner's billing values, and what was already
billed). Money is integer cents in the site's default currency. Results are the dataclasses in
`billing_codes/types.py`.

- **Versions.** Each imported file is a `Billing Code Set Version` of one health plan and kind (Fee
  or Diagnosis), holding its codes. An active version applies from its effective date until the
  next active version's, and a code can have its own dates within it, so a past service date
  still finds the code that applied then. Codes are never edited or deleted.
- **Importing** (`billing_codes/importing.py`). `upload` (or a version made on the desk with its
  file attached) keeps the file and its checksum; the same file again is the same version. A
  background job parses it with the province's importer and stages it, or marks it Failed with
  every bad line. The diff against the version in effect (added, retired, fee, description and
  rule changes) is on the version. `activate` puts it into effect; one activated from the same
  date replaces the old one. `retire` withdraws it and brings back what it replaced (the
  rollback); a retired version can be activated again. Practitioners whose favourite codes'
  fees change are told through `emr_notify`. These functions do not check permissions: the app's
  endpoint does.
- **Rules and pricing.** A fee code carries `Billing Fee Rule` rows. The core checks age, sex,
  specialty, location, per-day and per-year limits, codes that exclude or require each other,
  minimum minutes and maximum units (`billing_codes/rules.py`). A rule type it does not know comes
  back as a warning, never as a pass. Pricing is fee times units; a code with no set fee is not
  priced.
- **Providers** (`billing_codes/providers.py`). Each province's importers, extensions, rules and
  pricing come from a `CodeProvider` registered under the `billing_code_providers` hook. The core
  registers the standard provider for every province (`"*"`). It reads CSV files in the core's
  layout, described in `billing_codes/standard.py` (`standard.template(kind)` gives the header
  row). A province's own provider (Ontario's OHIP files, say) is registered by province and takes
  over. Every provider must pass the shared contract tests (`billing_codes/contract.py`).

## Services for apps built on it

The interface belongs to the app on top; the rules it calls are here, as plain Python:

- `emr_core/scheduling.py` — working hours, open times on the 15-minute grid, `find_openings`.
- `emr_core/availability.py` — who is in now (time clock or working hours), until when, or when next.
- `emr_core/visits.py` — walk-ins and walk-in registration, who may pick up or take a visit,
  open encounters and draft notes, cancelling with a reason.

The stage changes themselves are `Encounter` methods (`pick_up`, `begin`, `hand_over`, `assign`,
`complete`) and `Patient Appointment` methods (`check_in`, `start_encounter`, `mark_no_show`).

## Hooks for apps built on it

| Hook | Called as | For |
|---|---|---|
| `emr_notify_visit` | `(encounter, text, nurse=None, to_physicians=False)` | Telling staff a visit needs them (check-in, pick-up, hand-over) |
| `emr_notify` | `(users, text, doctype, name)` | Telling particular users about a document (a visit assigned to them) |
| `practitioner_working_periods` | `(practitioner, day)` → `[(start, end), …]` or None | A staff schedule's working hours; without an answer, clinic hours (9 to 5, Monday to Friday) |
| `phi_encrypted_fields` | `{DocType: [field, ...]}` | Another app's patient information, encrypted, rotated and redacted with the core's |
| `billing_code_providers` | `{province or "*": "path.to.CodeProvider"}` | A province's fee and diagnosis codes: its file importers, rules and pricing. The last app to register a province wins |

Without a notifier nothing is sent. See `emr_core/notify.py` for the placeholders in `text`.

`ophis_emr_core.emr_core.testing` has factories for synthetic patients, practitioners,
appointments and rooms, for the tests of apps built on the core.

## Installing

```
bench get-app <this repository>
bench --site <site> install-app ophis_emr_core
```

ERPNext v16 must be installed on the site, and `PII_ENCRYPTION_KEY` set in the bench's
environment. Tests run on a site with `allow_tests` set. On a site where ERPNext's setup wizard
has not been run, the tests complete it first for Canada with a synthetic company
(`testing.before_tests`):

```
bench --site <test site> run-tests --app ophis_emr_core
```

## Contributing

Issues and pull requests are welcome. Use synthetic data only: never put real patient information
in an issue, a test, a fixture or a log. Add a test with any change in behaviour.

If you work with an AI coding agent, the project's rules for it are in `AGENTS.md`. Claude Code's
own settings, skills and subagents go in `.claude/`.

## Contact

Questions about development, contributions or security: [dev@getophis.io](mailto:dev@getophis.io).
Report security issues by email rather than in a public issue.

## License

Copyright (C) 2026 STRMLNE

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program
(`license.txt`). If not, see <https://www.gnu.org/licenses/>.

If you run a modified version of this software as a network service, the AGPL requires you to
offer its users the source code of your modified version.
