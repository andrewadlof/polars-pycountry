"""Re-vendor the ISO code tables from the upstream `iso-codes` project.

Overwrites the JSON files in `src/data/`, which are compiled into the binary,
and rewrites `src/data/VERSION` with the upstream release the snapshot came
from.

The files are pulled from Debian's `iso-codes` repository, which is where
`pycountry` gets them too -- it vendors these bytes unchanged. Taking them from
the same place is what makes the parity suite meaningful: a disagreement with
`pycountry` can only be an algorithm difference, never two different snapshots.

Rebuild and re-run the parity suite afterwards. `tests/test_parity.py` reads
the vendored files directly, so it picks up the new tables automatically.

Usage
-----
    just refresh-iso
    # then: just check && just bump patch
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

#: Raw-file base for the upstream repository.
BASE = "https://salsa.debian.org/iso-codes-team/iso-codes/-/raw/main"

#: The tables themselves live under `data/`; the changelog sits at the root.
DATA_BASE = f"{BASE}/data"

#: Where the release version is recorded upstream. There is no version field
#: inside the JSON itself, so the changelog's topmost heading is the stamp.
CHANGELOG_URL = f"{BASE}/CHANGELOG.md"

DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "data"

#: `(upstream filename, vendored filename, JSON root key, minimum records)`.
#:
#: The record floors are a sanity check on the download, not a schema: a
#: truncated file or an HTML error page parses as *something*, and the failure
#: would otherwise only surface as countries quietly going missing.
TABLES = (
    ("iso_3166-1.json", "iso3166-1.json", "3166-1", 200),
    ("iso_3166-2.json", "iso3166-2.json", "3166-2", 4000),
    ("iso_3166-3.json", "iso3166-3.json", "3166-3", 20),
    ("iso_4217.json", "iso4217.json", "4217", 150),
)

_VERSION_HEADING = re.compile(r"^##\s*\[([0-9][0-9A-Za-z.\-]*)\]")


def _fetch(url: str, *, timeout: float = 60.0) -> bytes:
    """Download `url` and return the raw bytes.

    Parameters
    ----------
    url : str
        Address to fetch.
    timeout : float, default 60.0
        Seconds to wait.

    Returns
    -------
    bytes
        The response body.
    """
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def _upstream_version() -> str:
    """Read the current `iso-codes` release from its changelog.

    Returns
    -------
    str
        The topmost released version, for example `"4.20.1"`.

    Raises
    ------
    ValueError
        If the changelog has no recognizable version heading, which means the
        format changed and this parser needs updating rather than silently
        stamping the snapshot `"unknown"`.
    """
    text = _fetch(CHANGELOG_URL).decode("utf-8")
    for line in text.splitlines():
        match = _VERSION_HEADING.match(line.strip())
        if match:
            return match.group(1)
    msg = f"no version heading found in {CHANGELOG_URL}"
    raise ValueError(msg)


def main() -> int:
    """Download every table, sanity-check it, and write it into the crate.

    Returns
    -------
    int
        Process exit status: 0 on success, 1 if a download looks wrong.
    """
    version = _upstream_version()
    print(f"Upstream iso-codes {version}")

    fetched: dict[Path, bytes] = {}
    for remote, local, root_key, floor in TABLES:
        url = f"{DATA_BASE}/{remote}"
        print(f"Fetching {url} ...")
        raw = _fetch(url)

        try:
            records = json.loads(raw)[root_key]
        except (ValueError, KeyError, TypeError) as exc:
            print(
                f"error: {remote} is not the expected table: {exc}",
                file=sys.stderr,
            )
            return 1

        if len(records) < floor:
            print(
                f"error: {remote} holds {len(records)} records, "
                f"fewer than the {floor} expected -- refusing to vendor it",
                file=sys.stderr,
            )
            return 1

        fetched[DATA_DIR / local] = raw

    # Nothing is written until every table has been validated, so a partial
    # refresh cannot leave the crate holding a mix of two snapshots.
    changed = []
    for path, raw in fetched.items():
        old = path.read_bytes() if path.is_file() else b""
        if raw != old:
            path.write_bytes(raw)
            changed.append(path.name)

    stamp = DATA_DIR / "VERSION"
    old_version = "none"
    if stamp.is_file():
        old_version = stamp.read_text(encoding="utf-8").strip()
    stamp.write_text(f"{version}\n", encoding="utf-8")

    if not changed and old_version == version:
        print(f"Already up to date ({version}).")
        return 0

    touched = ", ".join(changed) or "nothing"
    print(f"Updated {touched}: {old_version} -> {version}")
    print("Now run: just check   (parity re-reads these files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
