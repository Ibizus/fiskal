# Imported bank data is immutable and validated or closed data changes only explicitly

Data imported from the bank (dates, amount, description, balance) is never
edited; only classification and note are editable. Manual entries are fully
editable. Rule changes reclassify only pending transactions; validated
transactions never change on their own. A closed month rejects import and
reclassification until it is explicitly reopened, and the reopening is
recorded.

This preserves an audit trail of the bank record and prevents accidental
changes to data the user has already reviewed. Corrections happen by creating
new rules or by explicit bulk actions with a preview.

## Consequences

- The data model distinguishes imported fields from editable classification
  fields.
- Bulk reclassification of validated history must show a preview before it runs.
- Reopening a closed month is a logged event.
