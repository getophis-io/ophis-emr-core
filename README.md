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
| Billing profiles | `Practitioner Billing Profile`, `Practitioner Billing Profile Value` |
| Registration | `Patient`, `Patient Emergency Contact`, `Patient Family Link` |
| Providers and rooms | `Practitioner`, `Room` |
| Scheduling | `Appointment Type`, `Patient Appointment` |
| Visits | `Encounter`, `Vital Signs`, `Clinical Note` |
| Chart | `Patient Condition`, `Patient Allergy`, `Patient Medication` |
| Orders and results | `Patient Order`, `Patient Referral`, `Lab Result`, `Lab Result Item`, `Received Report` |
| Prescribing | `Prescription`, `Prescription Item`, `Drug Product` |
| Billing | `Billing Claim`, `Billing Claim Item` |
| Integrations | `EMR Integration`, `EMR Integration Log` |
| Audit | `Patient Event` |

Rules worth knowing:

- **Health cards.** Ontario numbers are checked for format and check digit, and a number can be
  registered to only one patient per province. This is not eligibility; that is Health Card
  Validation.
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

Roles: `EMR Physician`, `EMR Nurse`, `EMR Reception`, `EMR Clinic Admin`. Reception can register and
book but cannot read the clinical chart.

## Patient identifiers are encrypted

Health card number, version and expiry, phones, email and emergency contact details are stored
encrypted (Fernet, with keys derived by HKDF), and found by exact value through keyed blind
indexes. The key is the `PII_ENCRYPTION_KEY` environment variable of the bench's processes, never
the database or site config; install and migrate stop without it. Make one with:

```
bench --site <site> execute ophis_emr_core.emr_core.phi.generate_key
```

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

Without a notifier nothing is sent. See `emr_core/notify.py` for the placeholders in `text`.

`ophis_emr_core.emr_core.testing` has factories for synthetic patients, practitioners,
appointments and rooms, for the tests of apps built on the core.

## Installing

```
bench get-app <this repository>
bench --site <site> install-app ophis_emr_core
```

ERPNext v16 must be installed on the site, and `PII_ENCRYPTION_KEY` set in the bench's
environment. Tests run on a site with `allow_tests` set:

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
