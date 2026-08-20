"""Agreement with `pycountry`, over everything the standard contains.

This is the load-bearing suite. `test_expr.py` covers how the expressions
behave as Polars expressions; this file covers whether they compute the same
answers the reference does.

The corpora are derived from the tables themselves rather than hand-written,
so they cannot go stale: refreshing the ISO data re-derives every case. Both
sides read the same files -- `conftest.reference` repoints `pycountry` at the
vendored copies -- so a disagreement can only be an algorithm difference.

Two divergences are deliberate and are asserted here as such rather than
skipped, so that they stay deliberate:

* blank input resolves to null instead of matching every country,
* fuzzy search covers countries and subdivisions only, as in `pycountry`.
"""

from __future__ import annotations

import polars_country as pc
from conftest import must

import pycountry
import pytest

# Enough hand-written mess to catch what a value-sweep cannot: the shapes real
# columns arrive in.
MESSY = (
    "  United States  ",
    "UNITED STATES",
    "united states",
    "u.s.a.",
    "USA",
    "Cote d'Ivoire",
    "Côte d'Ivoire",
    "COTE D'IVOIRE",
    "Aland Islands",
    "Åland Islands",
    "Republic of Korea",
    "Korea, Republic of",
    "Texas",
    "Bavaria",
    "Bayern",
    "England",
    "Holland",
    "The Netherlands",
    "Czech Republic",
    "Czechia",
    "Burma",
    "Myanmar",
    "Swaziland",
    "Eswatini",
    "Macedonia",
    "Congo",
    "guinea",
    "new",
    "island",
    "Nowhere At All",
    "12345",
    "004",
    "4",
)


def _reference_lookup(value: str) -> str | None:
    """Resolve with `pycountry`, returning the alpha-2 code or `None`.

    Parameters
    ----------
    value : str
        The string to resolve.

    Returns
    -------
    str | None
        The matched country's alpha-2 code, or `None` for a `LookupError`.
    """
    try:
        return pycountry.countries.lookup(value).alpha_2
    except LookupError:
        return None


def _reference_fuzzy(value: str) -> str | None:
    """Resolve with the reference's exact-then-fuzzy order.

    Mirrors what `fuzzy=True` promises: an exact hit wins, and only a miss
    falls through to `search_fuzzy`.

    Parameters
    ----------
    value : str
        The string to resolve.

    Returns
    -------
    str | None
        The matched country's alpha-2 code, or `None` when neither matched.
    """
    exact = _reference_lookup(value)
    if exact is not None:
        return exact
    try:
        return pycountry.countries.search_fuzzy(value)[0].alpha_2
    except LookupError:
        return None


# ---------------------------------------------------------------------------
# Exact lookup: every value the tables contain
# ---------------------------------------------------------------------------


def test_exact_lookup_agrees_on_every_indexed_country_value(
    all_country_values: list[str],
) -> None:
    """Sweep every alpha-2, alpha-3, number, name and flag in ISO 3166-1.

    If the two agree on every string the table holds, they agree on every
    exact lookup that can succeed -- which is the whole space, since an exact
    lookup can only succeed on a value that is in the table.
    """
    assert len(all_country_values) > 700, "the sweep should be substantial"

    got = [pc.lookup_country(v) for v in all_country_values]
    mine = [None if g is None else g["alpha_2"] for g in got]
    theirs = [_reference_lookup(v) for v in all_country_values]

    mismatches = [
        (v, m, t)
        for v, m, t in zip(all_country_values, mine, theirs, strict=True)
        if m != t
    ]
    assert not mismatches, mismatches[:10]
    # A sweep that resolved nothing would pass the comparison vacuously.
    assert all(t is not None for t in theirs)


def test_exact_lookup_agrees_under_case_folding(
    all_country_values: list[str],
) -> None:
    """The indices are case-insensitive, so every case spelling must agree."""
    cases = [
        cased
        for value in all_country_values
        for cased in (value.upper(), value.lower())
    ]
    mismatches = [
        (v, mine, theirs)
        for v in cases
        if (
            mine := (
                None if (r := pc.lookup_country(v)) is None else r["alpha_2"]
            )
        )
        != (theirs := _reference_lookup(v))
    ]
    assert not mismatches, mismatches[:10]


def test_exact_lookup_reports_the_same_record_not_just_the_same_code(
    all_country_values: list[str],
) -> None:
    """Every field of the resolved record matches the reference's."""
    for value in all_country_values:
        mine = pc.lookup_country(value)
        theirs = pycountry.countries.lookup(value)
        assert mine is not None, value
        for field, expected in theirs._fields.items():
            assert mine[field] == expected, (value, field)


