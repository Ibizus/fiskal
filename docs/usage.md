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

The launcher opens **Home** and the sidebar lists it first, followed by the
four working pages.

- **Home** — the number of pending movements, the date of the last import and
  links to the other pages. The full overview comes later with the reports.

The working pages:

- **Import** — create holders and accounts under **Accounts and holders**
  (an account needs a name and a three-letter currency; the last four digits
  of the account number and its holders are optional), then pick an account
  (with its last-four-digit hint), upload an Openbank statement, review the
  import summary, and revert a whole import. Adding a holder or an account
  confirms with a message, or shows the error. Messages after an action stay
  on screen until your next click.
- **Validate** — internal-transfer pair proposals come first and can be
  confirmed or rejected; both sides of an open proposal stay out of the queue
  until it is decided. Then work through the pending queue, shown 25 movements
  at a time. Each transaction shows the proposed type, category, cost center
  and optional holder. The single **Validate** button accepts the fields as
  shown, whether you changed them or not (hover the button for help). A
  category must be chosen when none was proposed. Tick **Create rule from this
  correction** to also create a ``contains`` rule whose pattern you can edit:
  it validates the other pending movements that match, and the page reports how
  many. The history-based suggestion is preselected.

  The top of the page shows how many movements are still pending and the
  percentage already classified. A **View** selector switches between the queue
  above and the guided **By concept** view, meant for classifying years of
  imported history in blocks (see below).
- **Transactions** — list transactions with filters for account, type, status
  and a text search over the description and counterparty. The status filter
  has a **Validated by rule** option to review what your rules validated
  automatically; there you can send a movement back to the Validate queue. To
  correct one, send it back and validate it again: it then stays manual. Only the 500 most
  recent matching movements are shown; the page says when that limit is hit.
- **Settings** — configure the application from the interface, in three sections
  picked at the top of the page; the selected section stays open after a save.
  *Cost centers*: create one (for example one per rental property), rename it,
  deactivate and reactivate it. *Categories*: create and edit a category with
  its kind (income or expense), fixed or variable, the income source for
  income categories (work, benefit, rental, other) and the default proration
  months; deactivate and reactivate it. Default items show a short description
  of what they are for. *Rules*: list every rule (yours first, then the default
  rules), edit one explicitly, deactivate and reactivate it. A rule change
  reclassifies only pending movements; validated movements never change.
  Nothing is ever deleted: an item used by movements or rules is deactivated,
  stays visible on the movements that already use it, and cannot be chosen for
  new ones. An item still assigned by an active rule cannot be deactivated
  until the rule is changed. Renaming a default item keeps its translation key
  and stores your name next to it; typing the original name again restores it.
- **Language** — the sidebar selector switches the interface between English
  and Spanish and remembers the choice in the profile database (never in the
  repository). Without a saved choice the interface is in English.

Until you create your own cost center, the Validate page shows a notice with a
link to Settings. There is no onboarding wizard.

The profile database is created on first run under the OS-specific data
location described in [profile-directory.md](profile-directory.md) (on Linux,
``~/.local/share/Fiskal/default/fiskal.db``). Set the ``FISKAL_DB_URL``
environment variable to a SQLAlchemy SQLite URL to use a different file. Every
start applies pending Alembic migrations to the profile, so it is always at the
latest schema revision. Run the UI from a repository checkout, because the
migrations live in the repository's ``migrations/`` directory.

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

A pair cannot be confirmed or undone when either side was validated by hand.
A movement a rule validated automatically can still be paired.

## Classifying a large history by concept

After importing years of history, switch the Validate page to **By concept
(guided)**. Pending movements are grouped by their normalized description, with
income and expenses in separate groups even when the description matches.
Descriptions that appear only once stay in the normal queue. Groups are
paginated (10 per page) and ordered from the most movements to the fewest, so
the biggest reduction of the queue comes first. The page draws one form per
group, never one per movement, so it stays responsive with thousands of
movements.

For each group choose the type, category, cost center and, optionally, the
person it is attributed to, then press **Classify the group**. The group says
beforehand how many movements will be affected, including the other pending
movements the rule would validate. Confirming:

- validates every pending movement of the group in one database transaction:
  if anything fails, nothing is written. The validated movements feed the
  history suggestions;
- by default also creates a rule of yours whose pattern is the normalized
  description (editable before confirming, as long as it stays part of the
  description). The group's movements are validated by that rule, like any
  movement one of your rules validates, so they follow the rule if you edit it
  later in Settings. The rule is limited to the same side of the books
  (expenses or income), validates other pending movements that contain the
  pattern and classifies future imports;
- with **Also create a rule for this description** unchecked, validates only
  the movements of the group, by hand: they become manual classifications and
  never change on their own;
- applies the chosen person to every movement. Without a rule it is a manual
  attribution that later reclassification never clears; with a rule the rule
  carries the person.

Amounts are not used to split a group. When the amounts in a group differ widely
(the largest is at least three times the smallest) the group shows a warning
that recommends not creating a rule: validate only those movements, or review
them one by one in the queue. The rule option stays checked; uncheck it if you
follow the recommendation.

Movements that are halves of a pending internal-transfer proposal, and paired
transfers, never appear in a group: confirm or reject the pair first.

### Undoing a group

Below the groups, **Recently validated groups** lists the latest groups you
validated this way. **Return group to pending** sends the group's movements
back to pending (they keep their classification as a preselection) and
deactivates the rule the group created, if any, which also returns to pending
the movements that rule had validated automatically. **Return only some
movements** returns selected movements of the group and leaves the rule alone.
A movement changed after the group was validated, for example by an explicit
reapply of rules over validated history, is left as it is and reported. This is
a safety net for a mistake in a large group, not a transaction editor.

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

Only rules you created validate movements: a movement that matches one is
classified and validated automatically (``validated by rule``), when importing
and when you create or edit such a rule for movements already pending. Seed
rules and history suggestions only preselect the category; those movements
stay ``pending`` until you validate them in the validation queue. Rule
changes reclassify pending movements and the movements a rule validated; a
movement you validated yourself never changes, and a bulk reapply over
validated history always shows a preview first. On a pending movement classified by a
rule, the holder belongs to the engine and is cleared when it is reclassified;
a manual edit switches the movement to manual, and manual movements keep their
holder.

### Neutral seed data

The repository ships neutral default categories, cost centers and a tiny set of
seed rules (all non-personal and merchant-independent). The seed includes a
variable *Rental expenses* expense category; profiles created earlier gain it
the next time the application starts, without any other change. The single income
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
from the account's holders. The validation queue shows the optional
"attributed to" field on every pending transaction; the report helpers are also
ready:

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

Only work (including benefits) and rental are income. Income is split into
labor income (work plus benefits), rental income and other inflows (gifts and
one-offs). Earned income is labor plus rental; other inflows are not income and
only help the cash balance, so they never count in earned income. All amounts
are integer minor units. Income without a category, or in a category without an
income source, counts as other inflows. Splitting rental income by property
share stays in the future tax module.

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
