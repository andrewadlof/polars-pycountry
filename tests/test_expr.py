"""Expression-level behavior: nulls, dtypes, scoping, composition, threads.

`test_parity.py` covers *what* the matching rules compute. This file covers
how they behave as Polars expressions -- the parts a comparison against a
scalar Python function cannot see.
"""

from __future__ import annotations

import polars_country as pc
import polars_country._expr

import polars as pl
import pytest

COUNTRIES = [
    "USA",
    "united kingdom",
    "276",
    "\U0001f1eb\U0001f1f7",
    "Bolivia, Plurinational State of",
    None,
    "",
    "   ",
    "Nowhere At All",
]

# One value per resolution path, so a change to any of them shows up.
PATHS = [
    "US",  # alpha_2
    "USA",  # alpha_3
    "840",  # numeric
    "United States",  # name
    "United States of America",  # official_name
    "\U0001f1fa\U0001f1f8",  # flag
]


def test_single_field_expressions_are_nullable_utf8() -> None:
    """Every single-field expression is Utf8, null where nothing matched."""
    out = pl.DataFrame({"c": COUNTRIES}).select(
        pc.alpha_2("c").alias("a2"),
        pc.alpha_3("c").alias("a3"),
        pc.numeric("c").alias("num"),
        pc.name("c").alias("name"),
        pc.official_name("c").alias("official"),
        pc.common_name("c").alias("common"),
        pc.flag("c").alias("flag"),
    )
    assert out.dtypes == [pl.String] * 7
    assert out["a2"].to_list() == [
        "US",
        "GB",
        "DE",
        "FR",
        "BO",
        None,  # null in, null out
        None,  # blank is null, not "every country"
        None,
        None,  # no match
    ]
    assert out["num"].to_list() == [
        "840",
        "826",
        "276",
        "250",
        "068",  # zero-padded, which is why this is Utf8
        None,
        None,
        None,
        None,
    ]


def test_official_and_common_name_are_null_where_the_standard_has_none() -> (
    None
):
    """Null is a real answer here, not only a failure to match.

    Most countries have no official name distinct from their short name, and
    almost none have a common name, so these columns are mostly null by
    design.
    """
    out = pl.DataFrame({"c": ["US", "FR", "BO", "CA"]}).select(
        pc.official_name("c").alias("official"),
        pc.common_name("c").alias("common"),
    )
    assert out["official"].to_list() == [
        "United States of America",
        "French Republic",
        "Plurinational State of Bolivia",
        None,  # Canada's short name is its only name
    ]
    assert out["common"].to_list() == [None, None, "Bolivia", None]


@pytest.mark.parametrize("value", PATHS)
def test_every_indexed_field_is_a_way_in(value: str) -> None:
    """Code, number, name, official name and flag all resolve the same row."""
    got = pl.DataFrame({"c": [value]}).select(pc.alpha_2("c"))["c"][0]
    assert got == "US"


def test_extract_returns_one_struct_with_every_field() -> None:
    """`extract` is the one-pass form, with a keyword-independent schema."""
    out = pl.DataFrame({"c": ["USA", None]}).select(pc.extract("c").alias("x"))
    assert out["x"].struct.fields == [
        "alpha_2",
        "alpha_3",
        "alpha_4",
        "numeric",
        "name",
        "official_name",
        "common_name",
        "flag",
        "withdrawal_date",
        "historic",
    ]
    row = out["x"][0]
    assert row["alpha_2"] == "US"
    assert row["historic"] is False
    # ISO 3166-3 fields, absent from a current-country match.
    assert row["alpha_4"] is None
    assert row["withdrawal_date"] is None
    assert out["x"][1]["alpha_2"] is None

    # The same fields with `historic` on: a keyword must not reshape a struct.
    historic = pl.DataFrame({"c": ["USA"]}).select(
        pc.extract("c", historic=True).alias("x")
    )
    assert historic["x"].struct.fields == out["x"].struct.fields


