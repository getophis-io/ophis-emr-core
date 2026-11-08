# AGENTS.md

Guidance for AI coding agents working in this repo (Claude Code, Codex, Cursor, Copilot and others).

## What this is

`ophis_emr_core` ("Ophis EMR Core") — an open-source Frappe v16 app holding the clinical record
for Canadian (Ontario-first) family practice on ERPNext: DocTypes, their rules, patient identifier
encryption and provincial plug-ins. No user interface. POC stage. Licensed AGPL-3.0-or-later.

CanChart (`strmlne_emr`, in `../../emr.git`) is the app built on it: screens, the SPA, the desk
forms for these DocTypes, staff notifications through Raven, the time clock. CanChart imports this
app; this app never imports CanChart or any other app built on it.

- `ophis_emr_core/hooks.py` — app manifest; the patient changelog and Version redaction hooks.
- `ophis_emr_core/install.py` — roles and starting records (appointment types, rooms), on install and migrate.
- `ophis_emr_core/emr_core/` — the only module (`EMR Core`). DocTypes, `validators.py` (health card,
  postal code), `phi.py` (encryption), `events.py` (patient changelog), `rooms.py` (occupancy),
  `chart.py` (chart summary and shared checks), `notify.py` (staff notification hooks),
  `scheduling.py` (working hours, open times), `availability.py` (who is in now), `visits.py`
  (walk-ins and the rules around a visit in progress),
  `drug_data.py` (Health Canada DPD import), `testing.py` (factories shared by tests and by apps
  built on the core).
- `ophis_emr_core/emr_core/requisitions/` — provincial lab requisition forms, one module per province.
- `ophis_emr_core/integrations/` — provincial systems and payers, one module per province.

## Ground rules

- **No UI.** No pages, SPA, desk form scripts or assets. Business rules live here as plain
  functions an app's API wraps; whitelisted endpoints and screen shaping belong to the app. Screens, and desk scripts for these
  DocTypes (through `doctype_js`), belong to the app on top.
- **Never import an app built on the core.** Ask it through a hook instead, as `notify.py` does
  (`emr_notify_visit`, `emr_notify`) and `phi.encrypted_fields` does (`phi_encrypted_fields`).
  Document every hook the core asks in the README.
- **Own clinical DocTypes only.** No Frappe Health / Marley. From ERPNext use standard DocTypes only;
  do not add custom fields to them.
- **Canada only.** Country, currency and regional data are fixed to Canada.
- **DocType names are public.** Apps and existing sites depend on them; renaming one needs a patch
  and a note in the README. ERPNext already owns `Appointment`, `Task` and others: check before naming one.
- **Chart entries are never deleted.** They are marked `Entered in Error`.
- **Signed means immutable.** `Clinical Note` is signed by submit and corrected only through
  `create_amendment`. Never add cancel/delete paths, and never write AI output into a signed record.
- **Issued means immutable.** A `Prescription` is fixed once issued and can only be cancelled with a reason.
- **Patient identifiers are encrypted** (`emr_core/phi.py`). The key is the `PII_ENCRYPTION_KEY`
  environment variable, never the database or site config. Pass encrypted columns read with
  `get_all`/`get_value` through `phi.decrypt_rows`; never write them with `db_set`/`set_value`.
  Look them up through their `*_index` columns with `phi.blind_indexes`. Add new identifiers to
  `ENCRYPTED_FIELDS`; code reads them through `phi.encrypted_fields()`, never the constant.
  **Never change `phi.KDF_CONTEXT`**: it is part of every key, and changing it makes every stored
  value unreadable.
- **Simulator output is never passed off as real.** Every integration runs in Simulator mode until a
  live connection is built, and its answers say they are simulated.
- **Synthetic data only.** No real patient information in fixtures, tests or logs.
- **Tests.** Add a test with any behaviour change. Test modules for DocTypes that link to `User`,
  `Company` or `Employee` set `IGNORE_TEST_RECORD_DEPENDENCIES`.

## Local dev

Run it through the `cemr-bench` stack (`../../cemr-bench`), which mounts this repo at
`/workspace/ophis-emr-core` and links it into the bench as `apps/ophis_emr_core`. From there:

```
docker compose exec -w /home/frappe/frappe-bench frappe bench --site emr.localhost migrate
docker compose exec -w /home/frappe/frappe-bench frappe bench --site test-emr.localhost run-tests --app ophis_emr_core
```

- Tests run on `test-emr.localhost`, never on the dev site.
- Don't push or open PRs unless asked.
