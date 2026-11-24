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

## Tool versions

`ruff` is installed from `uv.lock`, and the pre-commit hooks run the same
`uv run ruff ...` commands as CI, so local checks and CI always use the same
version. To upgrade it, widen or bump the range in `pyproject.toml`, run
`uv lock --upgrade-package ruff`, and include the resulting reformat in the same
pull request.

## Test fixtures

All test fixtures are synthetic. No file derived from a real bank statement may
ever enter the repository.

Generate a new Openbank-style fixture by hand with:

```bash
uv run python tests/support/openbank_synthetic.py \
  tests/fixtures/anon/openbank_synthetic.xls \
  --movements 100 \
  --start-date 2024-01-01 \
  --account-code 12345678901234567890 \
  --opening-balance 50000.00 \
  --seed 42
```

The generator is deterministic: the same options and seed produce byte-identical
output. Use the switches (`--truncate-descriptions`, `--repeated-rows`,
`--accented-descriptions`, `--thousands-in-amounts`, `--extra-trailing-blank`,
`--movements 0`, etc.) to create edge cases for importer tests.

Generated `.xls` files are HTML tables encoded in `iso-8859-1`, exactly like the
Openbank portal export. All invented text is in English except the five
Spanish column titles (`Fecha Operación`, `Fecha Valor`, `Concepto`,
`Importe`, `Saldo`), which are part of the real bank format and are the only
labels the importer should locate by text. Keep any generated fixture under
`tests/fixtures/anon/` so the personal-data guard accepts it.

## Captain approval required

The following areas require captain approval before changing:

- `.github/`
- `migrations/`
- `AGENTS.md`
- Seed rules and default seed data
- `scripts/guard_personal_data.py`

See [`AGENTS.md`](../AGENTS.md) for more details.
