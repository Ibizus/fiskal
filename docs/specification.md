# Fiskal specification

Fiskal is a free, local-first personal finance application. It imports downloaded
bank statements, classifies transactions automatically, asks for manual validation
only for the rest, and turns the result into reports per cost center.

This document is generic by design: it contains no personal data, and every
personal choice (cost centers, rules, account identifiers) lives in a local
profile outside the repository.

## Goals

Fiskal answers three kinds of questions in its core:

1. **Where did the money go?** A classified list of income, expenses, savings
   and internal transfers, grouped by category and cost center.
2. **What is the real monthly cost?** Annual charges are spread over the months
   they cover, while the cash view keeps them on the date they were paid.
3. **Which charges keep repeating?** A recurring-charge detector surfaces
   subscriptions, utilities and other periodic payments.

Principles:

- Free, local and private. No bank credentials, no public hosting, no data
  leaving the machine.
- Maintainable for years with few moving parts.
- Scales by adding rows (new accounts, categories, rules), never columns or
  code.
- Generic: nothing in the code or the repository is specific to one household.
  A household is a profile.
- English in the repository; every user-facing string (categories, cost centers,
  UI) comes from a translation catalog. English and Spanish ship from day one.

## Core scope

The v1 core is limited to:

- importing downloaded bank statements (first supported format: Openbank),
- classifying transactions with deterministic rules and history,
- reviewing them in a validation queue,
- maintaining transactions, manual entries and recurring templates, and
- reporting an annual matrix by category and month plus a cost-center view with
  cash and real-cost selectors.

Optional modules described as future work are listed in the
[Roadmap](roadmap.md).

## Stack and architecture

Python, SQLite and Streamlit, all local, no external services in v1.

| Piece | Choice | Reason |
| --- | --- | --- |
| Language | Python 3.12+, `uv` for dependencies | Data ecosystem |
| Database | SQLite, one file per profile | No server; backup is a file copy |
| Data access | SQLAlchemy 2 + Alembic migrations | Evolvable schema without losing data |
| UI | Streamlit | Validation queue, editable tables and charts with little code |
| Tests | pytest with synthetic fixtures | Every importer and rule has a test |

Modules, as independent packages talking through interfaces:

- `importers/`: an `Importer` interface returning raw transactions. Adding a bank
  means adding one class.
- `classification/`: a `Classifier` cascade (rules, history, and later AI).
- `domain/`: model and logic (proration, recurring templates, recurring-charge
  detection).
- `reports/`: reusable aggregate queries shared by the UI and exports.
- `ui/`: Streamlit pages with no business logic, so the UI can be replaced or
  packaged without touching the rest.

The data directory is configurable per operating system and never relative to
this repository. See [profile-directory.md](profile-directory.md).

## Phase 0: repository, CI and agent rules

The repository and CI are the safety net that lets agent-authored PRs be
accepted without reviewing every line. The repository is public and licensed
under AGPL-3.0-or-later, so nothing personal may ever enter it.

- Protected `main`: no direct pushes, PR required, green CI, branch up to date.
  Merging is always a manual decision.
- `pyproject.toml` + `uv.lock`, pinned Python version, `AGENTS.md` with
  conventions, `pre-commit` running the same checks as CI.
- Strict `.gitignore` for databases, spreadsheets, CSV files and the data
  directory, except `tests/fixtures/anon/`.

CI on every PR: format and lint (ruff), strict type checks on the core
packages, tests with a coverage floor, migration check (applies on an empty
database; no model change without a migration), personal-data guard (fails on
data files outside the anonymized fixtures folder, or on any 13-19 digit number
or IBAN), secret scan (gitleaks), dependency audit (pip-audit), and a headless
Streamlit smoke test of every page.

Rules for agent PRs: one task per small PR with the template filled; new logic
ships with tests; protected paths (`.github/`, migrations, `AGENTS.md`, seed
rules, the guard script) require captain approval via `CODEOWNERS`; conventional
commits.

## Data model

Each transaction carries three independent labels: **type** (its effect),
**cost center** (who or what it belongs to) and **category** (what it is).
Fixed or variable derives from the category and is never labeled by hand.

Amounts are stored as integer minor units (for example cents) together with an
ISO currency code per transaction. There is no cross-currency conversion in v1:
one profile uses one currency.

| Type | Counts as expense/income | Example |
| --- | --- | --- |
| `expense` | Yes | Groceries, utilities |
| `income` | Yes | Salary, rent received |
| `savings_investment` | No, separate block | Brokerage transfer, savings pots |
| `internal_transfer` | No (neutral) | Moving money between own accounts |
| `refund` | Subtracts from the expense category | A friend pays back a shared dinner |

