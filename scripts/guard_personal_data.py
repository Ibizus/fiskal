"""Personal-data guard for the Fiskal repository.

This script scans the repository for files that may contain personal or
financial data. It is intentionally conservative: it fails when it finds data
files outside the allowed fixture directory or when text files contain patterns
that look like account numbers or IBANs.

Rules:
- Data files (``.db``, ``.csv``, ``.xls``, ``.xlsx``, ``.ofx``, ``.qif``,
  ``.pdf``, ``.jpg``, ``.jpeg``, ``.png``) are only allowed under
  ``tests/fixtures/anon/``.
- Text files must not contain a 13-19 digit number or an IBAN.

The script is meant to run in CI and pre-commit. It returns a non-zero exit
code when a potential violation is found.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "anon"

SKIPPED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".tox",
    "node_modules",
    "htmlcov",
}

DATA_EXTENSIONS = {
    ".db",
    ".db-journal",
    ".csv",
    ".xls",
    ".xlsx",
    ".ofx",
    ".qif",
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".tiff",
}

IBAN_RE = re.compile(
    # IBAN: two-letter country code, two check digits, then up to 30 alphanumeric
    # characters. Requiring at least 11 BBAN characters avoids matching short
    # tokens such as "py312" while still catching real IBANs (minimum length 15).
    r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b",
    re.IGNORECASE,
)
ACCOUNT_NUMBER_RE = re.compile(r"\b\d{13,19}\b")

# Paths that are allowed to contain digit patterns (test fixtures, lock files).
ALLOWED_TEXT_PATHS = {
    "uv.lock",
    "coverage.xml",
    ".coverage",
}


def _is_in_allowed_fixture(path: Path) -> bool:
    """Return True if ``path`` lives under the anonymous fixture directory."""
    try:
        path.relative_to(ALLOWED_FIXTURE_DIR)
    except ValueError:
        return False
    return True


def _is_skipped_path(path: Path) -> bool:
    """Return True if ``path`` is inside a generated or external directory."""
    return any(part in SKIPPED_DIRECTORIES for part in path.parts)


def find_disallowed_data_files() -> list[Path]:
    """Return data files that appear outside the allowed fixture directory."""
    violations: list[Path] = []
    for root, _dirs, files in os.walk(REPO_ROOT):
        root_path = Path(root)
        if _is_skipped_path(root_path):
            continue
        for name in files:
            path = root_path / name
            if _is_skipped_path(path):
                continue
            if path.suffix.lower() in DATA_EXTENSIONS and not _is_in_allowed_fixture(
                path
            ):
                violations.append(path)
    return violations


def _is_allowed_text_file(relative: Path) -> bool:
    """Return True if the file is on the allow-list for digit patterns."""
    return relative.as_posix() in ALLOWED_TEXT_PATHS


def find_sensitive_patterns() -> list[tuple[Path, str]]:
    """Return text files that contain account-number-like or IBAN patterns."""
    violations: list[tuple[Path, str]] = []
    for root, _dirs, files in os.walk(REPO_ROOT):
        root_path = Path(root)
        if _is_skipped_path(root_path):
            continue
        for name in files:
            path = root_path / name
            if _is_skipped_path(path):
                continue
            if not path.is_file():
                continue
            # Only inspect human-readable text files.
            if path.suffix.lower() in DATA_EXTENSIONS:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            relative = path.relative_to(REPO_ROOT)
            if _is_allowed_text_file(relative):
                continue
            if ACCOUNT_NUMBER_RE.search(text) or IBAN_RE.search(text):
                violations.append((relative, "contains a 13-19 digit number or IBAN"))
    return violations


def main() -> int:
    """Run the guard and report violations."""
    data_violations = find_disallowed_data_files()
    pattern_violations = find_sensitive_patterns()

    exit_code = 0
    if data_violations:
        print("Personal data files found outside tests/fixtures/anon/:")
        for path in data_violations:
            print(f"  - {path.relative_to(REPO_ROOT)}")
        exit_code = 1

    if pattern_violations:
        print("Files containing possible account numbers or IBANs:")
        for relative, reason in pattern_violations:
            print(f"  - {relative}: {reason}")
        exit_code = 1

    if exit_code == 0:
        print("No personal or financial data detected.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
