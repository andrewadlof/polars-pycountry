"""The vendored tables, and replacing them in a running process.

Two jobs here. The first is provenance: the parity suite is only meaningful if
the tables compiled into this crate are the ones `pycountry` ships, so that is
asserted rather than assumed.

The second is the swap. Every test in that half mutates process-global state,
so each one restores the tables it started with. `_restore_tables` does that
even on failure; without it a single failing assertion would leave every later
test in the session resolving against a toy table.
"""

from __future__ import annotations

import polars_pycountry as pc

# `tests/` is not a package, so pytest's rootdir insertion puts it on
# sys.path and conftest imports as a plain module.
from conftest import TABLES, VENDORED_DATA, must

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pycountry
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Files a replacement directory has to hold.
TABLE_FILENAMES = tuple(filename for _, filename in TABLES)


@pytest.fixture(autouse=True)
def _restore_tables() -> Iterator[None]:
    """Put the vendored tables back after each test, however it ends.

    Yields
    ------
    None
        Control to the test, then restores the tables.
    """
    yield
    pc.load_iso_data(VENDORED_DATA)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("attribute", "filename"), TABLES)
def test_the_vendored_tables_are_the_ones_pycountry_ships(
    attribute: str, filename: str
) -> None:
    """The foundation the parity suite rests on, asserted not assumed.

    `pycountry` vendors the upstream `iso-codes` JSON unchanged, and
    `scripts/refresh_iso.py` pulls it from the same place, so the two should
    be byte-identical. When they are not, the installed `pycountry` has moved
    ahead of the vendored snapshot -- `conftest.reference` repoints it at
    these files so the parity tests still compare algorithms, and this test is
    the signal to run `just refresh-iso`.
    """
    # `conftest.reference` has already repointed the databases, so the
    # original path is recovered from the installed package rather than from
    # the (now rewritten) attribute.
    shipped = Path(pycountry.DATABASE_DIR) / filename
    vendored = VENDORED_DATA / filename
    assert vendored.is_file()
    assert shipped.is_file(), (
        f"pycountry ships no {filename} for `pycountry.{attribute}`"
    )
    assert vendored.read_bytes() == shipped.read_bytes(), (
        f"{filename} has drifted from the installed pycountry "
        f"({pycountry.__version__}); run `just refresh-iso`"
    )


def test_the_snapshot_reports_its_upstream_release() -> None:
    """Provenance has to be readable at runtime, not just in the repo."""
    version = pc.iso_version()
    assert version == (VENDORED_DATA / "VERSION").read_text().strip()
    assert version != "unknown"


def test_table_sizes_match_the_files() -> None:
    """A sanity check that all four tables actually loaded."""
    sizes = pc.table_sizes()
    for root_key, filename in (
        ("3166-1", "iso3166-1.json"),
        ("3166-2", "iso3166-2.json"),
        ("3166-3", "iso3166-3.json"),
        ("4217", "iso4217.json"),
    ):
        records = json.loads((VENDORED_DATA / filename).read_text())[root_key]
        assert sizes[root_key] == len(records), root_key


def test_the_data_path_env_var_is_exported() -> None:
    """The startup override is part of the public surface."""
    assert pc.DATA_PATH_ENV == "POLARS_COUNTRY_DATA"


# ---------------------------------------------------------------------------
# Replacing the tables
# ---------------------------------------------------------------------------