Main tables:

- **accounts**: id, display name, currency code, optional external identifier
  mask, active flag.
- **imports**: id, account id, file name, file hash, import date, rows read,
  new rows, duplicate rows, first transaction date, last transaction date, balance
  continuity flag.
- **transactions**: id, account id, import id, operation date, value
  date, original description (card number already masked), normalized
  description, counterparty, amount in minor units, balance in minor units,
  currency code, type, cost center, category, origin (rule / history / manual /
  template), rule id, status (pending / validated), optional proration period,
  note, manual flag.
- **cost_centers**: id, name key, optional custom name, group (free-form label),
  active flag, JSON attributes.
- **categories**: id, name key, kind (income or expense), fixed/variable flag,
  optional default proration months.
- **rules**: id, priority, condition on the normalized description
  ("contains" by default, optional regex advanced mode), optional amount range,
  optional day-of-month range, type, cost center, category, active flag, created
  from (seed / user), hit count, last hit.
- **recurring_templates**: entries generated on a configured day each month
  without passing through the bank.

Cost center groups, categories and rules are data, not code. The repository
ships a neutral default seed; a household's real configuration lives in its
local profile. All display text is a translation key.

## Importers

Every importer is idempotent: loading the same file, or overlapping periods,
duplicates nothing.

### Bank statement (first implementation: Openbank)

The Openbank export is an HTML table saved with an `.xls` extension, encoded in
`iso-8859-1`, with ten physical columns of which five are always-empty spacer
columns. The logical columns are operation date, value date, description,
amount and balance. Ten header rows precede the column titles; row indexes are
detected by content, not hard-coded.

- Parsing: sniff the container by magic bytes, then use `pandas.read_html`;
  `xlrd` and `read_excel` cannot open this format.
- Dates: `dd/mm/yyyy` as text.
- Amounts: text with comma decimals, dot thousands, leading minus for expenses,
  no currency symbol.
- Ordering: newest first.
- Descriptions: space-padded to 100 characters and truncated at 100 characters.
  Rows that reach the truncation limit are flagged.
- Unique key: for Openbank, a hash of (operation date, value date, description,
  amount, balance) plus an occurrence counter for identical rows inside one
  file. Each importer declares how it identifies a transaction; when the bank
  provides its own identifier, that identifier is used.
- Continuity: sort chronologically per account and verify
  `previous balance + amount = balance`; show a warning for gaps or
  inconsistent overlaps.
- Masking: replace any 13-19 digit card number with its last four digits before
  data leaves the importer. Full IBANs and account numbers are never stored.
- Normalization: recognize the operation kind by prefix and extract the
  counterparty (card purchase, direct debit, transfer, instant payment, cash
  withdrawal, loan settlement, social security). Rules match only normalized
  fields, never raw text, and never depend on the end of a description because
  banks truncate it.
- Duplicate handling: a transaction that reappears in an overlapping import with a
  slightly different description text is treated as a duplicate when date,
  amount and balance match. The import summary reports the text difference;
  nothing is created or modified.

### Import summary

After reading any file the importer reports:

- first and last transaction date,
- total line count,
- how many rows are new and how many were already loaded, and
- a balance-continuity warning when the check fails.

An alert is raised when the file adds no new transaction, which catches wrong
uploads and downloads with unexpected dates.

### Import revert

An import can be reverted as a whole while none of its transactions has been
validated and its month is not closed. Otherwise only single transactions can be
deleted, with a confirmation. Data imported from the bank (dates, amount,
description, balance) is never edited; only classification and note are
editable. Manual entries are fully editable.

## Classification pipeline

A cascade where each layer only receives what the previous one did not resolve,
and every manual correction can become a rule. Target: after the history is
loaded, under 5% of a month's transactions reach the manual queue.

1. Normalization (in the importer).
2. Deterministic rules by priority: first match assigns type, cost center and
   category. Conditions: "contains" on the normalized description/counterparty,
   optional amount range, optional day-of-month range. Regular expressions are
   an optional advanced mode.
3. History: if the same normalized counterparty was always validated one way,
   propose it; if it had several, queue it with the most frequent one
   preselected.
4. AI (optional module, v2): a `Classifier` implementation that suggests with a
   confidence level and never validates by itself. It sends only the normalized
   concept, amount and date.
5. Validation queue: confirm or correct; on correction the app offers "create
   rule" with an editable proposed pattern.

Cross-cutting business rules:

