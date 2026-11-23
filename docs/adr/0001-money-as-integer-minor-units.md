# Money as integer minor units plus currency code

Money is stored as an integer in minor units (for example cents) together with
an ISO currency code on each transaction. This replaces an earlier placeholder
model that used a fixed-precision decimal column.

The choice avoids floating-point rounding errors in reports and keeps the
schema simple. Cross-currency conversion is left out of v1 because one profile
uses one currency; adding conversion later is a query-layer change that does
not require altering stored amounts.

## Consequences

- Every amount field needs a paired currency code.
- UI and reports format the integer into display units at display time.
- A migration is required to replace the existing decimal placeholder.