def test_matched_on_names_the_field_the_reference_would_have_used(
    all_country_values: list[str],
) -> None:
    """`matched_on` is the real index that hit, not a guess after the fact.

    The reference walks its field indices in insertion order and takes the
    first hit, so the field it *would* report is recoverable: the first field
    whose lowercased value equals the query.
    """
    order = list(pycountry.countries.indices)
    for value in all_country_values:
        expected = next(
            field
            for field in order
            if value.lower() in pycountry.countries.indices[field]
        )
        assert must(pc.lookup_country(value), value)["matched_on"] == expected


# ---------------------------------------------------------------------------
# Subdivisions
# ---------------------------------------------------------------------------


def test_every_subdivision_code_resolves_to_the_reference_record(
    all_subdivision_codes: list[str],
) -> None:
    """All ~5,000 ISO 3166-2 codes, checked field by field."""
    assert len(all_subdivision_codes) > 4_000

    for code in all_subdivision_codes:
        mine = pc.lookup_subdivision(code)
        theirs = pycountry.subdivisions.get(code=code)
        assert mine is not None, code
        assert mine["code"] == theirs.code
        assert mine["name"] == theirs.name
        assert mine["type"] == theirs.type
        assert mine["country_code"] == theirs.country_code
        assert mine["parent_code"] == theirs.parent_code


def test_subdivision_codes_resolve_case_insensitively(
    all_subdivision_codes: list[str],
) -> None:
    """`"us-ca"` and `"US-CA"` are the same code, as they are upstream."""
    for code in all_subdivision_codes:
        assert must(pc.lookup_subdivision(code.lower()), code)["code"] == code
        assert must(pc.lookup_subdivision(code.upper()), code)["code"] == code


def test_bare_codes_resolve_within_their_country(
    all_subdivision_codes: list[str],
) -> None:
    """`"CA"` scoped to `"US"` is `US-CA`, which is the everyday shape."""
    for code in all_subdivision_codes:
        country, _, bare = code.partition("-")
        got = pc.lookup_subdivision(bare, country=country)
        assert got is not None, code
        assert got["code"] == code


def test_subdivision_names_resolve_within_their_country(
    all_subdivision_codes: list[str],
) -> None:
    """A name plus a country lands on a subdivision of that country.

    Not necessarily *the* one it was taken from: some countries list the same
    name twice under different codes, and the first listing wins. The invariant
    that matters is that the answer is in the right country and carries the
    right name.
    """
    for code in all_subdivision_codes:
        reference = pycountry.subdivisions.get(code=code)
        got = pc.lookup_subdivision(
            reference.name, country=reference.country_code
        )
        assert got is not None, code
        assert got["country_code"] == reference.country_code, code
        assert got["name"] == reference.name, code


# ---------------------------------------------------------------------------
# Currencies
# ---------------------------------------------------------------------------


def test_every_currency_value_resolves_to_the_reference_record() -> None:
    """Codes, numbers and names across ISO 4217."""
    values = [
        value
        for currency in pycountry.currencies
        for value in currency._fields.values()
    ]
    assert len(values) > 400

    for value in values:
        mine = pc.lookup_currency(value)
        theirs = pycountry.currencies.lookup(value)
        assert mine is not None, value
        assert mine["alpha_3"] == theirs.alpha_3, value
        assert mine["numeric"] == theirs.numeric, value
        assert mine["name"] == theirs.name, value


# ---------------------------------------------------------------------------
# Historic countries
# ---------------------------------------------------------------------------


def _reference_historic(value: str) -> tuple[str | None, bool]:
    """Resolve with the reference's current-then-withdrawn order.

    Parameters
    ----------
    value : str
        The string to resolve.

    Returns
    -------
    tuple[str | None, bool]
        The matched alpha-2 code (or `None`), and whether it came from ISO
        3166-3 rather than ISO 3166-1.
    """
    current = _reference_lookup(value)
    if current is not None:
        return current, False
    try:
        return pycountry.historic_countries.lookup(value).alpha_2, True
    except LookupError:
        return None, False


