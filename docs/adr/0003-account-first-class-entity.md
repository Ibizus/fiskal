# Account as a first-class entity from day one

Account is a first-class entity: every import and transaction belongs to an
account, and balance continuity is checked per account. The default view of
Overview, Reports, Transactions and Validate merges all accounts of the profile,
with a filter to show one or several accounts.

This avoids a later data migration to attach accounts to historical
transactions and makes multi-account support a UI and reporting change rather
than a schema change. Automatic pairing of both sides of an internal transfer
between the user's own accounts is part of the core, scheduled right after the
importer, so merged views do not double count transfers.

## Consequences

- Every import and transaction belongs to an account.
- The account is where the money moves; the cost center is who or what the
  money belongs to. The two are independent concepts.
- An account may have an optional default cost center, applied as the last
  classification layer before the validation queue: it only fills the cost
  center when no rule, history or AI suggestion has set one, and the
  transaction is still validated with it preselected.
- Overview, Reports, Transactions and Validate show all accounts merged by
  default, with a filter to show one or several accounts.
- Every import names its account. The importer proposes the account from the
  account code in the statement header, storing only the last four digits as a
  hint and never the full number; the user confirms.
- Balance continuity is checked per account, and a transaction's identity
  includes its account.
- Automatic pairing of both sides of an internal transfer between the user's
  own accounts runs in the core right after the importer, so merged views do
  not double count transfers.
