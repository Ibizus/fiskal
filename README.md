# Fiskal

A public, local-first personal finance app built with Python, SQLite, and
Streamlit.

> **Status:** phase 0 foundation. No importers, classification, or reports yet.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

## Installation

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Ibizus/fiskal.git
cd fiskal
uv sync --all-extras
```

## Usage

Run the test suite:

```bash
uv run pytest
```

Start the Streamlit UI:

```bash
uv run streamlit run src/fiskal/ui/launcher.py
```

Run migrations on a temporary database:

```bash
uv run alembic -c migrations/alembic.ini upgrade head
```

## Architecture overview

- `src/fiskal/domain/` – core entities and repository interfaces.
- `src/fiskal/db/` – SQLAlchemy base, engine helpers, and session utilities.
- `src/fiskal/importers/` – bank/file import adapters (future work).
- `src/fiskal/classification/` – category and cost-center rules (future work).
- `src/fiskal/reports/` – report builders (future work).
- `src/fiskal/fuel/` – optional fuel-tracking module.
- `src/fiskal/ui/` – Streamlit pages with no business logic.
- `src/fiskal/i18n/` – translation catalogs in English and Spanish.
- `migrations/` – Alembic database migrations.

All user-facing text uses translation keys; Spanish strings live only in
`src/fiskal/i18n/es.json`.

## Local profile directory

Personal configuration and data live outside the repository. See
[`docs/profile-directory.md`](docs/profile-directory.md).

## Contributing

See [`docs/contributing.md`](docs/contributing.md) and [`AGENTS.md`](AGENTS.md).

## Follow-up work

- Banking importers.
- Real data fixtures (only synthetic placeholders are allowed).
- Executable or installer packaging.
- Login and authentication.
- Fiscal (tax) report module.
- Photo/AI features.