def _fake_tables(destination: Path, *, name: str = "Frobnicia") -> Path:
    """Copy the vendored tables, renaming one country.

    A renamed country is what makes a swap *observable*: the version stamp
    alone would only prove the loader read a file, not that lookups use it.

    Parameters
    ----------
    destination : pathlib.Path
        Directory to write the copy into.
    name : str, default "Frobnicia"
        The name to give the first country in the table.

    Returns
    -------
    pathlib.Path
        The directory that was written.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for filename in TABLE_FILENAMES:
        shutil.copy(VENDORED_DATA / filename, destination / filename)

    path = destination / "iso3166-1.json"
    tables = json.loads(path.read_text(encoding="utf-8"))
    tables["3166-1"][0]["name"] = name
    path.write_text(json.dumps(tables), encoding="utf-8")
    (destination / "VERSION").write_text("9.9.9-fake\n", encoding="utf-8")
    return destination


def test_loading_a_directory_changes_what_lookups_return(
    tmp_path: Path,
) -> None:
    """A swapped-in table takes effect for lookups that start after it."""
    assert must(pc.lookup_country("Aruba"))["alpha_2"] == "AW"

    version = pc.load_iso_data(_fake_tables(tmp_path / "fake"))

    assert version == "9.9.9-fake"
    assert pc.iso_version() == "9.9.9-fake"
    assert pc.lookup_country("Aruba") is None
    assert must(pc.lookup_country("Frobnicia"))["alpha_2"] == "AW"


def test_the_expression_path_sees_the_new_tables_too(tmp_path: Path) -> None:
    """Swapping affects Polars expressions, not just the scalar helpers."""
    df = pl.DataFrame({"c": ["Aruba", "Frobnicia", "US", None]})
    assert df.select(pc.alpha_2("c"))["c"].to_list() == [
        "AW",
        None,
        "US",
        None,
    ]

    pc.load_iso_data(_fake_tables(tmp_path / "fake"))

    assert df.select(pc.alpha_2("c"))["c"].to_list() == [
        None,
        "AW",
        "US",
        None,
    ]


def test_a_path_object_and_a_string_both_work(tmp_path: Path) -> None:
    """The argument is a directory, in whichever spelling."""
    directory = _fake_tables(tmp_path / "fake")
    assert pc.load_iso_data(directory) == "9.9.9-fake"
    assert pc.load_iso_data(str(directory)) == "9.9.9-fake"


def test_a_directory_with_no_version_file_loads_as_unknown(
    tmp_path: Path,
) -> None:
    """The stamp is informational, so its absence is not a failure."""
    directory = _fake_tables(tmp_path / "fake")
    (directory / "VERSION").unlink()
    assert pc.load_iso_data(directory) == "unknown"
    assert must(pc.lookup_country("Frobnicia"))["alpha_2"] == "AW"


# ---------------------------------------------------------------------------
# Rejecting bad tables
# ---------------------------------------------------------------------------


def test_a_missing_table_is_rejected(tmp_path: Path) -> None:
    """All four or nothing: a partial directory is not a valid snapshot."""
    directory = _fake_tables(tmp_path / "fake")
    (directory / "iso4217.json").unlink()
    before = pc.iso_version()

    with pytest.raises(ValueError, match="could not read"):
        pc.load_iso_data(directory)

    assert pc.iso_version() == before
    assert must(pc.lookup_country("Aruba"))["alpha_2"] == "AW"


def test_a_truncated_table_is_rejected(tmp_path: Path) -> None:
    """The record floor is the check that catches a bad download.

    A truncated file is still valid JSON of the right shape, so without a
    floor the only symptom would be countries quietly going missing.
    """
    directory = _fake_tables(tmp_path / "fake")
    path = directory / "iso3166-1.json"
    tables = json.loads(path.read_text())
    tables["3166-1"] = tables["3166-1"][:5]
    path.write_text(json.dumps(tables))
    before = pc.iso_version()

    with pytest.raises(ValueError, match="fewer than"):
        pc.load_iso_data(directory)

    assert pc.iso_version() == before
    assert must(pc.lookup_country("Aruba"))["alpha_2"] == "AW"


def test_an_html_error_page_is_rejected(tmp_path: Path) -> None:
    """The shape a failed download actually takes."""
    directory = _fake_tables(tmp_path / "fake")
    (directory / "iso3166-2.json").write_text("<html>404 Not Found</html>")
    before = pc.iso_version()

    with pytest.raises(ValueError, match="not valid JSON"):
        pc.load_iso_data(directory)

    assert pc.iso_version() == before


def test_a_table_with_the_wrong_root_key_is_rejected(tmp_path: Path) -> None:
    """Valid JSON of the wrong shape must not load as an empty table."""
    directory = _fake_tables(tmp_path / "fake")
    (directory / "iso4217.json").write_text('{"currencies": []}')

    with pytest.raises(ValueError, match="4217"):
        pc.load_iso_data(directory)


def test_a_missing_directory_is_rejected(tmp_path: Path) -> None:
    """A typo'd path raises rather than silently keeping the old tables."""
    before = pc.iso_version()
    with pytest.raises(ValueError, match="could not read"):
        pc.load_iso_data(tmp_path / "does-not-exist")
    assert pc.iso_version() == before


# ---------------------------------------------------------------------------
# Refreshing over the network
# ---------------------------------------------------------------------------


def _upstream_names(directory: Path) -> Path:
    """Copy the vendored tables under their *upstream* filenames.

    `refresh_iso_data` fetches `iso_3166-1.json` and friends, which is what
    the `iso-codes` project publishes; the local copies drop the underscore.

    Parameters
    ----------
    directory : pathlib.Path
        Directory to write into.

    Returns
    -------
    pathlib.Path
        The directory that was written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for remote, local in pc.TABLE_FILES:
        shutil.copy(VENDORED_DATA / local, directory / remote)
    return directory


def test_refresh_downloads_and_loads(tmp_path: Path) -> None:
    """`refresh_iso_data` fetches, validates, and swaps in one call.

    Pointed at a `file://` URL so the test does not depend on the network or
    on what the upstream repository happens to be serving today.
    """
    source = _upstream_names(tmp_path / "remote")
    pc.load_iso_data(_fake_tables(tmp_path / "fake"))
    assert pc.lookup_country("Aruba") is None

    pc.refresh_iso_data(source.as_uri())

    assert must(pc.lookup_country("Aruba"))["alpha_2"] == "AW"


def test_refresh_can_save_a_copy(tmp_path: Path) -> None:
    """`save_to` writes the fetched tables for later offline reuse."""
    source = _upstream_names(tmp_path / "remote")
    destination = tmp_path / "cached"

    pc.refresh_iso_data(source.as_uri(), save_to=destination)

    for filename in TABLE_FILENAMES:
        assert (destination / filename).read_bytes() == (
            VENDORED_DATA / filename
        ).read_bytes()
    # And the saved copy is loadable on its own.
    assert pc.load_iso_data(destination) == "unknown"
    assert must(pc.lookup_country("Aruba"))["alpha_2"] == "AW"


def test_refresh_rejects_a_bad_download_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """Validation happens before the save, so nothing bad reaches disk."""
    source = _upstream_names(tmp_path / "remote")
    (source / "iso_4217.json").write_text("<html>404</html>")
    destination = tmp_path / "cached"
    before = pc.iso_version()

    with pytest.raises(ValueError, match="not valid JSON"):
        pc.refresh_iso_data(source.as_uri(), save_to=destination)

    assert pc.iso_version() == before
    assert not destination.exists()


def test_the_default_url_points_at_the_upstream_project() -> None:
    """The default source is the canonical one, over TLS."""
    assert pc.ISO_CODES_URL.startswith("https://")
    assert "iso-codes" in pc.ISO_CODES_URL
