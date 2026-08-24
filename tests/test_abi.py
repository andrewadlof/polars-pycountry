"""The plugin FFI ABI this build was compiled against.

`pyproject.toml` declares an open-ended `polars>=1.37.1`, which is only a safe
claim while Polars keeps the plugin FFI ABI at `(0, 1)`. Nothing else in the
suite asserts that directly: the expression tests would fail if the ABI moved,
but they would fail as a wall of unrelated-looking errors rather than as one
sentence naming the cause.

So this file reads the version straight out of the compiled extension, the way
Polars itself does -- `_polars_plugin_get_version` is the `#[no_mangle] extern
"C"` symbol `pyo3-polars` emits, and Polars calls it before dispatching to any
plugin expression. A failure here means the pin comments in `Cargo.toml` and
`pyproject.toml` have gone stale and the floor in `[project.dependencies]`
needs an upper bound.
"""

from __future__ import annotations

import polars_country as pc
from polars_country import _internal

import ctypes

import polars as pl

#: What `pyo3-polars 0.27` emits, and what py-polars has expected since 1.37.
EXPECTED_ABI = (0, 1)


def plugin_abi_version() -> tuple[int, int]:
    """Read the FFI ABI version out of the compiled extension.

    `pyo3-polars` packs the version into a `u32` as `major << 16 | minor`,
    which is the encoding Polars decodes on the other side.

    Returns
    -------
    tuple[int, int]
        The `(major, minor)` ABI version the extension reports.
    """
    library = ctypes.CDLL(_internal.__file__)
    symbol = library._polars_plugin_get_version
    symbol.argtypes = []
    symbol.restype = ctypes.c_uint32
    packed = symbol()
    return (packed >> 16, packed & 0xFFFF)


def test_the_compiled_plugin_still_speaks_the_abi_polars_expects() -> None:
    """Guard the open-ended `polars>=1.37.1` floor.

    If this fails, Polars has bumped the plugin ABI. The compiled wheel cannot
    be loaded by the newer Polars, so the dependency needs an upper bound and
    a rebuild against the matching `pyo3-polars`.
    """
    found = plugin_abi_version()
    assert found == EXPECTED_ABI, (
        f"plugin FFI ABI is {found}, not {EXPECTED_ABI}: the open-ended "
        f"`polars>=1.37.1` floor in pyproject.toml no longer holds. Rebuild "
        f"against a matching pyo3-polars and cap the dependency. "
        f"Running against py-polars {pl.__version__}."
    )


def test_the_installed_polars_dispatches_to_the_plugin() -> None:
    """The ABI agreeing is necessary, not sufficient.

    Reading the symbol proves what this build exports; it says nothing about
    whether the Polars actually installed will load it. One expression through
    the real dispatch path closes that gap, so an ABI check that passes while
    the plugin is unusable cannot happen quietly.
    """
    resolved = pl.DataFrame({"c": ["USA", "united kingdom", None]}).select(
        pc.alpha_2("c")
    )
    assert resolved["c"].to_list() == ["US", "GB", None]
