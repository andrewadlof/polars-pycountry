"""Shared fixtures.

The parity suite compares this plugin against `pycountry` itself. For that
comparison to mean anything, both sides must read the *same* ISO tables --
otherwise a disagreement could just be two different snapshots. `pycountry`
vendors the `iso-codes` JSON files byte for byte, and `scripts/refresh_iso.py`
pulls them from the same place, so the two normally agree by construction;
`test_data.py` asserts that rather than assuming it.

Where they do drift -- a newer `pycountry` release than the vendored snapshot
-- `pycountry` is repointed at the vendored files here, so the parity tests
still exercise the algorithm rather than the calendar.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

import pycountry
import pycountry.db
import pytest

#: One record from any table, as the scalar helpers return it.
Record: TypeAlias = dict[str, str | int | bool | None]

# repo_root/tests/conftest.py -> repo_root/src/data
VENDORED_DATA = Path(__file__).resolve().parents[1] / "src" / "data"


def must(record: Record | None, context: object = "") -> Record:
    """Assert a lookup matched, and hand back the record.

    Every scalar helper returns `None` for "no match", so a test that reads a
    field off one has to narrow it first. Doing that through a helper keeps
    the failure message useful -- a bare `None` subscript reports a
    `TypeError` from deep inside the assertion rather than saying which input
    failed to resolve.

    Parameters
    ----------
    record : Record | None
        The result of a lookup.
    context : object, optional
        Included in the failure message, usually the input that was resolved.

    Returns
    -------
    Record
        The record, guaranteed non-`None`.
    """
    where = f" for {context!r}" if context else ""
    assert record is not None, f"expected a match{where}"
    return record


#: `(pycountry database attribute, vendored filename)`.
TABLES = (
    ("countries", "iso3166-1.json"),
    ("subdivisions", "iso3166-2.json"),
    ("historic_countries", "iso3166-3.json"),
    ("currencies", "iso4217.json"),
)


@pytest.fixture(scope="session", autouse=True)
def reference() -> pycountry.db.Database:
    """Point `pycountry` at the tables this crate compiles in.

    Autouse and session-scoped: every parity test needs the two sides reading
    the same bytes, and repointing is cheap because `pycountry` loads its
    databases lazily on first access.

    Returns
    -------
    pycountry.db.Database
        The repointed country database, for tests that want it by name.
    """
    for attribute, filename in TABLES:
        path = VENDORED_DATA / filename
        assert path.is_file(), f"vendored table missing at {path}"
        database = getattr(pycountry, attribute)
        database.filename = str(path)
        # `_load` is lazy and idempotent; clearing forces the next access to
        # read the file just assigned rather than one already in memory.
        database._clear()
    return pycountry.countries


@pytest.fixture(scope="session")
def all_country_values(reference: pycountry.db.Database) -> list[str]:
    """Every indexed field value across ISO 3166-1.

    This is the corpus the exact-lookup parity test sweeps: if the two
    implementations agree on every string the standard actually contains,
    they agree on every exact lookup that can succeed.

    Parameters
    ----------
    reference : pycountry.db.Database
        The repointed country database.

    Returns
    -------
    list[str]
        Deduplicated field values, in table order.
    """
    seen: dict[str, None] = {}
    for country in reference:
        for value in country._fields.values():
            seen.setdefault(value, None)
    return list(seen)


@pytest.fixture(scope="session")
def all_subdivision_codes() -> list[str]:
    """Every ISO 3166-2 code, in table order.

    Returns
    -------
    list[str]
        Subdivision codes such as `"US-CA"`.
    """
    return [s.code for s in pycountry.subdivisions]
