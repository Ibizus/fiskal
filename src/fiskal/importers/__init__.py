"""Bank statement import adapters and the import service.

Modules in this package translate downloaded bank/files into Fiskal's neutral
transaction representation. ``base`` and the per-bank parsers are
framework-free; ``service`` is the thin database-touching layer that persists
parsed statements and supports whole-import revert.
"""

from fiskal.importers.base import (
    ImportedTransaction,
    Importer,
    ImporterError,
    ImportResult,
    ImportRevertError,
    ImportSummary,
    NearDuplicate,
    ParsedStatement,
)
from fiskal.importers.openbank import OpenbankImporter
from fiskal.importers.service import import_statement, revert_import

__all__ = [
    "ImportResult",
    "ImportRevertError",
    "ImportSummary",
    "ImportedTransaction",
    "Importer",
    "ImporterError",
    "NearDuplicate",
    "OpenbankImporter",
    "ParsedStatement",
    "import_statement",
    "revert_import",
]