def test_extract_populates_iso_3166_3_fields_for_a_historic_match() -> None:
    """The ISO 3166-3 fields are why they are in the schema at all."""
    row = pl.DataFrame({"c": ["YUCS"]}).select(
        pc.extract("c", historic=True).alias("x")
    )["x"][0]
    assert row["alpha_2"] == "YU"
    assert row["alpha_4"] == "YUCS"
    assert row["withdrawal_date"] == "2003-07-23"
    assert row["historic"] is True
    assert row["flag"] is None


def test_match_reports_how_each_value_resolved() -> None:
    """`matched_on` and `score` are what make a fuzzy column reviewable."""
    out = pl.DataFrame({
        "c": ["US", "USA", "840", "United States", "Texas", "Nowhere"]
    }).select(pc.match("c", fuzzy=True).alias("m"))
    assert out["m"].struct.fields == [
        "alpha_2",
        "matched_on",
        "score",
        "historic",
    ]
    assert out["m"].struct.field("matched_on").to_list() == [
        "alpha_2",
        "alpha_3",
        "numeric",
        "name",
        "fuzzy",
        None,
    ]
    scores = out["m"].struct.field("score").to_list()
    assert out["m"].struct.field("score").dtype == pl.UInt32
    # Null score means exact, which is the filter worth knowing.
    assert scores[:4] == [None, None, None, None]
    assert scores[4] is not None
    assert scores[5] is None


def test_fuzzy_and_historic_are_off_by_default() -> None:
    """Both are opt-in, so the default column is exact and current only."""
    df = pl.DataFrame({"c": ["Cote d'Ivoire", "YUCS"]})
    assert df.select(pc.alpha_2("c"))["c"].to_list() == [None, None]
    assert df.select(pc.alpha_2("c", fuzzy=True))["c"].to_list() == [
        "CI",
        None,
    ]
    assert df.select(pc.alpha_2("c", historic=True))["c"].to_list() == [
        None,
        "YU",
    ]


# ---------------------------------------------------------------------------
# Subdivisions
# ---------------------------------------------------------------------------


def test_subdivision_expressions_resolve_codes_and_names() -> None:
    """A full code, a bare code with a country, and a name all work."""
    out = pl.DataFrame({"s": ["US-CA", "GB-ENG", "JP-13"]}).select(
        pc.subdivision_code("s").alias("code"),
        pc.subdivision_name("s").alias("name"),
        pc.subdivision_type("s").alias("type"),
        pc.subdivision_country("s").alias("country"),
    )
    assert out.dtypes == [pl.String] * 4
    assert out["code"].to_list() == ["US-CA", "GB-ENG", "JP-13"]
    assert out["name"].to_list() == ["California", "England", "Tokyo"]
    assert out["type"].to_list() == ["State", "Country", "Prefecture"]
    assert out["country"].to_list() == ["US", "GB", "JP"]


def test_a_country_literal_scopes_the_search() -> None:
    """The scope is a broadcast literal, applied to every row."""
    out = pl.DataFrame({"s": ["CA", "California", "TX", "Nowhere"]}).select(
        pc.subdivision_code("s", country=pl.lit("US"))
    )
    assert out["s"].to_list() == ["US-CA", "US-CA", "US-TX", None]


def test_a_country_column_scopes_row_by_row() -> None:
    """Two rows with the same value can resolve differently.

    This is the case the scope exists for: `"CA"` is California under `US`
    and a Canadian-looking code under nothing.
    """
    out = pl.DataFrame({
        "s": ["CA", "CA", "ON", "ON"],
        "c": ["US", "Canada", "CA", "US"],
    }).select(pc.subdivision_code("s", country=pl.col("c")).alias("code"))
    assert out["code"].to_list() == ["US-CA", None, "CA-ON", None]


def test_an_unresolvable_scope_yields_null_not_a_global_search() -> None:
    """A bad country must not silently return another country's state."""
    out = pl.DataFrame({"s": ["California"]}).select(
        pc.subdivision_code("s", country=pl.lit("Atlantis")).alias("scoped"),
        pc.subdivision_code("s").alias("unscoped"),
    )
    assert out["scoped"][0] is None
    assert out["unscoped"][0] == "US-CA"


