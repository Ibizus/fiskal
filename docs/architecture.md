# Architecture overview

Fiskal is organized into packages with clear responsibilities.

## Package responsibilities

| Package | Responsibility |
|---------|----------------|
| `domain` | Core entities, value objects, and repository interfaces. |
| `db` | SQLAlchemy declarative base, engine helpers, and session utilities. |
| `importers` | Adapters that read downloaded bank/files and map them to domain objects. |
| `classification` | Rules that assign categories and cost centers to transactions. |
| `reports` | Builders that aggregate domain data into report structures. |
| `fuel` | Optional fuel-tracking module with its own tables. |
| `ui` | Streamlit pages and launch helpers. Pages contain no business logic. |
| `i18n` | Translation catalogs and lookup helpers. |

## Data flow

1. Importers parse external files into `Transaction` and related domain objects.
2. Classification rules enrich transactions with categories and cost centers.
3. Reports aggregate classified transactions.
4. The UI renders reports and lets the user review and adjust data.

## Local-first design

User data is stored in SQLite databases outside the repository. The exact
location is configurable per profile. See [profile-directory.md](profile-directory.md).