def test_historic_lookup_agrees_across_iso_3166_3() -> None:
    """Every value in the withdrawn-country table, with `historic=True`.

    Not every value resolves to the record it was taken from, and that is the
    reference's behavior rather than a rounding error. ISO 3166-3 reuses field
    values -- eight countries were withdrawn in 1977, `CS` was assigned twice
    -- and `pycountry` builds its indices last-write-wins, so `"1977"` answers
    with the last record carrying it. Comparing against the reference rather
    than against the record is what makes the sweep meaningful.
    """
    values = [
        value
        for record in pycountry.historic_countries
        for value in record._fields.values()
    ]
    assert len(values) > 150

    for value in values:
        mine = pc.lookup_country(value, historic=True)
        expected_code, expected_historic = _reference_historic(value)
        assert mine is not None, value
        assert mine["alpha_2"] == expected_code, value
        assert mine["historic"] is expected_historic, value


def test_a_reissued_code_resolves_to_the_country_that_has_it_now() -> None:
    """`AI` was French Afars and Issas until 1977 and is Anguilla today."""
    got = must(pc.lookup_country("AI", historic=True))
    assert got["alpha_2"] == "AI"
    assert got["name"] == "Anguilla"
    assert got["historic"] is False


def test_historic_is_off_by_default() -> None:
    """A withdrawn-only value is null unless `historic` is asked for."""
    assert pc.lookup_country("YUCS") is None
    assert must(pc.lookup_country("YUCS", historic=True))["alpha_2"] == "YU"


# ---------------------------------------------------------------------------
# Fuzzy search
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", MESSY)
def test_fuzzy_agrees_with_the_reference_on_messy_input(value: str) -> None:
    """Hand-written mess: the shapes real columns actually arrive in."""
    got = pc.lookup_country(value, fuzzy=True)
    mine = None if got is None else got["alpha_2"]
    assert mine == _reference_fuzzy(value), value


def test_fuzzy_agrees_on_every_country_name() -> None:
    """Every name and official name in ISO 3166-1, through the fuzzy path."""
    values = [
        value
        for country in pycountry.countries
        for key, value in country._fields.items()
        if key in {"name", "official_name", "common_name"}
    ]
    mismatches = [
        (v, mine, theirs)
        for v in values
        if (
            mine := (
                None
                if (r := pc.lookup_country(v, fuzzy=True)) is None
                else r["alpha_2"]
            )
        )
        != (theirs := _reference_fuzzy(v))
    ]
    assert not mismatches, mismatches[:10]


def test_fuzzy_agrees_on_every_subdivision_name(
    all_subdivision_codes: list[str],
) -> None:
    """Every subdivision name, resolved up to a country.

    This is the pass that scores subdivisions, and the one most likely to
    drift: it walks every field of every subdivision, including the type and
    the country code, and the multiplicity of the hits is part of the score.
    """
    names = sorted({
        pycountry.subdivisions.get(code=code).name
        for code in all_subdivision_codes
    })
    mismatches = [
        (v, mine, theirs)
        for v in names
        if (
            mine := (
                None
                if (r := pc.lookup_country(v, fuzzy=True)) is None
                else r["alpha_2"]
            )
        )
        != (theirs := _reference_fuzzy(v))
    ]
    assert not mismatches, mismatches[:10]


@pytest.mark.parametrize(
    "value", ("guinea", "island", "new", "united", "republic", "us", "state")
)
def test_fuzzy_ranking_agrees_in_full(value: str) -> None:
    """Not just the winner: the whole ranked list, in order.

    The scores are a sum over four passes, so two implementations can agree on
    the top result while disagreeing about everything below it. Comparing the
    full ranking is what catches that.
    """
    mine = [r["alpha_2"] for r in pc.search_countries(value)]
    theirs = [c.alpha_2 for c in pycountry.countries.search_fuzzy(value)]
    assert mine == theirs, value


def test_fuzzy_is_off_by_default() -> None:
    """Accent folding is a fuzzy behavior, so exact lookup must not do it."""
    assert pc.lookup_country("Cote d'Ivoire") is None
    got = must(pc.lookup_country("Cote d'Ivoire", fuzzy=True))
    assert got["alpha_2"] == "CI"


# ---------------------------------------------------------------------------
# The deliberate divergences
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ("", "   ", "\t\n"))
def test_blank_input_is_null_here_and_everything_there(blank: str) -> None:
    """The one divergence in matching, asserted rather than assumed.

    `search_fuzzy("")` matches every country -- the empty string is a
    substring of every name, at offset 0 -- and ranks Andorra first. In a
    DataFrame that would turn every blank cell into a confident wrong answer.
    """
    assert pc.lookup_country(blank, fuzzy=True) is None
    assert pc.search_countries(blank) == []

    if not blank.strip():
        reference = pycountry.countries.search_fuzzy(blank)
        assert len(reference) == len(pycountry.countries), (
            "the reference really does match everything; if this stops being "
            "true, the divergence documented above can be dropped"
        )