def test_subdivision_struct_carries_every_field() -> None:
    """`subdivision` is the one-pass form."""
    out = pl.DataFrame({"s": ["FR-75C", "US-CA"]}).select(
        pc.subdivision("s").alias("d")
    )
    assert out["d"].struct.fields == [
        "code",
        "name",
        "type",
        "country_code",
        "parent_code",
    ]
    # Paris sits inside Île-de-France, and the parent carries the country
    # prefix even though the raw table omits it.
    assert out["d"][0]["parent_code"] == "FR-IDF"
    assert out["d"][1]["parent_code"] is None


def test_parent_code_joins_back_against_subdivision_code() -> None:
    """The country prefix on `parent_code` is what makes the join work."""
    df = pl.DataFrame({"s": ["FR-75C"]}).with_columns(
        pc.subdivision_parent_code("s").alias("parent")
    )
    assert df.select(pc.subdivision_name("parent"))["parent"][0] == (
        "Île-de-France"
    )


# ---------------------------------------------------------------------------
# Currencies
# ---------------------------------------------------------------------------


def test_currency_expressions_resolve_codes_numbers_and_names() -> None:
    """ISO 4217, by any of its three fields."""
    out = pl.DataFrame({"c": ["USD", "978", "Yen", "nope", None]}).select(
        pc.currency_alpha_3("c").alias("code"),
        pc.currency_numeric("c").alias("num"),
        pc.currency_name("c").alias("name"),
    )
    assert out["code"].to_list() == ["USD", "EUR", "JPY", None, None]
    assert out["num"].to_list() == ["840", "978", "392", None, None]
    assert out["name"].to_list() == ["US Dollar", "Euro", "Yen", None, None]


def test_currency_struct_carries_every_field() -> None:
    """`currency` is the one-pass form."""
    out = pl.DataFrame({"c": ["GBP"]}).select(pc.currency("c").alias("x"))
    assert out["x"].struct.fields == ["alpha_3", "numeric", "name"]
    assert out["x"][0]["name"] == "Pound Sterling"


# ---------------------------------------------------------------------------
# Behaving like an expression
# ---------------------------------------------------------------------------


# The whole namespace surface, split by call shape. Listing it here rather
# than spot-checking means a method added to the module and not the namespace
# (or the reverse) fails in this file rather than in a user's pipeline.
COUNTRY_METHODS = (
    "extract",
    "match",
    "alpha_2",
    "alpha_3",
    "numeric",
    "name",
    "official_name",
    "common_name",
    "flag",
)
SUBDIVISION_METHODS = (
    "subdivision",
    "subdivision_code",
    "subdivision_name",
    "subdivision_type",
    "subdivision_country",
    "subdivision_parent_code",
)
CURRENCY_METHODS = (
    "currency",
    "currency_alpha_3",
    "currency_numeric",
    "currency_name",
)


@pytest.mark.parametrize("method", COUNTRY_METHODS + CURRENCY_METHODS)
def test_the_namespace_matches_the_module_level_functions(
    method: str,
) -> None:
    """`pl.col(...).country.*` builds the same expression as `pc.*`."""
    df = pl.DataFrame({"c": ["USA", "FR", "840", None, "nope"]})
    # `.country` is registered with Polars at import time, so a static checker
    # has no way to see it.
    bound = getattr(
        pl.col("c").country,  # ty: ignore[unresolved-attribute]
        method,
    )
    assert df.select(bound().alias("x")).equals(
        df.select(getattr(pc, method)("c").alias("x"))
    )


@pytest.mark.parametrize("method", SUBDIVISION_METHODS)
def test_the_namespace_passes_the_country_scope_through(method: str) -> None:
    """The subdivision methods take the scope positionally, like the funcs."""
    df = pl.DataFrame({"s": ["CA", "ON", None], "c": ["US", "CA", None]})
    bound = getattr(
        pl.col("s").country,  # ty: ignore[unresolved-attribute]
        method,
    )
    assert df.select(bound(pl.col("c")).alias("x")).equals(
        df.select(getattr(pc, method)("s", pl.col("c")).alias("x"))
    )


