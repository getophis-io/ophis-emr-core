"""Loading a province's published code file, and putting it into effect.

1. **Upload.** `upload` (or a `Billing Code Set Version` made on the desk with its file) keeps
   the original file with its SHA-256 checksum. The same file uploaded again for the same health
   plan and kind is the version already made from it.
2. **Detect and parse.** In a background job, the province's provider finds the importer that
   reads the file, which parses it without writing anything.
3. **Stage.** The codes are written to the version, which becomes Staged; a file that cannot be
   read leaves it Failed with the reasons.
4. **Diff.** The staged codes are compared with the version in effect: added, retired, fee,
   description, rule and other changes (`get_diff`).
5. **Activate.** `activate` puts the version into effect from its effective date. A version
   activated from the same date as an active one replaces it; otherwise each active version
   applies until the next one's date. Practitioners whose favourite codes' fees changed are told
   (the `emr_notify` hook).
6. **Retire.** `retire` withdraws an active version: the one it replaced, or the one before it,
   applies again. That is the rollback, and since codes are never edited it is always safe. A
   retired version can be activated again.

Nothing here checks the caller's permissions: the app's endpoint does (`EMR Billing Admin`).
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime

from ophis_emr_core.emr_core import notify
from ophis_emr_core.emr_core.billing_codes import repository
from ophis_emr_core.emr_core.billing_codes.providers import ParseError, SourceFile, get_provider
from ophis_emr_core.emr_core.billing_codes.types import FEE, KINDS, ParsedCodeSet

VERSION = repository.VERSION
QUEUED, STAGED, ACTIVE, RETIRED, FAILED = "Queued", "Staged", "Active", "Retired", "Failed"


def checksum(content: bytes) -> str:
	return hashlib.sha256(content).hexdigest()


def find_duplicate(payer: str, kind: str, digest: str) -> str | None:
	"""A version already made from this file, unless it failed."""
	return frappe.db.get_value(
		VERSION, {"payer": payer, "kind": kind, "checksum": digest, "status": ("!=", FAILED)}
	)


def read_source_file(file_url: str) -> SourceFile:
	file = frappe.get_doc("File", {"file_url": file_url})
	# The file's own path, from its File record; never from a request.
	with open(file.get_full_path(), "rb") as handle:  # nosemgrep: frappe-security-file-traversal
		return SourceFile(file.file_name, handle.read())


def upload(payer: str, kind: str, filename: str, content: bytes, **details):
	"""Keep a published file as a new code set version and stage it.

	`details` are the version's own fields (`effective_from`, `source_name`, `source_version`,
	`published_on`, `language`); what the file says fills in the rest. Staging runs in a
	background job, at once in tests. Returns the version, or the one already made from the file.
	"""
	if kind not in KINDS:
		frappe.throw(_("Kind must be one of {0}.").format(", ".join(KINDS)))
	existing = find_duplicate(payer, kind, checksum(content))
	if existing:
		return frappe.get_doc(VERSION, existing)
	file = frappe.get_doc(
		{"doctype": "File", "file_name": filename, "content": content, "is_private": 1}
	).insert(ignore_permissions=True)
	version = frappe.get_doc(
		{"doctype": VERSION, "payer": payer, "kind": kind, "source_file": file.file_url, **details}
	).insert(ignore_permissions=True)
	file.db_set({"attached_to_doctype": VERSION, "attached_to_name": version.name})
	return frappe.get_doc(VERSION, version.name)


def enqueue_stage(version: str):
	frappe.enqueue(
		f"{__name__}.stage",
		queue="long",
		timeout=3600,
		version=version,
		now=bool(frappe.flags.in_test),
		enqueue_after_commit=True,
	)


def stage(version: str):
	"""Parse a queued version's file and write its codes. A failure leaves it Failed, nothing written."""
	doc = frappe.get_doc(VERSION, version)
	if doc.status not in (QUEUED, FAILED):
		return doc
	frappe.db.savepoint("billing_code_stage")
	try:
		provider = get_provider(doc.province)
		file = read_source_file(doc.source_file)
		importer = provider.find_importer(file, doc.kind)
		parsed = importer.parse(file)
		check_parsed(parsed, doc.kind)
		delete_rows(doc.name)
		count = write_rows(doc, provider, parsed)
	except Exception as error:
		frappe.db.rollback(save_point="billing_code_stage")
		frappe.clear_messages()
		if not isinstance(error, frappe.ValidationError):
			frappe.log_error(f"Billing code import {doc.name}")
		_save(doc, status=FAILED, import_error=str(error) or type(error).__name__)
		return doc

	_save(
		doc,
		status=STAGED,
		import_error=None,
		importer=importer.name,
		code_count=count,
		source_name=doc.source_name or parsed.source_name or importer.source_name,
		source_version=doc.source_version or parsed.source_version,
		published_on=doc.published_on or parsed.published_on,
		effective_from=doc.effective_from or parsed.effective_from,
		imported_by=frappe.session.user,
		imported_on=now_datetime(),
	)
	_store_diff(doc)
	return doc


