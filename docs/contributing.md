# Contributing

Thank you for helping improve Fiskal.

## Repository rules

- The repository is public and AGPL-3.0-or-later licensed.
- **Never commit personal or financial data.** This includes real account
  numbers, balances, transaction descriptions, bank files, loan details, or
  photos of documents.
- **Never commit personal seed configuration.** Use the local profile directory
  for personal rules and categories.
- **Everything in the repo is written in English.** Code, comments, docs,
  commit messages, and PR text must be in English.
- User-facing text uses translation keys; Spanish translations live in
  `src/fiskal/i18n/es.json`.

## Conventional commits

Use conventional commits:

- `feat:` – new feature
- `fix:` – bug fix
- `docs:` – documentation only
- `style:` – formatting, no logic change
- `refactor:` – code change that neither fixes nor adds a feature
- `test:` – adding or correcting tests
- `chore:` – tooling, dependencies, CI

## Before submitting a PR

1. Run the test suite:

   ```bash
   uv run pytest
   ```

2. Run linting and type checks:

   ```bash
   uv run ruff format --check
   uv run ruff check
   uv run mypy --strict src/fiskal/domain src/fiskal/classification src/fiskal/importers
   ```

3. Run migrations:

   ```bash
   uv run alembic -c migrations/alembic.ini upgrade head
   uv run alembic -c migrations/alembic.ini check
   ```

4. Run the personal-data guard:

   ```bash
   uv run python scripts/guard_personal_data.py
   ```

## Captain approval required

The following areas require captain approval before changing:

- `.github/`
- `migrations/`
- `AGENTS.md`
- Seed rules and default seed data
- `scripts/guard_personal_data.py`

See [`AGENTS.md`](../AGENTS.md) for more details.