def test_every_public_expression_is_on_the_namespace() -> None:
    """The two surfaces are enumerated above; this pins them to the code."""
    listed = set(COUNTRY_METHODS + SUBDIVISION_METHODS + CURRENCY_METHODS)
    exported = set(pc._expr.__all__)
    assert exported == listed, exported.symmetric_difference(listed)
    for method in listed:
        assert hasattr(pc.CountryNamespace, method), method


def test_expressions_compose_in_filters_and_group_by() -> None:
    """Elementwise, so Polars is free to push them around."""
    df = pl.DataFrame({
        "c": ["USA", "US", "united states", "France", "FR", "nope"]
    })
    counted = (
        df
        .group_by(pc.alpha_2("c").alias("a2"))
        .len()
        .sort("a2", nulls_last=True)
    )
    assert counted.to_dicts() == [
        {"a2": "FR", "len": 2},
        {"a2": "US", "len": 3},
        {"a2": None, "len": 1},
    ]
    assert df.filter(pc.alpha_3("c") == "USA").height == 3


def test_expressions_work_in_a_lazy_plan() -> None:
    """Nothing here needs the frame materialized to build the plan."""
    lf = pl.LazyFrame({"c": ["USA", "FR"]}).with_columns(
        pc.alpha_2("c").alias("a2")
    )
    assert lf.collect_schema()["a2"] == pl.String
    assert lf.collect()["a2"].to_list() == ["US", "FR"]


def test_a_non_string_column_is_a_clear_error() -> None:
    """Numeric codes have to be strings; the error should say so."""
    with pytest.raises(pl.exceptions.PolarsError):
        pl.DataFrame({"c": [840, 276]}).select(pc.alpha_2("c"))


def test_an_integer_column_resolves_once_it_is_zero_padded() -> None:
    """The documented way in from a numeric column."""
    out = pl.DataFrame({"n": [840, 276, 4]}).select(
        pc.alpha_2(pl.col("n").cast(pl.String).str.zfill(3))
    )
    assert out["n"].to_list() == ["US", "DE", "AF"]


@pytest.mark.parametrize("fuzzy", (False, True))
def test_parallel_and_serial_paths_agree(fuzzy: bool) -> None:
    """Above 100k rows the column is split across threads; results must match.

    The fan-out hands each thread a contiguous slice and concatenates the
    pieces back in order, so a mistake here shows up as rows landing in the
    wrong place rather than as wrong values.
    """
    values = ["USA", "FR", "276", "Cote d'Ivoire", None, "", "nope"]
    df = pl.DataFrame({"c": values * 20_000})
    assert df.height > 100_000

    serial = df.select(pc.extract("c", fuzzy=fuzzy, parallel=False))
    parallel = df.select(pc.extract("c", fuzzy=fuzzy, parallel=True))
    assert serial.equals(parallel)
    # And the split did not shuffle anything: row i still answers value i.
    assert parallel["c"][:7].to_list() == serial["c"][:7].to_list()


def test_the_scope_column_survives_the_parallel_split() -> None:
    """A per-row country column has to be sliced alongside the values."""
    df = pl.DataFrame({
        "s": ["CA", "ON", "CA"] * 40_000,
        "c": ["US", "CA", "Canada"] * 40_000,
    })
    assert df.height > 100_000

    serial = df.select(
        pc.subdivision_code("s", country=pl.col("c"), parallel=False)
    )
    parallel = df.select(
        pc.subdivision_code("s", country=pl.col("c"), parallel=True)
    )
    assert serial.equals(parallel)
    assert parallel["s"][:3].to_list() == ["US-CA", "CA-ON", None]


def test_a_broadcast_scope_survives_the_parallel_split() -> None:
    """A literal country is length 1 and must not be sliced with the values."""
    df = pl.DataFrame({"s": ["CA", "TX", "NY"] * 40_000})
    assert df.height > 100_000

    out = df.select(
        pc.subdivision_code("s", country=pl.lit("US"), parallel=True)
    )
    assert out["s"][:3].to_list() == ["US-CA", "US-TX", "US-NY"]
    assert out["s"].null_count() == 0
