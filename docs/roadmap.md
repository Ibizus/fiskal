# Roadmap

This roadmap reflects the settled product design for Fiskal. The core comes
first; optional modules follow. Items are listed in rough execution order.

## Core scope (v1)

The v1 core is: import bank statements, classify them, validate the queue,
keep transactions, and report by category and cost center.

1. Design docs, fuel removal, CI fixes.
2. Core data model (accounts, imports, transactions, categories, cost centers,
   rules, recurring templates), with money as integer minor units plus currency
   code.
3. Synthetic statement generator: produce test fixtures from a structural
   description of the bank format, never from a real bank file.
4. Openbank importer: HTML-table `.xls` parser, idempotency, balance
   continuity, card-number masking, normalization, duplicate detection.
5. Internal transfer pairing: propose and confirm counterpart transactions
   between the user's own accounts, so merged views do not double count
   transfers.
6. Classification engine and neutral seed rules.
7. Validation queue: confirm or correct the proposed category and cost
   center, create a rule from a correction, show the history-based suggestion,
   confirm or reject internal-transfer pair proposals, and attribute a holder
   (person). Guided bulk history import mode: the Validate page has a by-concept
   view that groups pending movements by normalized description and validates
   a group in one block, optionally creating a rule, with undo. Follow-up: a
   full transaction editor.
   Settings page: cost centers, categories, rules and the interface language
   are configurable from the interface. Follow-ups: recurring templates
   management, detector thresholds, accounts editing.
8. Transactions page with filters (account, type, status and text search).
   Manual entries and recurring templates remain to be built.
9. Reports and proration (cash view vs real-cost view).
10. Month close, recurring-charge detector and manual backup.
11. User documentation and usability test.

## Optional modules (after the core)

These depend on the core but are not part of it.

- **Mortgage / loan schedules**: paste or upload an amortization schedule;
  split each loan settlement into interest and capital.
- **Rental and tax report**: owner shares per property, country-specific tax
  rules, and a report for the tax adviser. Core categories carry only name,
  kind, fixed/variable and an optional income source; the mapping to tax-report lines lives in this
  module.

## Later items

- Pluggable AI classifier layer for the validation queue.
- Generic bank-file column mapping.
- More banks and countries.
- Installable executable or installer packaging.
- Onboarding wizard.
- Application-level encrypted backup.
- Optional login and remote access. Remote access is a scalability horizon,
  not a priority: the app keeps a configurable listen address and makes no
  localhost-only assumption.

## Explicitly out of scope

- Fuel tracking as a product feature. Fuel is an ordinary expense category;
  per-vehicle fuel logs, EUR/km and l/100 km belong to a separate future
  project.
- Owner shares and country tax rules in the core.
- Cross-currency conversion in v1.
- Application-level encryption in v1.
- Deviation alerts and manual budget caps in v1.
- Importing the user's previous spreadsheet history; it is used only to
  compare totals of a closed year.