def check_parsed(parsed: ParsedCodeSet, kind: str):
	"""What every importer's output must be, whoever wrote the importer."""
	if parsed.kind != kind:
		raise ParseError([_("The file holds {0} codes, not {1} codes.").format(parsed.kind, kind)])
	if not parsed.codes:
		raise ParseError([_("The file holds no codes.")])
	errors, seen = [], set()
	for code in parsed.codes:
		key = (code.code or "").strip().upper()
		if not key:
			errors.append(_("A code is blank."))
		elif key in seen:
			errors.append(_("{0} is in the file more than once.").format(key))
		seen.add(key)
		if not (code.description or "").strip():
			errors.append(_("{0} has no description.").format(key))
		if code.effective_from and code.effective_to and code.effective_to <= code.effective_from:
			errors.append(_("{0} ends before it starts.").format(key))
		if kind == FEE and code.fee_cents is not None and code.fee_cents < 0:
			errors.append(_("{0} has a negative fee.").format(key))
	if errors:
		raise ParseError(errors)


def write_rows(doc, provider, parsed: ParsedCodeSet) -> int:
	"""Bulk-write the parsed codes into the version. Returns how many."""
	table = repository.TABLES[doc.kind]
	stamp, user = now_datetime(), frappe.session.user
	base = {"creation": stamp, "modified": stamp, "owner": user, "modified_by": user, "docstatus": 0}
	codes, rules, texts = [], [], []

	def child(parent, field, idx, **values):
		return {
			**base,
			"name": frappe.generate_hash(length=12),
			"parent": parent,
			"parenttype": table,
			"parentfield": field,
			"idx": idx,
			**values,
		}

	for code in parsed.codes:
		name = frappe.generate_hash(length=12)
		row = {
			**base,
			"name": name,
			"idx": 0,
			"code_set": doc.name,
			"payer": doc.payer,
			"code": code.code.strip().upper(),
			"description": code.description.strip(),
			"effective_from": code.effective_from,
			"effective_to": code.effective_to,
		}
		if doc.kind == FEE:
			row.update(
				short_description=code.short_description,
				section=code.section,
				fee_cents=code.fee_cents or 0,
				no_set_fee=int(code.fee_cents is None),
				fee=(code.fee_cents or 0) / 100,
				fee_components=json.dumps(code.components.as_dict()) if code.components.as_dict() else None,
				unit_type=code.unit_type,
				requires_diagnosis=int(code.requires_diagnosis),
				extensions=provider.encode_extension(code.extensions),
			)
			rules.extend(
				child(
					name,
					"rules",
					idx,
					rule_type=rule.rule_type,
					parameters=json.dumps(rule.parameters, sort_keys=True),
					severity=rule.severity,
					message=rule.message,
				)
				for idx, rule in enumerate(code.rules, 1)
			)
		else:
			row.update(
				category=code.category,
				is_billable=int(code.is_billable),
				extensions=provider.encode_extension(code.extensions),
			)
		texts.extend(
			child(
				name,
				"descriptions",
				idx,
				language=text.language,
				description=text.description,
				short_description=text.short_description,
			)
			for idx, text in enumerate(code.descriptions, 1)
		)
		codes.append(row)

	for doctype, rows in ((table, codes), (repository.RULE, rules), (repository.DESCRIPTION, texts)):
		if rows:
			fields = list(rows[0])
			frappe.db.bulk_insert(doctype, fields, [[row[field] for field in fields] for row in rows])
	return len(codes)


def delete_rows(version: str):
	"""Remove a version's codes. Only for a version that never took effect."""
	for kind, table in repository.TABLES.items():
		parents = frappe.get_all(table, filters={"code_set": version}, pluck="name")
		for start in range(0, len(parents), 1000):
			chunk = parents[start : start + 1000]
			children = (repository.DESCRIPTION, repository.RULE) if kind == FEE else (repository.DESCRIPTION,)
			for child in children:
				frappe.db.delete(child, {"parent": ("in", chunk), "parenttype": table})
		frappe.db.delete(table, {"code_set": version})


def get_diff(doc) -> dict:
	"""What a version changes from the one in effect on its effective date (or today)."""
	day = getdate(doc.effective_from) if doc.effective_from else getdate()
	current = repository.get_version(doc.payer, doc.kind, day)
	against = current.name if current and current.name != doc.name else None
	new = repository.snapshot(doc.kind, doc.name)
	old = repository.snapshot(doc.kind, against) if against else {}
	fields = repository.snapshot_fields(doc.kind)
	diff = {"against": against, "added": sorted(set(new) - set(old)), "retired": sorted(set(old) - set(new))}
	diff.update({"fee_changed": [], "description_changed": [], "rules_changed": [], "other_changed": []})
	for code in sorted(set(new) & set(old)):
		before, after = dict(zip(fields, old[code], strict=True)), dict(zip(fields, new[code], strict=True))
		if before == after:
			continue
		if doc.kind == FEE and (before["fee_cents"], before["no_set_fee"]) != (
			after["fee_cents"],
			after["no_set_fee"],
		):
			diff["fee_changed"].append({"code": code, "old": _cents(before), "new": _cents(after)})
		if before["description"] != after["description"]:
			diff["description_changed"].append(code)
		if before["rules"] != after["rules"]:
			diff["rules_changed"].append(code)
		other = [
			f
			for f in fields
			if f not in ("fee_cents", "no_set_fee", "description", "rules") and before[f] != after[f]
		]
		if other:
			diff["other_changed"].append({"code": code, "fields": other})
	return diff


