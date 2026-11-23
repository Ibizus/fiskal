# Money as integer minor units plus currency code

Money is stored as an integer in minor units (for example cents) together with
an ISO currency code on each transaction. This replaces an earlier placeholder
model that used a fixed-precision decimal column.

The choice avoids floating-point rounding errors in reports and keeps the
schema simple. Calculations such as proration use integer arithmetic with a
deterministic distribution of the remainder, so the parts always add up
exactly to the total; for example, 10.00 split over 12 months gives 83 cents
each plus the 4 leftover cents assigned one by one to the first months.
Percentages and rates are represented as `Decimal` values, converted back to
integer minor units with an explicit rounding rule, and floating point is
never used for money. Cross-currency conversion is left out of v1 because one
profile uses one currency; adding conversion later is a query-layer change that
does not require altering stored amounts.

## Consequences

- Every amount field needs a paired currency code.
- UI and reports format the integer into display units at display time.
- Calculations must keep results in integer minor units using explicit
  rounding, and proration must distribute remainders deterministically.
- A migration is required to replace the existing decimal placeholder.
