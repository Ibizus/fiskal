# Conventional commits

This repository uses conventional commits to keep the changelog readable and
to support automated tooling.

## Format

```text
<type>(<scope>): <short summary>
```

## Types

| Type | Use for |
|------|---------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation changes only |
| `style` | Formatting, missing semi-colons, etc.; no code change |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or correcting tests |
| `chore` | Tooling, dependencies, CI configuration |

## Examples

```text
feat(i18n): add utility cost-center translation keys
fix(db): use correct column type for transaction amount
docs(readme): update installation instructions
test(classification): add seed category parity check
chore(ci): enable pip-audit in GitHub Actions
```

## Scope

Scope is optional but encouraged. Use the package or area being changed, such
as `domain`, `db`, `importers`, `classification`, `reports`, `fuel`, `ui`,
`i18n`, `ci`, or `docs`.