def summarize(diff: dict, count: int) -> str:
	if not diff["against"]:
		return _("No version in effect to compare with: {0} codes, all new.").format(count)
	return _(
		"From {0}: {1} added, {2} retired, {3} fee changes, {4} description changes, {5} rule changes, {6} other changes."
	).format(
		diff["against"],
		len(diff["added"]),
		len(diff["retired"]),
		len(diff["fee_changed"]),
		len(diff["description_changed"]),
		len(diff["rules_changed"]),
		len(diff["other_changed"]),
	)


def activate(version: str, effective_from=None):
	"""Put a staged (or retired) version into effect from `effective_from` or its own date."""
	doc = frappe.get_doc(VERSION, version)
	if doc.status not in (STAGED, RETIRED):
		frappe.throw(
			_("Only a staged or retired version can be activated; {0} is {1}.").format(doc.name, doc.status)
		)
	if effective_from:
		doc.effective_from = getdate(effective_from)
	if not doc.effective_from:
		frappe.throw(_("Give the date {0} takes effect from.").format(doc.name))
	diff = _store_diff(doc)

	for name in frappe.get_all(
		VERSION,
		filters={
			"payer": doc.payer,
			"kind": doc.kind,
			"status": ACTIVE,
			"effective_from": doc.effective_from,
			"name": ("!=", doc.name),
		},
		pluck="name",
	):
		_save(frappe.get_doc(VERSION, name), status=RETIRED, superseded_by=doc.name, **_by("retired"))
	_save(
		doc,
		status=ACTIVE,
		superseded_by=None,
		retired_by=None,
		retired_on=None,
		effective_from=doc.effective_from,
		**_by("activated"),
	)
	_update_windows(doc.payer, doc.kind)
	notify_favourites(doc, diff)
	return frappe.get_doc(VERSION, doc.name)


def retire(version: str):
	"""Withdraw an active version. What it replaced, or the version before it, applies again."""
	doc = frappe.get_doc(VERSION, version)
	if doc.status != ACTIVE:
		frappe.throw(_("Only an active version can be retired; {0} is {1}.").format(doc.name, doc.status))
	_save(doc, status=RETIRED, **_by("retired"))
	for name in frappe.get_all(VERSION, filters={"superseded_by": doc.name, "status": RETIRED}, pluck="name"):
		_save(
			frappe.get_doc(VERSION, name), status=ACTIVE, superseded_by=None, retired_by=None, retired_on=None
		)
	_update_windows(doc.payer, doc.kind)
	return frappe.get_doc(VERSION, doc.name)


def notify_favourites(doc, diff: dict):
	"""Tell practitioners whose favourite codes' fees change with this version."""
	if doc.kind != FEE or not diff["fee_changed"]:
		return
	changed = {change["code"] for change in diff["fee_changed"]}
	by_practitioner = {}
	for row in frappe.get_all(
		"Billing Favourite",
		filters={"payer": doc.payer, "code": ("in", list(changed))},
		fields=["practitioner", "code"],
	):
		by_practitioner.setdefault(row.practitioner, set()).add(row.code)
	for practitioner, codes in by_practitioner.items():
		user = frappe.db.get_value("Practitioner", practitioner, "user")
		if user:
			notify.notify(
				[user],
				_("{0} fees change from {1} for your favourite codes {2}.").format(
					doc.payer, frappe.format(doc.effective_from, "Date"), ", ".join(sorted(codes))
				),
				VERSION,
				doc.name,
			)


def _cents(values: dict) -> int | None:
	return None if values["no_set_fee"] in ("1", 1) else int(values["fee_cents"] or 0)


def _store_diff(doc) -> dict:
	diff = get_diff(doc)
	_save(doc, diff=json.dumps(diff), diff_summary=summarize(diff, doc.code_count or 0))
	return diff


def _by(action: str) -> dict:
	return {f"{action}_by": frappe.session.user, f"{action}_on": now_datetime()}


def _save(doc, **values):
	doc.update(values)
	doc.flags.from_pipeline = True
	doc.save(ignore_permissions=True)
	doc.flags.from_pipeline = False


def _update_windows(payer: str, kind: str):
	"""Each active version applies until the next one's effective date."""
	active = frappe.get_all(
		VERSION,
		filters={"payer": payer, "kind": kind, "status": ACTIVE},
		fields=["name", "effective_from", "effective_to"],
		order_by="effective_from asc",
	)
	for current, following in zip(active, [*active[1:], None], strict=True):
		end = following.effective_from if following else None
		if (getdate(current.effective_to) if current.effective_to else None) != (
			getdate(end) if end else None
		):
			frappe.db.set_value(VERSION, current.name, "effective_to", end, update_modified=False)
	repository.clear_cache(payer, kind)
