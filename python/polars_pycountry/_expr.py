"""Polars expression wrappers around the compiled plugin.

Every function here is a thin `register_plugin_function` call. The real work
happens in the Rust cdylib that sits next to this file; `plugin_path` points at
the package directory and Polars locates the shared object inside it.

All expressions are elementwise, so Polars is free to reorder, slice, and
parallelize around them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
from polars.plugins import register_plugin_function

if TYPE_CHECKING:
    from polars._typing import IntoExprColumn

# The compiled plugin lives alongside this module.
_PLUGIN_PATH = Path(__file__).parent

__all__ = [
    "alpha_2",
    "alpha_3",
    "common_name",
    "currency",
    "currency_alpha_3",
    "currency_name",
    "currency_numeric",
    "extract",
    "flag",
    "match",
    "name",
    "numeric",
    "official_name",
    "subdivision",
    "subdivision_code",
    "subdivision_country",
    "subdivision_name",
    "subdivision_parent_code",
    "subdivision_type",
]


def _country_plugin(
    function_name: str,
    expr: IntoExprColumn,
    *,
    fuzzy: bool,
    historic: bool,
    parallel: bool,
) -> pl.Expr:
    """Register one of the plugin's country expressions.

    Parameters
    ----------
    function_name : str
        Symbol exported by the Rust cdylib.
    expr : IntoExprColumn
        String column of country names or codes.
    fuzzy : bool
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool
        Whether ISO 3166-3 is consulted after ISO 3166-1.
    parallel : bool
        Whether the plugin may fan the column out across rayon threads.

    Returns
    -------
    pl.Expr
        The registered expression.
    """
    return register_plugin_function(
        plugin_path=_PLUGIN_PATH,
        function_name=function_name,
        args=[expr],
        kwargs={"fuzzy": fuzzy, "historic": historic, "parallel": parallel},
        is_elementwise=True,
    )


def _subdivision_plugin(
    function_name: str,
    expr: IntoExprColumn,
    country: IntoExprColumn | None,
    *,
    fuzzy: bool,
    parallel: bool,
) -> pl.Expr:
    """Register one of the plugin's subdivision expressions.

    Parameters
    ----------
    function_name : str
        Symbol exported by the Rust cdylib.
    expr : IntoExprColumn
        String column of subdivision codes or names.
    country : IntoExprColumn | None
        Expression scoping the search to one country, or `None`.
    fuzzy : bool
        Whether the search may fall back to accent-folded and partial matches.
    parallel : bool
        Whether the plugin may fan the column out across rayon threads.

    Returns
    -------
    pl.Expr
        The registered expression.
    """
    # The Rust side always takes two arguments, so an absent country becomes a
    # null literal rather than a shorter argument list. Polars broadcasts the
    # length-1 literal, and the plugin reads it once per row.
    scope = pl.lit(None, dtype=pl.String) if country is None else country
    return register_plugin_function(
        plugin_path=_PLUGIN_PATH,
        function_name=function_name,
        args=[expr, scope],
        kwargs={"fuzzy": fuzzy, "parallel": parallel},
        is_elementwise=True,
    )


def _currency_plugin(
    function_name: str, expr: IntoExprColumn, *, parallel: bool
) -> pl.Expr:
    """Register one of the plugin's currency expressions.

    Parameters
    ----------
    function_name : str
        Symbol exported by the Rust cdylib.
    expr : IntoExprColumn
        String column of currency codes, numbers, or names.
    parallel : bool
        Whether the plugin may fan the column out across rayon threads.

    Returns
    -------
    pl.Expr
        The registered expression.
    """
    return register_plugin_function(
        plugin_path=_PLUGIN_PATH,
        function_name=function_name,
        args=[expr],
        kwargs={"parallel": parallel},
        is_elementwise=True,
    )


# ---------------------------------------------------------------------------
# Countries
# ---------------------------------------------------------------------------


def extract(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Resolve a country and return every ISO field as a struct.

    One pass produces all of them, so this is cheaper than calling several
    single-field expressions on the same column.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to `pycountry`'s `search_fuzzy`,
        which also reaches countries through their subdivisions -- `"Texas"`
        resolves to `US`. Off by default because it is a guess, and a wrong
        guess is harder to notice than a null.
    historic : bool, default False
        Whether ISO 3166-3 (countries withdrawn from the standard) is
        consulted when ISO 3166-1 has no match. Current countries always win,
        so a reused code like `AI` still resolves to Anguilla.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.
        Columns below 100k rows run single-threaded regardless.

    Returns
    -------
    pl.Expr
        Struct of `alpha_2`, `alpha_3`, `alpha_4`, `numeric`, `name`,
        `official_name`, `common_name`, `flag`, `withdrawal_date` and
        `historic`. Every field is null when nothing matched. `alpha_4` and
        `withdrawal_date` exist only in ISO 3166-3, so they are populated only
        for a historic match; they are present in the schema either way, so a
        keyword never changes the struct's shape.
    """
    return _country_plugin(
        "country_extract",
        expr,
        fuzzy=fuzzy,
        historic=historic,
        parallel=parallel,
    )