- **Rule precedence**: rules created from the validation queue always beat seed
  rules. Within each group, an editable numeric priority decides; first match
  wins. The rule editor warns when a new rule collides with another.
- **Correcting a classified transaction**: correcting a transaction that a rule
  classified offers a new, more specific rule above it. A rule is never
  modified silently. Rule changes reclassify only pending transactions;
  validated transactions never change on their own. An explicit bulk action
  with a preview can reapply rules to validated history.
- **Refunds**: an incoming transfer from a person with no rule of its own is a
  refund that nets against its category in the month it arrives. It is not
  linked to the original expense.
- **Proration**: a category can define a default number of proration months;
  the period can also be edited per transaction. A prorated transaction is spread
  evenly across months in the real-cost view and shown whole on its date in the
  cash view.
- **Recurring templates**: generated on the configured day each month, for
  example a meal-voucher income and its matching expense.
- **Recurring-charge detector**: groups by normalized counterparty and detects
  periodic charges with a stable amount. Defaults: at least 3 occurrences at a
  monthly or quarterly interval with amount within 10%, or at least 2 if annual.
  It only proposes; confirmation or dismissal is stored. Thresholds are
  configurable. A new recurrence or an amount increase raises a notice.

## Accounts

Account is a first-class entity from day one: every import and transaction
belongs to an account, and balance continuity is checked per account. The first
UI handles one account. Automatic pairing of both sides of an internal transfer
is deferred; until then a rule classifies it as an internal transfer.

## Screens

Six Streamlit pages in the core:

1. **Import**: upload files; summary of rows read, new, duplicated, date range,
   balance-continuity warning and queue size.
2. **Validate**: pending queue with preselected suggestion; inline editing of
   type, cost center, category, proration period and note; confirm,
   confirm selected, create rule.
3. **Transactions**: filterable table with editing and manual entries.
4. **Overview**: current month against the 12-month average; fixed and variable
   spending; savings; active notices; newly detected recurrences.
5. **Reports**: annual matrix by category and month, cost-center view, cash
   versus real-cost selector.
6. **Settings**: rules, cost centers, categories, recurring templates,
   accounts, language.

## Reports

All reports are queries over `transactions`; none stores its own totals.

- Yearly summary (category by month plus total, with savings and investments in
  a separate block), in cash and real-cost views.
- Cost-center view: income, expenses and balance for a cost center, with cash
  and real-cost selectors.
- Recurring charges with last increase.

Deviation alerts and manual budget caps are deferred to a later version; they
are a query layer on top and require no model change.

## Closed month

A month can be closed by hand once the review is done. In a closed month
nothing can be imported or reclassified until the month is explicitly reopened,
and the reopening is recorded.

## Bulk history import

Importing years of history is a guided mode: transactions are grouped by
normalized description and validated in blocks, so one correction classifies
every occurrence and feeds the rule dictionary. Bulk import must handle
thousands of rows.

## Privacy and security

Data never leaves the user's machine in v1.

- No bank credentials and no bank connection: only files downloaded by hand.
- Card numbers masked before saving; full IBANs and account numbers are never
  stored.
- The database, statements and real fixtures stay out of the repository. Tests
  use synthetic data only.
- Backup: manual action that copies the database file to a folder the user
  picks. Application-level encryption is deferred.
- The profile (real cost centers, seed rules, account identifiers) is a local
  directory outside the repository.

## Core acceptance criteria

- [ ] A real Openbank file imports correctly, with correct dates, amounts and
      balances.
- [ ] Re-importing the same or an overlapping file creates zero duplicates; a
      differing description only warns.
- [ ] The import summary reports first and last date, total lines, new and
      duplicate counts, and raises an alert when no transaction is new.
- [ ] The continuity check detects a deliberate gap in a fixture.
- [ ] No full card number is ever stored in the database, logs or error
      messages.
- [ ] Creating a rule from the validation queue classifies the same concept
      automatically on the next import, and user rules beat seed rules.
- [ ] A closed month rejects import and reclassification until explicitly
      reopened.
- [ ] An annual insurance charge with a 12-month period appears whole in the
      cash view and spread in the real-cost view.
- [ ] The annual summary reproduces a closed year from the user's previous
      spreadsheet, except differences explained by the new model.
- [ ] After validating the history, a new month leaves under 5% of its
      transactions in the queue.
- [ ] The recurring-charge detector flags a planted recurrence.
- [ ] Automated tests cover the importer, proration, rule precedence and
      normalization.
- [ ] Documentation lets a new user install, load a statement and finish a
      monthly review without help.
