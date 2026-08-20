"""Replacing the ISO tables in a running process.

The bundled snapshot is fixed at build time, which is the right default: no
network at import, no cache directory, no first-call latency spike. But ISO
3166 changes -- countries are added, subdivisions are renamed and renumbered,
codes are withdrawn -- and a long-lived process would otherwise be stuck with
whatever tables it read when it resolved its first country.

`load_iso_data` swaps in a directory you already have. `refresh_iso_data`
downloads the current tables from the upstream `iso-codes` project and swaps
those in. Neither runs unless called, so the default path stays offline.

Both validate before swapping: a directory missing a table, or holding one
that does not parse or is implausibly small, is rejected and the process keeps
the tables it already had. That last check earns its keep -- a truncated
download or an HTML error page is still valid *something*, and without a floor
on the record count the only symptom would be countries quietly going missing.
"""

from __future__ import annotations

from polars_country._internal import VERSION_FILE, load_iso_dir

import tempfile
import urllib.request
from pathlib import Path

__all__ = ["ISO_CODES_URL", "TABLE_FILES", "load_iso_data", "refresh_iso_data"]

#: Raw-file base `refresh_iso_data` downloads from by default. This is the
#: Debian `iso-codes` project, which is also where `pycountry` takes its
#: tables from -- it ships these bytes unchanged.
ISO_CODES_URL = (
    "https://salsa.debian.org/iso-codes-team/iso-codes/-/raw/main/data"
)

#: `(upstream filename, local filename)` for each table the plugin reads.
TABLE_FILES = (
    ("iso_3166-1.json", "iso3166-1.json"),
    ("iso_3166-2.json", "iso3166-2.json"),
    ("iso_3166-3.json", "iso3166-3.json"),
    ("iso_4217.json", "iso4217.json"),
)


def load_iso_data(source: str | Path) -> str:
    """Replace the live ISO tables, and return the new version stamp.

    Takes effect for every lookup that *starts* after it returns. A query
    already running keeps the tables it started with, so no column can be
    resolved against two different snapshots.

    Parameters
    ----------
    source : str | pathlib.Path
        Directory holding `iso3166-1.json`, `iso3166-2.json`,
        `iso3166-3.json` and `iso4217.json`, in the shape the `iso-codes`
        project publishes them. A `VERSION` file alongside them is read as the
        stamp if present.

    Returns
    -------
    str
        The `VERSION` stamp of the newly loaded tables, or `"unknown"` when
        the directory carries no `VERSION` file.

    Raises
    ------
    ValueError
        If a table is missing, unreadable, unparseable, or too small to be a
        complete copy. The previously loaded tables stay in use.

    Examples
    --------
    >>> import polars_country as pc
    >>> pc.load_iso_data("/mnt/reference/iso-codes")  # doctest: +SKIP
    '4.20.1'
    """
    return load_iso_dir(str(source))


def refresh_iso_data(
    url: str = ISO_CODES_URL,
    *,
    timeout: float = 60.0,
    save_to: str | Path | None = None,
) -> str:
    """Download the current ISO tables and load them.

    This is the only function in the package that touches the network, and
    only when you call it. Call it before the lookup whose results should
    reflect the newer tables.

    Parameters
    ----------
    url : str, default `ISO_CODES_URL`
        Directory URL to fetch the table files from. Point this at an internal
        mirror if outbound access is restricted; it must serve the four
        `iso_*.json` files under their upstream names.
    timeout : float, default 60.0
        Seconds to wait for each download.
    save_to : str | pathlib.Path | None, optional
        If given, also write the downloaded tables here, so a later run can
        `load_iso_data` them (or `POLARS_COUNTRY_DATA` can point at the
        directory) without going back to the network. Only written once the
        tables have been validated and loaded.

    Returns
    -------
    str
        The version stamp of the newly loaded tables. The upstream directory
        carries no `VERSION` file, so this is `"unknown"` unless `save_to`
        pointed at a directory that already had one.

    Raises
    ------
    OSError
        If a download fails. The previously loaded tables stay in use.
    ValueError
        If what comes back is not a usable set of ISO tables. The previously
        loaded tables stay in use.

    Examples
    --------
    >>> import polars_country as pc
    >>> pc.refresh_iso_data(save_to="iso-codes/")  # doctest: +SKIP
    'unknown'
    """
    base = url.rstrip("/")
    downloaded: dict[str, bytes] = {}
    for remote, local in TABLE_FILES:
        with urllib.request.urlopen(
            f"{base}/{remote}", timeout=timeout
        ) as response:
            downloaded[local] = response.read()

    # The plugin loads from a directory, so the download lands in a temporary
    # one first. Validation happens inside `load_iso_data`, which means a bad
    # download never reaches `save_to`.
    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging)
        for local, payload in downloaded.items():
            (staged / local).write_bytes(payload)
        if save_to is not None:
            # An existing stamp beside the destination is the caller's, so it
            # is carried through rather than overwritten with "unknown".
            existing = Path(save_to) / VERSION_FILE
            if existing.is_file():
                (staged / VERSION_FILE).write_bytes(existing.read_bytes())
        version = load_iso_data(staged)

        if save_to is not None:
            destination = Path(save_to)
            destination.mkdir(parents=True, exist_ok=True)
            for local, payload in downloaded.items():
                (destination / local).write_bytes(payload)

    return version
