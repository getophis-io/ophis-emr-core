"""Pricing a bill line.

The core prices a line as its code's fee times its units. A province's calculator subclasses
`FeeCalculator`: `units` for time-based codes, `adjustments` for premiums and differentials,
and `base_fee` where the fee depends on more than the schedule's amount (the specialty, say).
"""

from ophis_emr_core.emr_core.billing_codes.types import (
	Adjustment,
	BillingContext,
	BillLineDraft,
	FeeCode,
	PriceResult,
)


class FeeCalculator:
	def price(self, fee: FeeCode, line: BillLineDraft, ctx: BillingContext) -> PriceResult:
		units = self.units(fee, line, ctx)
		base = self.base_fee(fee, line, ctx)
		if base is None:
			return PriceResult(
				None,
				units=units,
				message=f"{fee.code} has no set fee; it is paid on independent consideration",
			)
		adjustments = tuple(self.adjustments(fee, line, ctx, base * units))
		total = base * units + sum(adjustment.amount_cents for adjustment in adjustments)
		return PriceResult(total, base_cents=base, units=units, adjustments=adjustments)

	def units(self, fee: FeeCode, line: BillLineDraft, ctx: BillingContext) -> int:
		return max(int(line.units or 1), 1)

	def base_fee(self, fee: FeeCode, line: BillLineDraft, ctx: BillingContext) -> int | None:
		return fee.fee_cents

	def adjustments(
		self, fee: FeeCode, line: BillLineDraft, ctx: BillingContext, subtotal_cents: int
	) -> list[Adjustment]:
		return []
