"""Throughput comparison against the `map_elements` path this replaces.

Marked `bench` and deselected by default (`just bench` runs it), because it is
a timing measurement, not a correctness check. The one assertion per case is a
floor generous enough that it fails only on a genuine performance regression,
not on a busy machine.

Two workloads are measured, because they have different shapes. Exact lookup
is a hash probe per row and the win is almost entirely the interpreter
round-trip. Fuzzy search is a scan over ~250 countries and ~5,000 subdivisions
per *distinct* input, memoized per call, so the win there also depends on how
much a column repeats itself -- which real columns do, heavily.
"""

from __future__ import annotations

import polars_pycountry as pc

import random
import time
from typing import TYPE_CHECKING

import polars as pl
import pycountry
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

ROWS = 200_000

# The fuzzy case runs on far fewer rows, because `pycountry`'s `search_fuzzy`
# costs about 15 ms per call -- it scans every country and every subdivision,
# in Python, with no cache. At 200,000 rows the reference alone would take
# most of an hour, which is not a benchmark anyone runs. Both sides are
# measured on the same count, so the ratio is still the ratio.
FUZZY_ROWS = 2_000

# Floors, not targets: they leave room for a slower or busier host before they
# start crying wolf.
#
# The exact floor is deliberately modest. `pycountry.countries.lookup` is a
# dict probe -- sub-microsecond -- so nearly all of what `map_elements` costs
# there is the interpreter round-trip, and a single-threaded native lookup can
# only win by so much. The fuzzy floor is two orders of magnitude higher
# because that is where the reference actually does work.
MIN_SPEEDUP_EXACT = 5.0
MIN_SPEEDUP_FUZZY = 100.0

pytestmark = pytest.mark.bench


@pytest.fixture(scope="module")
def countries() -> list[str]:
    """Build a realistic mix of country spellings.

    Every ISO 3166-1 country appears, in the spellings a real column carries:
    codes, numbers, names, and case noise.

    Returns
    -------
    list[str]
        `ROWS` country strings.
    """
    rng = random.Random(20260820)
    vocabulary: list[str] = []
    for country in pycountry.countries:
        vocabulary += [
            country.alpha_2,
            country.alpha_3,
            country.numeric,
            country.name,
            country.name.lower(),
            country.name.upper(),
        ]
    vocabulary += ["not a country", "", "   "]
    return [rng.choice(vocabulary) for _ in range(ROWS)]


@pytest.fixture(scope="module")
def messy_countries() -> list[str]:
    """Build country names that only the fuzzy path resolves.

    Accent-stripped spellings and subdivision names, which is what a column
    typed by humans actually looks like.

    Returns
    -------
    list[str]
        `FUZZY_ROWS` strings needing the fuzzy path.
    """
    rng = random.Random(20260820)
    vocabulary = [
        "Cote d'Ivoire",
        "Aland Islands",
        "Curacao",
        "Reunion",
        "Saint Barthelemy",
        "Republic of Korea",
        "Texas",
        "Bayern",
        "Ontario",
        "New South Wales",
        "USA",
        "UK",
        "Holland",
        "not a country at all",
    ]
    return [rng.choice(vocabulary) for _ in range(FUZZY_ROWS)]


def _timed(label: str, fn: Callable[[], object], rows: int) -> float:
    """Run `fn`, print its throughput, and return the elapsed seconds.

    Parameters
    ----------
    label : str
        Name to print alongside the measurement.
    fn : Callable[[], object]
        Zero-argument callable to time.
    rows : int
        How many rows `fn` processed, for the throughput figure.

    Returns
    -------
    float
        Elapsed wall-clock seconds.
    """
    start = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - start
    print(f"  {label:<34} {elapsed:7.3f}s  {rows / elapsed:>12,.0f} rows/s")
    return elapsed


def _warm_up() -> None:
    """Force the one-off table parse before anything is timed.

    The ISO tables are parsed lazily, on the first lookup in the process --
    about 40 ms for half a megabyte of JSON. That is a real cost, but it is
    paid once, not per row, and leaving it inside the first measurement made
    the single-threaded figure swing by a factor of two between runs.
    """
    pc.lookup_country("US", fuzzy=True)


def _report(baseline: float, serial: float, parallel: float) -> None:
    """Print the two speedups.

    Parameters
    ----------
    baseline : float
        Seconds taken by the `map_elements` path.
    serial : float
        Seconds taken with `parallel=False`.
    parallel : float
        Seconds taken with `parallel=True`.
    """
    print(
        f"  speedup: {baseline / serial:.1f}x single-threaded, "
        f"{baseline / parallel:.1f}x multi-threaded"
    )


def test_exact_lookup_throughput(countries: list[str]) -> None:
    """Exact lookup must be far faster than driving `pycountry` per row."""
    df = pl.DataFrame({"c": countries})

    def via_map_elements() -> None:
        def resolve(value: str) -> str | None:
            try:
                return pycountry.countries.lookup(value).alpha_2
            except LookupError:
                return None

        df.with_columns(
            pl.col("c").map_elements(resolve, return_dtype=pl.String)
        )

    _warm_up()
    print(f"\nexact lookup, {ROWS:,} rows:")
    baseline = _timed("pycountry via map_elements", via_map_elements, ROWS)
    serial = _timed(
        "plugin (parallel=False)",
        lambda: df.with_columns(pc.alpha_2("c", parallel=False)),
        ROWS,
    )
    parallel = _timed(
        "plugin (parallel=True)",
        lambda: df.with_columns(pc.alpha_2("c", parallel=True)),
        ROWS,
    )
    _report(baseline, serial, parallel)

    assert baseline / serial > MIN_SPEEDUP_EXACT, (
        f"plugin is only {baseline / serial:.1f}x faster than map_elements; "
        f"expected at least {MIN_SPEEDUP_EXACT}x. If this is far below the "
        f"floor rather than just under it, check that the installed extension "
        f"is an optimized build: `maturin develop` without `--release` is far "
        f"slower here, which measures the profile rather than the code. "
        f"`just bench` builds the right one."
    )


def test_fuzzy_throughput(messy_countries: list[str]) -> None:
    """The fuzzy path is where `pycountry` is slowest and this wins most."""
    df = pl.DataFrame({"c": messy_countries})

    def via_map_elements() -> None:
        def resolve(value: str) -> str | None:
            try:
                return pycountry.countries.lookup(value).alpha_2
            except LookupError:
                pass
            try:
                return pycountry.countries.search_fuzzy(value)[0].alpha_2
            except LookupError:
                return None

        df.with_columns(
            pl.col("c").map_elements(resolve, return_dtype=pl.String)
        )

    _warm_up()
    print(f"\nfuzzy lookup, {FUZZY_ROWS:,} rows:")
    baseline = _timed(
        "pycountry via map_elements", via_map_elements, FUZZY_ROWS
    )
    serial = _timed(
        "plugin (parallel=False)",
        lambda: df.with_columns(pc.alpha_2("c", fuzzy=True, parallel=False)),
        FUZZY_ROWS,
    )
    parallel = _timed(
        "plugin (parallel=True)",
        lambda: df.with_columns(pc.alpha_2("c", fuzzy=True, parallel=True)),
        FUZZY_ROWS,
    )
    _report(baseline, serial, parallel)

    assert baseline / serial > MIN_SPEEDUP_FUZZY, (
        f"plugin is only {baseline / serial:.1f}x faster than map_elements; "
        f"expected at least {MIN_SPEEDUP_FUZZY}x."
    )
