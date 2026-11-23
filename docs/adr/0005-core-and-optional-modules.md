# Core versus optional modules

The v1 core is limited to importing, classifying, validating and reporting.
Owner shares and country tax rules live outside the core in an optional rental
and tax module.

This keeps the core small and independent of jurisdiction-specific rules that
change often and are irrelevant to users who do not need them. Optional modules
add their own tables and parameters without altering the core schema.

## Consequences

- The core category model carries only name, kind and fixed/variable; tax-report
  mappings live in the optional module.
- Owner percentages are not hard-coded to any default value.
- The rental/tax module and the core can be tested and released separately.
