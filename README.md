# Fiskal

A public, local-first personal finance app built with Python, SQLite, and
Streamlit.

> **Status:** phase 0 foundation. Design documents and roadmap are in place;
> implementation of the core is in progress.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

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

## Documentation

- [`docs/specification.md`](docs/specification.md) – product specification.
- [`docs/roadmap.md`](docs/roadmap.md) – scope and ordered task list.
- [`GLOSSARY.md`](GLOSSARY.md) – domain terminology.
- [`docs/adr/`](docs/adr/) – architecture decision records.

## Architecture overview

- `src/fiskal/domain/` – core entities and repository interfaces.
- `src/fiskal/db/` – SQLAlchemy base, engine helpers, and session utilities.
- `src/fiskal/importers/` – bank/file import adapters.
- `src/fiskal/classification/` – category and cost-center rules.
- `src/fiskal/reports/` – report builders.
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

Planned work and scope are tracked in [`docs/roadmap.md`](docs/roadmap.md).