def match(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Report *how* each value resolved, not just what it resolved to.

    This is the expression to reach for when auditing a messy column: it says
    which field matched, whether the answer came from the fuzzy search, and
    how confident that search was.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool, default False
        Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Struct of `alpha_2`, `matched_on`, `score` and `historic`.
        `matched_on` names the ISO field that matched -- `"alpha_2"`,
        `"name"`, `"numeric"` and so on -- or `"fuzzy"` when the fuzzy search
        produced the answer. `score` carries the fuzzy points and is null for
        an exact match, so `score.is_null()` separates the answers you can
        trust from the ones worth reviewing.
    """
    return _country_plugin(
        "country_match",
        expr,
        fuzzy=fuzzy,
        historic=historic,
        parallel=parallel,
    )


def alpha_2(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the ISO 3166-1 alpha-2 code, e.g. `"US"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool, default False
        Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of alpha-2 codes, null where nothing matched.
    """
    return _country_plugin(
        "country_alpha_2",
        expr,
        fuzzy=fuzzy,
        historic=historic,
        parallel=parallel,
    )


def alpha_3(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the ISO 3166-1 alpha-3 code, e.g. `"USA"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool, default False
        Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of alpha-3 codes, null where nothing matched.
    """
    return _country_plugin(
        "country_alpha_3",
        expr,
        fuzzy=fuzzy,
        historic=historic,
        parallel=parallel,
    )


def numeric(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the ISO 3166-1 numeric code, e.g. `"840"`.

    The result is Utf8 rather than an integer because the standard's codes are
    three digits wide and `"004"` is not `"4"`. Cast it yourself if you want
    the number; going the other way, an integer column needs
    `pl.col("n").cast(pl.String).str.zfill(3)` before it will match.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool, default False
        Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of zero-padded numeric codes, null where nothing matched.
    """
    return _country_plugin(
        "country_numeric",
        expr,
        fuzzy=fuzzy,
        historic=historic,
        parallel=parallel,
    )


def name(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the country's short name, e.g. `"United States"`.

    This is the standard's `name` field, which is the one to group or join on.
    It is not always the name people use: the short name of `BO` is
    `"Bolivia, Plurinational State of"`, and `common_name` holds `"Bolivia"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool, default False
        Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of short names, null where nothing matched.
    """
    return _country_plugin(
        "country_name", expr, fuzzy=fuzzy, historic=historic, parallel=parallel
    )


def official_name(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the official name, e.g. `"United States of America"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool, default False
        Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column, **null** where the country has no official name distinct
        from its short name -- most do not.
    """
    return _country_plugin(
        "country_official_name",
        expr,
        fuzzy=fuzzy,
        historic=historic,
        parallel=parallel,
    )


def common_name(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the common name, e.g. `"Bolivia"` for `BO`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool, default False
        Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column, **null** for the great majority of countries, whose
        common name is their short name. `pl.coalesce(common_name(c), name(c))`
        is the usual way to get a display name.
    """
    return _country_plugin(
        "country_common_name",
        expr,
        fuzzy=fuzzy,
        historic=historic,
        parallel=parallel,
    )


def flag(
    expr: IntoExprColumn,
    *,
    fuzzy: bool = False,
    historic: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the flag emoji, e.g. `"\U0001f1fa\U0001f1f8"` for `US`.

    The emoji is a pair of regional indicator symbols, which is also a value
    the lookup accepts as *input*.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of country names, codes, or flag emoji.
    fuzzy : bool, default False
        Whether an exact miss may fall back to the fuzzy search.
    historic : bool, default False
        Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of flag emoji, null where nothing matched. Withdrawn
        countries have no flag, so a historic match is null here.
    """
    return _country_plugin(
        "country_flag", expr, fuzzy=fuzzy, historic=historic, parallel=parallel
    )


# ---------------------------------------------------------------------------
# Subdivisions
# ---------------------------------------------------------------------------


def subdivision(
    expr: IntoExprColumn,
    country: IntoExprColumn | None = None,
    *,
    fuzzy: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Resolve an ISO 3166-2 subdivision and return every field as a struct.

    Pass `country` whenever you have it. Subdivision names are not unique
    across the standard -- `"Central"` names a region in a dozen countries,
    and `"Georgia"` is both a US state and a country -- so an unscoped name
    resolves to whichever record ISO lists first. With a country the answer is
    well defined.

    The value may be a full code (`"US-CA"`), a bare code when a country is
    given (`"CA"`), or a name (`"California"`).

    Parameters
    ----------
    expr : IntoExprColumn
        String column of subdivision codes or names.
    country : IntoExprColumn | None, optional
        The country to search within. Follows the usual Polars convention, so
        a bare `str` names a **column**; a fixed country is `pl.lit("US")`.
        Whichever form, the value may be anything the country lookup accepts
        -- `"US"`, `"USA"`, `"840"`, `"United States"`. A country that does
        not itself resolve yields null rather than falling back to a global
        search, so a bad scope cannot quietly return another country's state.
    fuzzy : bool, default False
        Whether to fall back to accent-folded and then partial name matches.
        The country scope is resolved with the same setting.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Struct of `code`, `name`, `type`, `country_code` and `parent_code`.
        Every field is null when nothing matched; `parent_code` is null for
        top-level subdivisions.
    """
    return _subdivision_plugin(
        "subdivision_extract", expr, country, fuzzy=fuzzy, parallel=parallel
    )


def subdivision_code(
    expr: IntoExprColumn,
    country: IntoExprColumn | None = None,
    *,
    fuzzy: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the ISO 3166-2 code, e.g. `"US-CA"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of subdivision codes or names.
    country : IntoExprColumn | None, optional
        The country to scope the search to. A bare `str` names a column, per
        the usual Polars convention; use `pl.lit("US")` for a fixed country.
    fuzzy : bool, default False
        Whether to fall back to accent-folded and partial name matches.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of subdivision codes, null where nothing matched.
    """
    return _subdivision_plugin(
        "subdivision_code", expr, country, fuzzy=fuzzy, parallel=parallel
    )


def subdivision_name(
    expr: IntoExprColumn,
    country: IntoExprColumn | None = None,
    *,
    fuzzy: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the subdivision's name, e.g. `"California"`.

    This is the standard's spelling, which is how it normalizes a messy
    column: `"CA"`, `"calif."` and `"California"` all land on one string.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of subdivision codes or names.
    country : IntoExprColumn | None, optional
        The country to scope the search to. A bare `str` names a column, per
        the usual Polars convention; use `pl.lit("US")` for a fixed country.
    fuzzy : bool, default False
        Whether to fall back to accent-folded and partial name matches.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of subdivision names, null where nothing matched.
    """
    return _subdivision_plugin(
        "subdivision_name", expr, country, fuzzy=fuzzy, parallel=parallel
    )


def subdivision_type(
    expr: IntoExprColumn,
    country: IntoExprColumn | None = None,
    *,
    fuzzy: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the subdivision's type, e.g. `"State"` or `"Province"`.

    The standard uses 100-odd distinct types, and they are English words
    chosen per country rather than a closed vocabulary -- `"Province"`,
    `"Autonomous province"` and `"Overseas territorial collectivity"` are all
    in there.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of subdivision codes or names.
    country : IntoExprColumn | None, optional
        The country to scope the search to. A bare `str` names a column, per
        the usual Polars convention; use `pl.lit("US")` for a fixed country.
    fuzzy : bool, default False
        Whether to fall back to accent-folded and partial name matches.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of subdivision types, null where nothing matched.
    """
    return _subdivision_plugin(
        "subdivision_type", expr, country, fuzzy=fuzzy, parallel=parallel
    )


def subdivision_country(
    expr: IntoExprColumn,
    country: IntoExprColumn | None = None,
    *,
    fuzzy: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the alpha-2 code of the country a subdivision belongs to.

    Useful without a `country` scope: it answers "which country is this state
    in?" for a column that holds only subdivision codes.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of subdivision codes or names.
    country : IntoExprColumn | None, optional
        The country to scope the search to. A bare `str` names a column, per
        the usual Polars convention; use `pl.lit("US")` for a fixed country.
    fuzzy : bool, default False
        Whether to fall back to accent-folded and partial name matches.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of alpha-2 codes, null where nothing matched.
    """
    return _subdivision_plugin(
        "subdivision_country", expr, country, fuzzy=fuzzy, parallel=parallel
    )


def subdivision_parent_code(
    expr: IntoExprColumn,
    country: IntoExprColumn | None = None,
    *,
    fuzzy: bool = False,
    parallel: bool = True,
) -> pl.Expr:
    """Extract the parent subdivision's code, where there is one.

    Subdivisions nest: a French department sits inside a region, and both are
    ISO 3166-2 records. The parent code carries the country prefix even where
    the standard's raw data omits it, so it can be joined straight back
    against `subdivision_code`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of subdivision codes or names.
    country : IntoExprColumn | None, optional
        The country to scope the search to. A bare `str` names a column, per
        the usual Polars convention; use `pl.lit("US")` for a fixed country.
    fuzzy : bool, default False
        Whether to fall back to accent-folded and partial name matches.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of parent codes, **null** for a top-level subdivision as
        well as for no match.
    """
    return _subdivision_plugin(
        "subdivision_parent_code",
        expr,
        country,
        fuzzy=fuzzy,
        parallel=parallel,
    )


# ---------------------------------------------------------------------------
# Currencies
# ---------------------------------------------------------------------------


def currency(expr: IntoExprColumn, *, parallel: bool = True) -> pl.Expr:
    """Resolve an ISO 4217 currency and return every field as a struct.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of currency codes, numbers, or names.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Struct of `alpha_3`, `numeric` and `name`, all null when nothing
        matched.
    """
    return _currency_plugin("currency_extract", expr, parallel=parallel)


def currency_alpha_3(
    expr: IntoExprColumn, *, parallel: bool = True
) -> pl.Expr:
    """Extract the ISO 4217 alpha-3 code, e.g. `"USD"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of currency codes, numbers, or names.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of currency codes, null where nothing matched.
    """
    return _currency_plugin("currency_alpha_3", expr, parallel=parallel)


def currency_numeric(
    expr: IntoExprColumn, *, parallel: bool = True
) -> pl.Expr:
    """Extract the ISO 4217 numeric code, e.g. `"840"`.

    Utf8 for the same reason as the country code: the standard's codes are
    three digits wide and the leading zero is part of the value.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of currency codes, numbers, or names.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of zero-padded numeric codes, null where nothing matched.
    """
    return _currency_plugin("currency_numeric", expr, parallel=parallel)


def currency_name(expr: IntoExprColumn, *, parallel: bool = True) -> pl.Expr:
    """Extract the currency's name, e.g. `"US Dollar"`.

    Parameters
    ----------
    expr : IntoExprColumn
        String column of currency codes, numbers, or names.
    parallel : bool, default True
        Whether the plugin may split large columns across rayon threads.

    Returns
    -------
    pl.Expr
        Utf8 column of currency names, null where nothing matched.
    """
    return _currency_plugin("currency_name", expr, parallel=parallel)
