# Local profile directory

Fiskal is local-first: your personal data, configuration, and SQLite databases
live on your own machine, not in the cloud or in the repository.

## What belongs in a profile directory

- Personal SQLite databases (`*.db`).
- Personal seed rules and category overrides.
- Bank import mappings and local settings.

## What does **not** belong in the repository

- Real account numbers, balances, or transaction details.
- Real bank files (`*.csv`, `*.xls`, `*.xlsx`, `*.ofx`, `*.qif`, `*.pdf`, images).
- Personal profile directories.

## Default location

The default profile directory is outside the repository. On Linux this is a
subdirectory of `~/.local/share/`. The exact path is configurable at runtime and
is never relative to the repository root.

## Example structure

Use synthetic placeholders only. A profile directory might look like this:

```text
~/.local/share/Fiskal/
├── default/
│   ├── fiskal.db
│   ├── rules.yaml
│   └── settings.yaml
```

Do not create a real profile directory in the repository. The repository ships
only a neutral default seed that contains no personal information.
