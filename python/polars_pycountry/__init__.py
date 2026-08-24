"""Vectorized, `pycountry`-compatible ISO code lookup for Polars.

A native Polars expression plugin, written in Rust against the same
`iso-codes` tables `pycountry` ships, that reproduces its lookup rules exactly
-- so resolving countries, subdivisions and currencies runs at native speed
instead of row-by-row through `Expr.map_elements`.

Examples
--------
>>> import polars as pl
>>> import polars_pycountry as pc
>>> df = pl.DataFrame({"c": ["USA", "united kingdom", "276", None]})
>>> df.with_columns(pc.alpha_2("c"))  # doctest: +SKIP
>>> df.with_columns(pl.col("c").country.name())  # doctest: +SKIP

The `.country` expression namespace is registered on import.
"""

from __future__ import annotations

from polars_pycountry._data import (
    ISO_CODES_URL,
    TABLE_FILES,
    load_iso_data,
    refresh_iso_data,
)
from polars_pycountry._expr import (
    alpha_2,
    alpha_3,
    common_name,
    currency,
    currency_alpha_3,
    currency_name,
    currency_numeric,
    extract,
    flag,
    match,
    name,
    numeric,
    official_name,
    subdivision,
    subdivision_code,
    subdivision_country,
    subdivision_name,
    subdivision_parent_code,
    subdivision_type,
)
from polars_pycountry._internal import (
    DATA_PATH_ENV,
    iso_version,
    lookup_country,
    lookup_currency,
    lookup_subdivision,
    search_countries,
    table_sizes,
)
from polars_pycountry._namespace import CountryNamespace

__all__ = [
    "DATA_PATH_ENV",
    "ISO_CODES_URL",
    "TABLE_FILES",
    "CountryNamespace",
    "alpha_2",
    "alpha_3",
    "common_name",
    "currency",
    "currency_alpha_3",
    "currency_name",
    "currency_numeric",
    "extract",
    "flag",
    "iso_version",
    "load_iso_data",
    "lookup_country",
    "lookup_currency",
    "lookup_subdivision",
    "match",
    "name",
    "numeric",
    "official_name",
    "refresh_iso_data",
    "search_countries",
    "subdivision",
    "subdivision_code",
    "subdivision_country",
    "subdivision_name",
    "subdivision_parent_code",
    "subdivision_type",
    "table_sizes",
]
