"""An interactive tour of polars-country.

Run it with `just notebook` (editable) or `just notebook-run` (read-only).
Every cell is reactive: change a widget and everything downstream recomputes.
"""

import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium", app_title="polars-country")


@app.cell(hide_code=True)
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _():
    import random
    import time

    import polars as pl
    import pycountry

    import polars_country as pc

    return pc, pl, pycountry, random, time


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    # polars-country

    ISO 3166 and ISO 4217 lookup as native Polars expressions, built to return
    exactly what [`pycountry`](https://github.com/pycountry/pycountry) returns.

    This notebook is a working tour: every table below is computed live, and
    the widgets re-run the cells under them.
    """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""## Is it installed, and what data is it using?""")
    return


@app.cell
def _(pc, pl):
    _sizes = pc.table_sizes()
    environment = pl.DataFrame(
        {
            "what": [
                "polars",
                "iso-codes release",
                "ISO 3166-1 (countries)",
                "ISO 3166-2 (subdivisions)",
                "ISO 3166-3 (withdrawn)",
                "ISO 4217 (currencies)",
            ],
            "value": [
                pl.__version__,
                pc.iso_version(),
                f"{_sizes['3166-1']:,} records",
                f"{_sizes['3166-2']:,} records",
                f"{_sizes['3166-3']:,} records",
                f"{_sizes['4217']:,} records",
            ],
        }
    )
    environment
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ## The problem

    A country column from the real world is not a clean key. Every row below
    names a country a human would recognise, and no two spell it the same way.
    """
    )
    return


@app.cell
def _(pl):
    messy = pl.DataFrame(
        {
            "country": [
                "USA",
                "united kingdom",
                "276",
                "🇫🇷",
                "Korea, Republic of",
                "Bolivia, Plurinational State of",
                "Côte d'Ivoire",
                "Cote d'Ivoire",
                "Texas",
                "Nowhere At All",
                "",
                None,
            ]
        }
    )
    messy
    return (messy,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ## Exact lookup

    The default matches the way `pycountry.countries.lookup` does: case
    insensitive, across every field the standard indexes — alpha-2, alpha-3,
    the numeric code, the short name, the official name, the common name, and
    the flag emoji. Anything it cannot resolve is **null**, never a guess.
    """
    )
    return


