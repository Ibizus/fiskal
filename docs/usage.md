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

## Pre-commit

Install the pre-commit hooks to run the same checks as CI before each commit:

```bash
uv run pre-commit install
```
