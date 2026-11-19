"""Reading codes out of the shared tables, as of a service date.

A date is answered by the active code set version whose window holds it (from its effective
date until the next active version's), and within it by the code whose own dates hold it. The
active versions of each health plan and kind are cached until a version is activated or retired.
"""

import datetime
import json
from dataclasses import replace

import frappe
from frappe.utils import getdate

from ophis_emr_core.emr_core.billing_codes.providers import CodeProvider
from ophis_emr_core.emr_core.billing_codes.types import (
	DEFAULT_LANGUAGE,
	DIAGNOSIS,
	FEE,
	BillingRule,
	Description,
	DiagnosisCode,
	FeeCode,
	FeeComponents,
	Page,
	SearchQuery,
)

VERSION = "Billing Code Set Version"
TABLES = {FEE: "Billing Fee Code", DIAGNOSIS: "Billing Diagnosis Code"}
RULE = "Billing Fee Rule"
DESCRIPTION = "Billing Code Description"
CACHE_PREFIX = "ophis_emr_core:billing_code_sets"

FIELDS = {
	FEE: [
		"name",
		"code",
		"description",
		"short_description",
		"section",
		"fee_cents",
		"no_set_fee",
		"fee_components",
		"unit_type",
		"requires_diagnosis",
		"effective_from",
		"effective_to",
		"extensions",
		"code_set",
	],
	DIAGNOSIS: [
		"name",
		"code",
		"description",
		"category",
		"is_billable",
		"effective_from",
		"effective_to",
		"extensions",
		"code_set",
	],
}


def _cache_key(payer: str, kind: str) -> str:
	return f"{CACHE_PREFIX}:{payer}:{kind}"


def active_versions(payer: str, kind: str) -> list[dict]:
	"""The active versions of a health plan's codes of one kind, oldest first."""
	return frappe.cache.get_value(
		_cache_key(payer, kind),
		lambda: frappe.get_all(
			VERSION,
			filters={"payer": payer, "kind": kind, "status": "Active"},
			fields=["name", "effective_from", "effective_to", "language"],
			order_by="effective_from asc",
		),
	)


def clear_cache(payer: str, kind: str):
	frappe.cache.delete_value(_cache_key(payer, kind))


def get_version(payer: str, kind: str, day: datetime.date) -> dict | None:
	"""The active version that applies on `day`."""
	for version in reversed(active_versions(payer, kind)):
		if getdate(version.effective_from) <= day and (
			not version.effective_to or day < getdate(version.effective_to)
		):
			return version
	return None


def get_code(provider: CodeProvider, payer: str, kind: str, code: str, day: datetime.date):
	version = get_version(payer, kind, day)
	if not version:
		return None
	rows = frappe.get_all(
		TABLES[kind],
		filters={"code_set": version.name, "code": code.strip().upper()},
		fields=FIELDS[kind],
	)
	rows = [row for row in rows if _in_effect(row, day)]
	return _build(provider, kind, rows, version.language)[0] if rows else None


def search(provider: CodeProvider, payer: str, kind: str, query: SearchQuery) -> Page:
	day = getdate(query.service_date) if query.service_date else getdate()
	empty = Page([], 0, query.start, query.page_length)
	version = get_version(payer, kind, day)
	if not version:
		return empty

	table = TABLES[kind]
	values = {"code_set": version.name, "day": day, "parenttype": table}
	conditions = [
		"t.code_set = %(code_set)s",
		"(t.effective_from is null or t.effective_from <= %(day)s)",
		"(t.effective_to is null or t.effective_to > %(day)s)",
	]
	if query.section:
		conditions.append(f"t.{'section' if kind == FEE else 'category'} = %(section)s")
		values["section"] = query.section
	if query.billable_only and kind == DIAGNOSIS:
		conditions.append("t.is_billable = 1")
	words = query.text.split()
	for index, word in enumerate(words):
		escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
		values[f"prefix{index}"] = f"{escaped.upper()}%"
		values[f"word{index}"] = f"%{escaped}%"
		conditions.append(
			f"(t.code like %(prefix{index})s or t.description like %(word{index})s"
			f" or exists (select 1 from `tab{DESCRIPTION}` d where d.parent = t.name"
			f" and d.parenttype = %(parenttype)s and d.description like %(word{index})s))"
		)
	where = " and ".join(conditions)
	values["exact"] = query.text.strip().upper()
	values["start"], values["length"] = int(query.start), int(query.page_length)

	total = frappe.db.sql(f"select count(*) from `tab{table}` t where {where}", values)[0][0]
	names = [
		row[0]
		for row in frappe.db.sql(
			f"select t.name from `tab{table}` t where {where}"
			" order by (t.code = %(exact)s) desc, t.code asc limit %(start)s, %(length)s",
			values,
		)
	]
	if not names:
		return Page([], total, query.start, query.page_length)
	rows = {
		row.name: row for row in frappe.get_all(table, filters={"name": ("in", names)}, fields=FIELDS[kind])
	}
	codes = _build(provider, kind, [rows[name] for name in names], version.language)
	if query.language:
		codes = [in_language(code, query.language) for code in codes]
	return Page(codes, total, query.start, query.page_length)


