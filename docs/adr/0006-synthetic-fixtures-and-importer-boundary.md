# Synthetic-only test fixtures and bank text handled only at the importer boundary

No file derived from a real bank file may enter the repository. Test fixtures
are produced by a script that generates synthetic statements from a structural
description of the bank format. The importer is the only code that sees raw
bank text; it stores the original description (with card numbers already
masked), a normalized description and a counterparty.

This protects the public repository from accidental leaks of personal data and
centralizes bank-specific quirks in one place. Rules match only normalized
fields and never depend on the end of a description, because banks truncate it.

## Consequences

- Fixture generation must be maintained alongside each importer.
- Normalization logic must be robust enough to support all rules.
- Personal-data CI guard remains as a second barrier.
