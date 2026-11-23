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
run safely in CI without a configured profile directory.

## Lint and format

```bash
uv run ruff format --check
uv run ruff check
uv run mypy --strict src/fiskal/domain src/fiskal/classification src/fiskal/importers
```

## Pre-commit

Install the pre-commit hooks to run the same checks as CI before each commit:

```bash
uv run pre-commit install
```