def in_language(code, language: str | None):
	"""The code with its description in `language`, where it has one."""
	match = next((d for d in code.descriptions if d.language == language), None)
	if not match:
		return code
	changes = {"description": match.description}
	if isinstance(code, FeeCode):
		changes["short_description"] = match.short_description or code.short_description
	return replace(code, **changes)


def snapshot(kind: str, code_set: str) -> dict[str, tuple]:
	"""{code: what a diff compares} of every code in a version."""
	fields = FIELDS[kind][1:-2]
	rows = frappe.get_all(TABLES[kind], filters={"code_set": code_set}, fields=["name", *fields])
	rules = {}
	if kind == FEE:
		for parent, rule_type, parameters, severity in frappe.db.sql(
			f"""select r.parent, r.rule_type, r.parameters, r.severity
			from `tab{RULE}` r join `tab{TABLES[FEE]}` t on r.parent = t.name
			where t.code_set = %s and r.parenttype = %s""",
			(code_set, TABLES[FEE]),
		):
			rules.setdefault(parent, []).append((rule_type, _canonical(parameters), severity))
	return {
		row.code: (
			*(str(row.get(field)) if row.get(field) is not None else None for field in fields),
			tuple(sorted(rules.get(row.name, []))),
		)
		for row in rows
	}


def snapshot_fields(kind: str) -> list[str]:
	return [*FIELDS[kind][1:-2], "rules"]


def _canonical(raw) -> str:
	return json.dumps(json.loads(raw) if isinstance(raw, str) and raw else raw or {}, sort_keys=True)


def _in_effect(row, day: datetime.date) -> bool:
	return (not row.effective_from or getdate(row.effective_from) <= day) and (
		not row.effective_to or day < getdate(row.effective_to)
	)


def _children(doctype: str, parenttype: str, parents: list[str]) -> dict[str, list]:
	if doctype == RULE:
		fields = ["parent", "rule_type", "parameters", "severity", "message"]
	else:
		fields = ["parent", "language", "description", "short_description"]
	children = {}
	for row in frappe.get_all(
		doctype,
		filters={"parent": ("in", parents), "parenttype": parenttype},
		fields=fields,
		order_by="idx asc",
	):
		children.setdefault(row.parent, []).append(row)
	return children


def _build(provider: CodeProvider, kind: str, rows: list, language: str | None) -> list:
	names = [row.name for row in rows]
	descriptions = _children(DESCRIPTION, TABLES[kind], names)
	rules = _children(RULE, TABLES[FEE], names) if kind == FEE else {}
	codes = []
	for row in rows:
		texts = (
			Description(language or DEFAULT_LANGUAGE, row.description, row.get("short_description")),
			*(
				Description(d.language, d.description, d.short_description)
				for d in descriptions.get(row.name, [])
			),
		)
		common = dict(
			code=row.code,
			description=row.description,
			effective_from=getdate(row.effective_from) if row.effective_from else None,
			effective_to=getdate(row.effective_to) if row.effective_to else None,
			descriptions=texts,
			code_set=row.code_set,
		)
		if kind == FEE:
			components = json.loads(row.fee_components) if row.fee_components else {}
			codes.append(
				FeeCode(
					**common,
					short_description=row.short_description,
					section=row.section,
					fee_cents=None if row.no_set_fee else row.fee_cents,
					components=FeeComponents(**components),
					unit_type=row.unit_type,
					requires_diagnosis=bool(row.requires_diagnosis),
					rules=tuple(
						BillingRule(
							r.rule_type,
							json.loads(r.parameters)
							if isinstance(r.parameters, str) and r.parameters
							else r.parameters or {},
							r.severity,
							r.message,
						)
						for r in rules.get(row.name, [])
					),
					extensions=provider.decode_extension(provider.fee_extension, row.extensions),
				)
			)
		else:
			codes.append(
				DiagnosisCode(
					**common,
					category=row.category,
					is_billable=bool(row.is_billable),
					extensions=provider.decode_extension(provider.diagnosis_extension, row.extensions),
				)
			)
	return codes
