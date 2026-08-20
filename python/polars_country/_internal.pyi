"""Type stubs for the compiled Rust extension module."""

from typing import TypeAlias

DATA_PATH_ENV: str
VERSION_FILE: str

#: One record from any of the tables. Values are strings except `historic`
#: (bool) and `score` (int), both of which only appear on country results.
#: Spelled as a `TypeAlias` assignment rather than a `type` statement, which
#: needs Python 3.12 and this package supports 3.10.
Record: TypeAlias = dict[str, str | int | bool | None]

def lookup_country(
    value: str | None, *, fuzzy: bool = False, historic: bool = False
) -> Record | None:
    """Resolve one country string, or `None` when nothing matched."""

def search_countries(value: str, *, limit: int | None = None) -> list[Record]:
    """Rank every country the fuzzy search scores, best first."""

def lookup_subdivision(
    value: str | None, *, country: str | None = None, fuzzy: bool = False
) -> Record | None:
    """Resolve one ISO 3166-2 subdivision, optionally scoped to a country."""

def lookup_currency(value: str | None) -> Record | None:
    """Resolve one ISO 4217 currency, or `None` when nothing matched."""

def iso_version() -> str:
    """Report the `iso-codes` release stamp of the tables in use."""

def table_sizes() -> dict[str, int]:
    """Report how many records each loaded table holds."""

def load_iso_dir(path: str) -> str:
    """Replace the live tables from a directory; return the new version."""
