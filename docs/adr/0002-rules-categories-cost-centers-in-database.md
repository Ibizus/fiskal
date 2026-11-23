# Rules, categories and cost centers live in the database

Rules, categories and cost centers are stored in the database as the single
source of truth. The YAML file in the profile directory is only an initial seed
and an export format.

This lets users edit them through the UI without restarting the application and
keeps runtime behavior independent of file parsing. The repository ships a
neutral default seed; personal seeds stay in the local profile outside the
repository.

## Consequences

- Seed data must be idempotently loaded on first run.
- Tests can build fixtures directly in the database instead of relying on YAML
  files.
- Personal configuration never needs to be committed.
