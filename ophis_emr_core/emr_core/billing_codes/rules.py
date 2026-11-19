"""Checking a bill line against its fee code's rules.

The core knows the rule types every province has (`CORE_RULES`). A province's rule engine
subclasses `RuleEngine`, adds its own types to `handlers` and can check a whole line in
`check_line` (its modifiers, say). A rule of a type nobody knows is reported as not checked,
never passed silently.

Parameters of the core types:

| Type | Parameters | Passes when |
|---|---|---|
| `age` | `min`, `max` (whole years, both inclusive, either optional) | the patient's age on the service date is in range |
| `sex` | `allowed`: ["Female", "Male", ...] | the patient's sex is one of them |
| `specialty` | `allowed`: specialty codes | the practitioner's specialty is one of them |
| `location` | `allowed`: location codes | the service's location is one of them |
| `max_per_day` | `count` | the code is billed at most `count` times for the patient that day |
| `max_per_year` | `count` | ... in the service date's calendar year |
| `not_with` | `codes` | none of them is billed in the same visit |
| `requires` | `codes` | at least one of them is billed in the same visit |
| `min_minutes` | `minutes` | the service's start and end times are at least that far apart |
| `max_units` | `units` | the line has at most that many units |
"""

from collections.abc import Callable

from ophis_emr_core.emr_core.billing_codes.types import (
	ERROR,
	WARNING,
	BillingContext,
	BillingRule,
	BillLineDraft,
	FeeCode,
	Issue,
)

Handler = Callable[[BillingRule, FeeCode, BillLineDraft, BillingContext], str | None]


def _words(values) -> set[str]:
	return {str(value).strip().lower() for value in values or ()}


def check_age(rule, fee, line, ctx):
	if ctx.age is None:
		return "The patient's date of birth is needed to check the age limit"
	low, high = rule.parameters.get("min"), rule.parameters.get("max")
	if (low is not None and ctx.age < low) or (high is not None and ctx.age > high):
		limits = " to ".join(str(x) for x in (low, high) if x is not None)
		return f"{fee.code} is for patients aged {limits}; the patient is {ctx.age}"


def check_sex(rule, fee, line, ctx):
	allowed = rule.parameters.get("allowed") or []
	if not ctx.sex:
		return "The patient's sex is needed to check this code"
	if ctx.sex.lower() not in _words(allowed):
		return f"{fee.code} is only for {', '.join(allowed)} patients"


def check_specialty(rule, fee, line, ctx):
	allowed = rule.parameters.get("allowed") or []
	if not ctx.specialty:
		return "The practitioner's specialty is needed to check this code"
	if ctx.specialty.lower() not in _words(allowed):
		return f"{fee.code} cannot be billed by specialty {ctx.specialty}"


def check_location(rule, fee, line, ctx):
	allowed = rule.parameters.get("allowed") or []
	if not ctx.location:
		return "The service location is needed to check this code"
	if ctx.location.lower() not in _words(allowed):
		return f"{fee.code} cannot be billed at location {ctx.location}"


def _count_same_visit(fee, ctx) -> int:
	return sum(1 for other in ctx.other_lines if other.fee_code.upper() == fee.code)


def check_max_per_day(rule, fee, line, ctx):
	limit = int(rule.parameters.get("count", 1))
	if ctx.history.same_day.get(fee.code, 0) + _count_same_visit(fee, ctx) + 1 > limit:
		return f"{fee.code} can be billed {limit} time(s) a day for a patient"


def check_max_per_year(rule, fee, line, ctx):
	limit = int(rule.parameters.get("count", 1))
	if ctx.history.same_year.get(fee.code, 0) + _count_same_visit(fee, ctx) + 1 > limit:
		return f"{fee.code} can be billed {limit} time(s) a year for a patient"


def check_not_with(rule, fee, line, ctx):
	codes = {code.upper() for code in rule.parameters.get("codes") or ()}
	clash = sorted(codes & {other.fee_code.upper() for other in ctx.other_lines})
	if clash:
		return f"{fee.code} cannot be billed with {', '.join(clash)}"


def check_requires(rule, fee, line, ctx):
	codes = [code.upper() for code in rule.parameters.get("codes") or ()]
	if codes and not set(codes) & {other.fee_code.upper() for other in ctx.other_lines}:
		return f"{fee.code} must be billed with one of {', '.join(codes)}"


def check_min_minutes(rule, fee, line, ctx):
	minutes = int(rule.parameters.get("minutes", 0))
	if line.minutes is None:
		return f"{fee.code} needs the service's start and end times"
	if line.minutes < minutes:
		return f"{fee.code} needs at least {minutes} minutes; this service took {line.minutes}"


def check_max_units(rule, fee, line, ctx):
	limit = int(rule.parameters.get("units", 1))
	if (line.units or 1) > limit:
		return f"{fee.code} can be billed for at most {limit} unit(s)"


CORE_RULES: dict[str, Handler] = {
	"age": check_age,
	"sex": check_sex,
	"specialty": check_specialty,
	"location": check_location,
	"max_per_day": check_max_per_day,
	"max_per_year": check_max_per_year,
	"not_with": check_not_with,
	"requires": check_requires,
	"min_minutes": check_min_minutes,
	"max_units": check_max_units,
}


class RuleEngine:
	"""Checks a line against its fee code's rules. A province's engine adds to `handlers`."""

	def __init__(self):
		self.handlers: dict[str, Handler] = dict(CORE_RULES)

	def check(self, fee: FeeCode, line: BillLineDraft, ctx: BillingContext) -> list[Issue]:
		issues = []
		for rule in fee.rules:
			handler = self.handlers.get(rule.rule_type)
			if not handler:
				issues.append(
					Issue(rule.rule_type, WARNING, f"Rule {rule.rule_type} of {fee.code} was not checked")
				)
				continue
			problem = handler(rule, fee, line, ctx)
			if problem:
				issues.append(Issue(rule.rule_type, rule.severity or ERROR, rule.message or problem))
		issues.extend(self.check_line(fee, line, ctx))
		return issues

	def check_line(self, fee: FeeCode, line: BillLineDraft, ctx: BillingContext) -> list[Issue]:
		"""Province checks of the line as a whole, beyond its code's rules. None in the core."""
		return []
