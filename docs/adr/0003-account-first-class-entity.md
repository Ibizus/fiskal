# Account as a first-class entity from day one

Account is a first-class entity: every import and transaction belongs to an
account, and balance continuity is checked per account. The first UI handles
one account.

This avoids a later data migration to attach accounts to historical
transactions and makes multi-account support a UI and reporting change rather
than a schema change. Automatic pairing of both sides of an internal transfer
is deferred; until then a rule classifies the transfer as internal.

## Consequences

- Imports must be associated with an account at upload time.
- Reports aggregate per account by default.
- Multi-account users will need a way to switch accounts, which can be added
  later without touching the data model.
