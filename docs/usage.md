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
- **Warnings** — balance-continuity notices (a gap in history or an incoherent
  running balance) and movements skipped because their description differed
  slightly from an already-loaded movement.
- An **alert** when the file added no new movement at all.

Re-importing the same or an overlapping file is safe: nothing is ever
duplicated.

## Pre-commit

Install the pre-commit hooks to run the same checks as CI before each commit:

```bash
uv run pre-commit install
```
