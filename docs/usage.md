# Usage

## Run the test suite

```bash
uv run pytest
```

## Start the Streamlit UI

```bash
uv run streamlit run src/fiskal/ui/launcher.py
```

Then open the URL printed by Streamlit in your browser.

## Run database migrations

```bash
uv run alembic -c migrations/alembic.ini upgrade head
```

This repository uses a temporary database by default so that migrations can be
run safely in CI without a configured profile directory. Set the
`FISKAL_MIGRATION_URL` environment variable to a SQLAlchemy SQLite URL to
migrate a different database.

## Lint and format

```bash
uv run ruff format --check
uv run ruff check
uv run mypy --strict src/fiskal/domain src/fiskal/classification src/fiskal/importers
```

## Importing an Openbank statement

Fiskal's first supported importer reads the movement export from the Openbank
web portal.

1. In the portal, open the account's movements and choose the export option.
   The download is saved with an `.xls` extension but is actually an HTML
   table encoded in `iso-8859-1`; that is the expected format, so do not
   convert it.
2. The portal export is capped at 1000 movements per file. For accounts with
   more activity, export shorter date ranges so no movements are missed.

After an import Fiskal reports a summary with:

- **First and last movement date** — the earliest and latest movement date in
  the file.
- **Rows read** — how many movement rows the file contained.
- **New / already loaded** — how many movements were added and how many were
  skipped because they were already present.
- **Truncated descriptions** — how many descriptions reached the 100-character
  limit and may have been cut short by the bank.
- **Proposed internal-transfer pairs** — how many newly imported movements have
  a plausible counterpart in another account of the profile.
- **Classified by rule / by history / by default** — how many of the new
  movements received their category from a rule, from validated history, and
  how many received only their cost center from the account default.
- **Still unclassified** — how many of the new movements still have no
  category and therefore wait in the validation queue.
- **Warnings** — balance-continuity notices (a gap in history or an incoherent
  running balance) and movements skipped because their description differed
  slightly from an already-loaded movement; for each of those the summary shows
  the stored and the incoming description.
- An **alert** when the file added no new movement at all.

Re-importing the same or an overlapping file is safe: nothing is ever
duplicated.

## Internal transfer pairing

When you move money between your own accounts, both sides appear as separate
movements, one in each account. Right after an import Fiskal proposes the
matching counterpart in a different account: the opposite amount, the same
currency and a value date within a few days. Several plausible candidates are
shown ranked by date distance; Fiskal never picks one silently.

Confirming a proposal links both movements and marks them as
``internal_transfer`` so they are not counted twice in merged reports. Rejecting
a proposal remembers your choice and stops the same pair from being proposed
again. You can undo a confirmed pair later; both sides return to their default
type and pending status. Pairing never changes imported bank fields.

A pair cannot be confirmed or undone when either side is already validated.

## Classification

Right after an import, and after internal-transfer proposals, Fiskal runs a
classification cascade over the new, pending movements:

1. **Rules** — an active rule whose condition matches assigns the type, cost
   center, category and optionally a holder (person). Rules are data in the
   database, never code. A rule has a numeric priority, a case-insensitive
   ``contains`` condition on the normalized description and counterparty (or an
   optional regex advanced mode), an optional amount range and an optional
   day-of-month range. User rules always beat seed rules; inside each group the
   lower priority wins first, ties break on the rule id. The import summary
   reports how many movements were classified by rule.
2. **History** — when every validated transaction with the same normalized
   counterparty was classified the same way, that classification is applied.
   When history disagrees, the movement stays unclassified; nothing is stored,
   and the validation queue recomputes the most frequent outcome (with the
   alternatives) through ``suggest_from_history`` to preselect it.
3. **Account default cost center** — the last layer fills only the cost center
   when no earlier layer set one, and never sets type or category.

Rules and categories are never validated by the engine: every movement remains
``pending`` until the user confirms or corrects it in the validation queue.
Rule changes reclassify only pending movements; a bulk reapply over validated
history always shows a preview first.

### Neutral seed data

The repository ships neutral default categories, cost centers and a tiny set of
seed rules (all non-personal and merchant-independent). The single income
category is split into work, benefit, rental and other income categories, each
with its income source, so the neutral seed already distinguishes labor income,
rental income and other inflows. Categories and cost centers are installed idempotently by key and never
overwrite user-customised names. Rule seeding is a first-run action: seed rules
are installed only while the profile has no rules at all, so a deleted or
edited seed rule never comes back on a later run. Restoring the default rules
would be a separate, explicit feature:

```python
from fiskal.classification import seed_neutral_data

seed_neutral_data(session)
```

## Holders and income

A holder is a lightweight person entity; an account has one or more holders.
A transaction can optionally be attributed to a holder, with no default taken
from the account's holders. This data layer ships without a user interface for
now (the screens belong to a later slice), but the report helpers are ready:

```python
from fiskal.reports.income import (
    earned_income_minor,
    income_by_holder,
    labor_income_minor,
    other_inflows_minor,
    rental_income_minor,
    total_income,
    unattributed_income,
)
```

Income is split into labor income (work plus benefits), rental income and other
inflows (gifts and one-offs). Earned income is labor plus rental; other inflows
count in the cash balance but not in earned income. All amounts are integer
minor units. Income without a category, or in a category without an income
source, counts as other inflows. Splitting rental income by property share
stays in the future tax module.

An existing profile is not migrated: seeding adds the new work, benefit, rental
and other income categories next to the old single income category, which
keeps its name, its rules and its transactions. That old category has no income
source, so its income keeps counting as other inflows until you move it or set
its source yourself.

## Pre-commit

Install the pre-commit hooks to run the same checks as CI before each commit:

```bash
uv run pre-commit install
```
