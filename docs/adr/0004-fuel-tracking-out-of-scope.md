# Fuel tracking is out of scope

Fuel tracking is removed from Fiskal. Fuel is an ordinary expense category; the
per-vehicle fuel log, EUR/km and l/100 km calculations belong to a separate
future project.

The feature duplicated concepts that the core already handles as categories and
cost centers, and a photo-based fuel log with AI is a product of its own. A
fuel transaction can still be assigned to a vehicle cost center by hand during
validation.

## Consequences

- The `fuel/` package is removed.
- No fuel-specific importer, classifier layer or acceptance criterion remains.
- Vehicle cost reports, if added later, will rely on ordinary categorization
  and optional modules.
