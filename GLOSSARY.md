# Fiskal glossary

Definitions for the product domain used across Fiskal documentation and code.

## Core entities

**Transaction**:
A single movement of money recorded in Fiskal.
_Avoid_: Movement (used internally for some database tables), operation.

**Account**:
A bank or cash account where money moves. Every import and transaction belongs
to an account. The account is independent of the cost center, which is who or
what the money belongs to. An account may have an optional default cost center
for transactions that no rule classifies.
_Avoid_: Wallet, pocket, book.

**Import**:
One act of reading a downloaded bank file and creating or matching transactions.
_Avoid_: Upload, load.

**Importer**:
The adapter that parses a specific bank or file format into Fiskal domain objects.
_Avoid_: Parser, reader.

**Account hint**:
The last four digits of an account code, stored to help recognize the account on
later imports. Full account numbers and IBANs are never stored.
_Avoid_: Account number, IBAN.

**Holder (person)**:
A lightweight entity for a person who owns accounts. An account has one or more
holders. A transaction can optionally be attributed to a holder; income reports
group by that attribution, and a transaction with no attribution is not
personal. Holders are never seeded; each profile creates its own.
_Avoid_: User, member.

## Classification

**Cost center**:
The answer to "who or what does this belong to?", such as a property, vehicle,
person or group.
_Avoid_: Budget, envelope, project.

**Category**:
The answer to "what is this?", such as groceries, utilities or salary.
_Avoid_: Tag, label.

**Income source**:
An optional attribute on an income category: work, benefit, rental or other.
Work and benefit together are labor income; labor plus rental is earned income.
Other marks gifts, refunds, deposit returns and uncategorized inflows, which
are not income: they count in the cash balance but never in earned income.

**Transaction type**:
The effect of a transaction on the books. One of five kinds:

- **expense**: money spent, counts as an expense.
- **income**: money received, counts as an income.
- **savings_investment**: money moved to savings or investments, shown outside
  income and expenses.
- **internal_transfer**: money moved between the user's own accounts, neutral
  to totals.
- **refund**: money received back that subtracts from its category's expenses.

_Avoid_: Direction, sign, class.

**Rule**:
A configurable condition that assigns type, cost center, category and
optionally a holder to a transaction automatically.
_Avoid_: Filter, automation.

**Rule precedence**:
The order in which rules are tried: user rules always beat seed rules; inside
each group the lower numeric priority wins, and the first match wins.

**Rule collision**:
Two rules that can match the same transaction. The rule editor warns when a new
rule collides with an existing one.

**Seed**:
The neutral, non-personal default categories, cost centers and rules shipped
with the application. Seeds are only initial data; the live source of truth is
in the database.
_Avoid_: Default, template.

**Default cost center**:
An optional cost center assigned to an account, applied as the last
classification layer before the validation queue: it only fills the cost center
when no rule, history or AI suggestion has set one, and the transaction is still
validated with it preselected.
_Avoid_: Fallback category.

**Internal transfer pairing**:
The core step that proposes the two transactions, one in each of the user's own
accounts, that make up an internal transfer. Pairing marks both as
`internal_transfer` so merged views do not double count them; the user confirms.
_Avoid_: Transfer matching, reconciliation.

**Rejected pair**:
A pair of transactions the user explicitly chose not to link as an internal
transfer. The decision is persisted so the same proposal is not surfaced again.
_Avoid_: Dismissed match, ignored pair.

## Workflow

**Profile**:
A local directory outside the repository that holds one household's database,
settings and personal seed rules.
_Avoid_: Tenant, workspace.

**Validation queue**:
The list of transactions the classifier could not resolve automatically, ready
for manual review.
_Avoid_: Inbox, review pile.

**Pending**:
A transaction that has been imported but not yet confirmed by the user.
_Avoid_: Unconfirmed, draft.

**Validated**:
A transaction the user has confirmed or corrected, or that a rule the user
created validated automatically (_validated by rule_).
_Avoid_: Approved, committed.

**Guided action**:
One confirmation of a group of pending transactions that share a normalized
description, validated together in the by-concept view of the Validate page.
It records the rule it created, if any, so the whole block can be undone.
_Avoid_: Batch, bulk edit.

**Closed month**:
A month that has been explicitly locked after review. Nothing can be imported
or reclassified in it until it is reopened.
_Avoid_: Locked period, finalized.

## Reporting

**Proration**:
Spreading a single transaction's amount evenly across several months, starting
from its date.
_Avoid_: Amortization, split.

**Cash view**:
A report that shows transactions on the dates they happened.
_Avoid_: Actual view, paid view.

**Real-cost view**:
A report that shows prorated transactions spread across the months they cover.
_Avoid_: Accrual view, smoothed view.

**Recurring charge**:
A periodic payment the detector identifies by its repeating counterparty,
interval and amount.
_Avoid_: Subscription, periodic expense.

**Earned income**:
Work plus benefits plus rental income. Only work, benefits and rental are
income.
_Avoid_: Net income, take-home pay.

**Labor income**:
Work plus benefits income.
_Avoid_: Salary.

**Rental income**:
Income from renting out property, reported separately from labor income.

**Other inflows**:
Gifts, refunds, deposit returns and uncategorized inflows. They are not income:
they only help the cash balance and never count in earned income.

**Attributed / unattributed**:
A transaction attributed to a holder is personal; one with no attribution is
not personal (for example shared rental income or gifts).

**Recurring template**:
A manually configured pattern that generates a transaction on the same day each
month without passing through the bank.
_Avoid_: Scheduled transaction, repeat.

## Normalization

**Counterparty**:
The other party extracted from a bank description, such as a merchant or payer.
_Avoid_: Payee, vendor.

**Normalized description**:
The cleaned, rule-ready text derived from the raw bank description. Rules match
only normalized fields.
_Avoid_: Cleaned text, mapped description.

## Scope

**Core**:
The mandatory product scope: import, classification, validation queue,
transactions and reports.
_Avoid_: Base, kernel.

**Optional module**:
A feature area that is installed or activated after the core, such as loan
schedules or rental and tax reporting.
_Avoid_: Plugin, extension.
