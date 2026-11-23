# Fiskal agent guidance

This file is project-intrinsic knowledge for every coding session. It applies
to all contributors, including agent workers. Treat it as a hard contract;
escalate to the captain before changing it.

## Project overview

Fiskal is a public, local-first personal finance application written in Python,
using SQLite for persistence and Streamlit for the UI. The repository is
licensed under AGPL-3.0-or-later. **Nothing personal or private may ever be
committed.**

## Module layout

- `src/fiskal/domain/` – core entities, value objects, and repository
  interfaces. Must stay free of UI, import, and framework code.
- `src/fiskal/db/` – SQLAlchemy declarative base, engine helpers, and session
  utilities.
- `src/fiskal/importers/` – bank/file import adapters. Only parsing and mapping
  to domain objects; no business logic.
- `src/fiskal/classification/` – rules that assign categories and cost centers.
  Only configurable, non-personal default rules belong here.
- `src/fiskal/reports/` – report builders that aggregate domain data.
- `src/fiskal/fuel/` – optional fuel-tracking module with its own tables.
- `src/fiskal/ui/` – Streamlit pages and launch helpers. Pages hold **no**
  business logic.
- `src/fiskal/i18n/` – translation catalogs. Keys are neutral English; display
  text lives in `en.json` and `es.json`. No Spanish text in code.
- `migrations/` – Alembic migrations. Every schema change needs a migration.
- `tests/` – pytest suite.
- `scripts/` – tooling scripts, including the personal-data guard.

## How to run tests and checks

```bash
# Sync dependencies
uv sync

# Run tests with coverage
uv run pytest

# Lint and format
uv run ruff format --check
uv run ruff check

# Strict mypy on core packages
uv run mypy --strict src/fiskal/domain src/fiskal/classification src/fiskal/importers

# Run migrations on a temporary database
uv run alembic -c migrations/alembic.ini upgrade head

# Pre-commit (runs the same checks as CI)
uv run pre-commit run --all-files
```

## Captain-controlled files

The following files and directories require captain approval before changing:

- `.github/`
- `migrations/`
- `AGENTS.md`
- Seed rules and seed data (`src/fiskal/classification/seed*`,
  `src/fiskal/i18n/*.json` default keys)
- `scripts/guard_personal_data.py`

## Hard safety rules

1. **No personal or financial data in the repository.** This includes real
   account numbers, balances, transaction descriptions, bank files, loan
   details, or photographs of documents.
2. **No personal seed configuration.** Default seeds must be neutral and
   synthetic. Personal rules belong in a local profile directory outside the
   repository.
3. **No real bank files.** Do not commit CSV, XLS, XLSX, OFX, QIF, PDF, or
   image exports from real banks.
4. **Data directory is configurable and OS-specific.** It must never be a path
   relative to the repository root. Use the profile directory concept described
   in `docs/profile-directory.md`.
5. **Ask the captain when in doubt.** If you are unsure whether something counts
   as personal data, stop and ask.

## Personal data guard

CI runs `scripts/guard_personal_data.py`. It fails if:

- A data file (`*.db`, `*.csv`, `*.xls`, `*.xlsx`, and other personal formats)
  appears outside `tests/fixtures/anon/`.
- Any committed file contains a 13-19 digit number or an IBAN.

Keep the guard green. If a legitimate fixture needs an exception, discuss it
with the captain.

## i18n rules

- All user-facing text (category names, cost-center labels, UI strings) uses
  translation keys.
- Keys are written in neutral English.
- Spanish translations live only in `src/fiskal/i18n/es.json`.
- Add keys to every supported catalog and update tests.

## Conventional commits

Use conventional commits for all changes:

- `feat:` – new feature
- `fix:` – bug fix
- `docs:` – documentation only
- `style:` – formatting, no logic change
- `refactor:` – code change that neither fixes nor adds a feature
- `test:` – adding or correcting tests
- `chore:` – tooling, dependencies, CI

## Workflow

1. Work in a feature branch, never the default branch.
2. Keep changes small and focused.
3. Ensure CI passes locally before pushing.
4. The captain reviews and merges every PR; there is no automatic merge.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