@app.cell
def _(messy, pc):
    messy.with_columns(
        pc.alpha_2("country").alias("alpha_2"),
        pc.alpha_3("country").alias("alpha_3"),
        pc.numeric("country").alias("numeric"),
        pc.name("country").alias("name"),
        pc.flag("country").alias("flag"),
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    Note the three that came back null. `Cote d'Ivoire` without the accent is a
    genuine miss — `pycountry`'s indices are accent-*sensitive*, and this
    reproduces that. `Texas` is not a country. Blank and null are blank and
    null.

    /// admonition | One pass, every field
    `pc.extract` computes all of them together, which is cheaper than calling
    several single-field expressions on the same column.
    ///
    """
    )
    return


@app.cell
def _(messy, pc):
    messy.head(4).with_columns(pc.extract("country").alias("iso")).unnest("iso")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ## Try it yourself

    Type anything — a code, a number, a name, a flag emoji, a misspelling.
    """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    probe = mo.ui.text(
        value="Republic of Korea",
        label="Resolve",
        full_width=True,
    )
    probe_fuzzy = mo.ui.switch(value=False, label="fuzzy")
    probe_historic = mo.ui.switch(value=False, label="historic")
    mo.hstack(
        [probe, probe_fuzzy, probe_historic],
        justify="start",
        gap=1,
    )
    return probe, probe_fuzzy, probe_historic


@app.cell
def _(mo, pc, probe, probe_fuzzy, probe_historic):
    _hit = pc.lookup_country(
        probe.value,
        fuzzy=probe_fuzzy.value,
        historic=probe_historic.value,
    )
    mo.md(f"```python\n{_hit}\n```") if _hit else mo.callout(
        f"No match for {probe.value!r}. Try turning on `fuzzy`.",
        kind="warn",
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ## Fuzzy matching, and why it is off by default

    `fuzzy=True` lets an exact miss fall back to `pycountry`'s `search_fuzzy`:
    accents are folded away, partial names and initials match, and a country
    can be reached through one of its subdivisions.

    It is a **guess**, and a wrong guess is harder to notice than a null.
    """
    )
    return


@app.cell
def _(messy, pc):
    messy.with_columns(
        pc.alpha_2("country").alias("exact"),
        pc.alpha_2("country", fuzzy=True).alias("fuzzy"),
        pc.name("country", fuzzy=True).alias("fuzzy_name"),
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    `Cote d'Ivoire` and `Texas` now resolve. So does `Korea, Republic of` —
    but watch what `Republic of Korea` does in the widget above: it answers
    **KP**, not KR. No ISO field spells the name that way, so the subdivision
    pass decides it. `pycountry` answers KP too; this package's job is to agree
    with it, not to be cleverer than it.

    That is what `pc.match` is for. `score` is null for an exact match and
    carries the fuzzy points otherwise, so `score.is_null()` separates the
    answers you can trust from the ones worth eyeballing.
    """
    )
    return


@app.cell
def _(messy, pc, pl):
    (
        messy.with_columns(pc.match("country", fuzzy=True).alias("m"))
        .unnest("m")
        .with_columns(pl.col("score").is_null().alias("trustworthy"))
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ### The whole ranked list

    `search_countries` exposes the scores `pycountry` keeps to itself, which is
    the quickest way to see *why* a fuzzy answer came out the way it did.
    """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    ranked_query = mo.ui.text(value="guinea", label="Rank countries for", full_width=True)
    ranked_query
    return (ranked_query,)


@app.cell
def _(pc, pl, ranked_query):
    _rows = pc.search_countries(ranked_query.value, limit=10)
    pl.DataFrame(
        {
            "alpha_2": [r["alpha_2"] for r in _rows],
            "name": [r["name"] for r in _rows],
            "score": [r["score"] for r in _rows],
        }
    ) if _rows else pl.DataFrame({"alpha_2": [], "name": [], "score": []})
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ## Subdivisions — ISO 3166-2

    States, provinces, regions. The value may be a full code (`US-CA`), a bare
    code when a country is given (`CA`), or a name (`California`).

    **Pass `country` whenever you have it.** Subdivision names are not unique
    across the standard, so an unscoped name resolves to whichever record ISO
    lists first.
    """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    sub_value = mo.ui.text(value="CA", label="Subdivision", full_width=True)
    sub_country = mo.ui.text(value="US", label="within country", full_width=True)
    mo.hstack([sub_value, sub_country], justify="start", gap=1)
    return sub_country, sub_value


@app.cell
def _(mo, pc, sub_country, sub_value):
    _scoped = pc.lookup_subdivision(
        sub_value.value,
        country=sub_country.value or None,
    )
    _global = pc.lookup_subdivision(sub_value.value)
    mo.md(
        f"""
    | | result |
    | --- | --- |
    | scoped to `{sub_country.value or "—"}` | `{_scoped}` |
    | unscoped | `{_global}` |
    """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    The scope can be a column, so each row resolves against its own country.
    `Georgia` is the case that makes the point: a US state *and* a country.
    """
    )
    return


@app.cell
def _(pc, pl):
    states = pl.DataFrame(
        {
            "state": ["CA", "CA", "Georgia", "Georgia", "75C", "Bayern", "ON"],
            "country": ["US", "Canada", "US", None, "France", "DE", "CA"],
        }
    )
    states.with_columns(
        pc.subdivision_code("state", pl.col("country")).alias("code"),
        pc.subdivision_name("state", pl.col("country")).alias("name"),
        pc.subdivision_type("state", pl.col("country")).alias("type"),
        pc.subdivision_country("state", pl.col("country")).alias("in_country"),
        pc.subdivision_parent_code("state", pl.col("country")).alias("parent"),
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    Row 4 is the trap: `Georgia` with no country scope resolves to the *state*,
    because ISO lists it before nothing else claims the name. Row 2 shows the
    other half — `CA` is not a Canadian subdivision, so scoping to Canada
    correctly yields null instead of quietly handing back California.

    ## Currencies — ISO 4217

    Exact only. `pycountry` gives `search_fuzzy` to countries and subdivisions
    but not to currencies, so there is no reference to check a fuzzy currency
    search against.
    """
    )
    return


@app.cell
def _(pc, pl):
    pl.DataFrame({"money": ["USD", "978", "Yen", "pound sterling", "bitcoin"]}).with_columns(
        pc.currency_alpha_3("money").alias("code"),
        pc.currency_numeric("money").alias("numeric"),
        pc.currency_name("money").alias("name"),
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ## Withdrawn countries — ISO 3166-3

    `historic=True` consults the withdrawn table *after* ISO 3166-1 has missed,
    so a reused code still resolves to the country that holds it today. `AI` was
    French Afars and Issas until 1977 and is Anguilla now.
    """
    )
    return


@app.cell
def _(pc, pl):
    pl.DataFrame({"code": ["YUCS", "CSHH", "AI", "DDDE"]}).with_columns(
        pc.extract("code", historic=True).alias("iso")
    ).unnest("iso").select(
        "code", "alpha_2", "alpha_4", "name", "withdrawal_date", "historic"
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ## Does it actually agree with `pycountry`?

    The repository proves this properly — `tests/test_parity.py` sweeps every
    value in all four tables. This is the ten-second version, run live.
    """
    )
    return


@app.cell
def _(mo, pc, pycountry):
    def _reference(value):
        """Exact, then fuzzy — the order `fuzzy=True` promises."""
        try:
            return pycountry.countries.lookup(value).alpha_2
        except LookupError:
            pass
        try:
            return pycountry.countries.search_fuzzy(value)[0].alpha_2
        except LookupError:
            return None

    _probes = [c.name for c in pycountry.countries][:60]
    _probes += ["USA", "Texas", "Cote d'Ivoire", "guinea", "new", "Nowhere"]

    _disagreements = []
    for _value in _probes:
        _hit = pc.lookup_country(_value, fuzzy=True)
        _mine = None if _hit is None else _hit["alpha_2"]
        if _mine != _reference(_value):
            _disagreements.append(_value)

    mo.callout(
        f"Checked {len(_probes)} inputs against pycountry — no disagreements.",
        kind="success",
    ) if not _disagreements else mo.callout(
        f"Disagreements: {_disagreements}", kind="danger"
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    ## Performance

    The comparison is against the only other way to do this inside Polars:
    driving `pycountry` per row through `Expr.map_elements`.

    Press the button — the exact case runs on the row count you pick, the fuzzy
    case on a fixed 500 rows, because `search_fuzzy` costs ~15 ms *per call*
    and would otherwise take all day.
    """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    rows = mo.ui.slider(
        steps=[10_000, 50_000, 100_000, 200_000, 500_000],
        value=200_000,
        label="rows (exact case)",
        show_value=True,
    )
    run_bench = mo.ui.run_button(label="Run benchmark")
    mo.hstack([rows, run_bench], justify="start", gap=1)
    return rows, run_bench


@app.cell
def _(mo, pc, pl, pycountry, random, rows, run_bench, time):
    mo.stop(not run_bench.value, mo.md("*Press the button to measure.*"))

    def _timed(fn):
        _start = time.perf_counter()
        fn()
        return time.perf_counter() - _start

    def _exact(value):
        try:
            return pycountry.countries.lookup(value).alpha_2
        except LookupError:
            return None

    def _fuzzy(value):
        hit = _exact(value)
        if hit is not None:
            return hit
        try:
            return pycountry.countries.search_fuzzy(value)[0].alpha_2
        except LookupError:
            return None

    # Warm the one-off table parse so it is not charged to the first timing.
    pc.lookup_country("US", fuzzy=True)

    _rng = random.Random(20260820)
    _clean = [
        v
        for c in pycountry.countries
        for v in (c.alpha_2, c.alpha_3, c.numeric, c.name, c.name.lower())
    ]
    _dirty = [
        "Cote d'Ivoire", "Texas", "Bayern", "Ontario", "New South Wales",
        "USA", "Holland", "Curacao", "Reunion", "Aland Islands",
    ]

    _exact_df = pl.DataFrame({"c": [_rng.choice(_clean) for _ in range(rows.value)]})
    _fuzzy_df = pl.DataFrame({"c": [_rng.choice(_dirty) for _ in range(500)]})

    _measurements = []
    for _label, _df, _udf, _kw in (
        ("exact", _exact_df, _exact, {}),
        ("fuzzy", _fuzzy_df, _fuzzy, {"fuzzy": True}),
    ):
        _n = _df.height
        _base = _timed(
            lambda df=_df, udf=_udf: df.with_columns(
                pl.col("c").map_elements(udf, return_dtype=pl.String)
            )
        )
        _serial = _timed(
            lambda df=_df, kw=_kw: df.with_columns(pc.alpha_2("c", parallel=False, **kw))
        )
        _par = _timed(
            lambda df=_df, kw=_kw: df.with_columns(pc.alpha_2("c", parallel=True, **kw))
        )
        _measurements.append(
            {
                "case": f"{_label} ({_n:,} rows)",
                "pycountry rows/s": round(_n / _base),
                "plugin serial rows/s": round(_n / _serial),
                "plugin parallel rows/s": round(_n / _par),
                "speedup serial": round(_base / _serial, 1),
                "speedup parallel": round(_base / _par, 1),
            }
        )

    pl.DataFrame(_measurements)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
    /// admonition | Cardinality matters more than row count
    Fuzzy matching resolves the *distinct* values in a column, not the rows.
    A 200,000-row column with 14 distinct values runs at ~13M rows/s; the same
    column with 5,000 distinct values runs at ~150k. Both are thousands of times
    faster than the per-row alternative, but they are not the same number.
    ///

    ## Swapping the ISO tables

    The tables are compiled into the extension, so nothing touches the network
    at import. They can still be replaced in a running process — which is also
    how this package meets its LGPL obligation for the vendored data. See
    `NOTICE`.

    ```python
    pc.refresh_iso_data()                     # download the current tables
    pc.refresh_iso_data(save_to="iso-codes/")  # ...and keep a copy
    pc.load_iso_data("iso-codes/")             # or load ones you already have
    ```

    Nothing is swapped until every table has parsed and passed a record-count
    floor, so a truncated download leaves the working tables in place.

    ## Where to go next

    - `just test` — the Rust unit tests and the full Python suite
    - `just check` — the whole gate: fmt, clippy, ruff, ty, pydoclint, pytest
    - `just bench` — the throughput numbers above, as a repeatable benchmark
    - `just docs-serve` — the documentation site
    """
    )
    return


if __name__ == "__main__":
    app.run()
