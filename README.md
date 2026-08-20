# polars-country

[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

<!--intro-start-->

ISO 3166 and ISO 4217 code lookup for [Polars](https://pola.rs), as a native Rust expression plugin.

Turning a country column into codes looks like a dictionary lookup until you meet real data. `US`, `USA`, `840`, `United
States`, `United States of America` and 🇺🇸 are all the same country; `Côte d'Ivoire` and `Cote d'Ivoire` are not the
same string; `CA` is Canada or California depending on the column next to it. The reference answer in Python is
[`pycountry`](https://github.com/pycountry/pycountry) — a good library, but a Python one. Inside Polars it can only be
driven through `Expr.map_elements`, one interpreter round-trip per row.

This package reads the same [`iso-codes`](https://salsa.debian.org/iso-codes-team/iso-codes) tables `pycountry` ships,
implements its lookup rules in Rust, and exposes them as ordinary Polars expressions. It is built to produce **identical
output to `pycountry`**, not merely similar output — see
[Correctness](https://andrewadlof.github.io/polars-country/correctness/).

```python
import polars as pl
import polars_country as pc

df = pl.DataFrame({
    "country": ["USA", "united kingdom", "276", "🇫🇷", "Nowhere", None],
})

df.with_columns(
    pc.alpha_2("country").alias("alpha_2"),
    pc.alpha_3("country").alias("alpha_3"),
    pc.name("country").alias("name"),
    pc.flag("country").alias("flag"),
)
```

```text
┌────────────────┬─────────┬─────────┬────────────────┬──────┐
│ country        ┆ alpha_2 ┆ alpha_3 ┆ name           ┆ flag │
╞════════════════╪═════════╪═════════╪════════════════╪══════╡
│ USA            ┆ US      ┆ USA     ┆ United States  ┆ 🇺🇸   │
│ united kingdom ┆ GB      ┆ GBR     ┆ United Kingdom ┆ 🇬🇧   │
│ 276            ┆ DE      ┆ DEU     ┆ Germany        ┆ 🇩🇪   │
│ 🇫🇷             ┆ FR      ┆ FRA     ┆ France         ┆ 🇫🇷   │
│ Nowhere        ┆ null    ┆ null    ┆ null           ┆ null │
│ null           ┆ null    ┆ null    ┆ null           ┆ null │
└────────────────┴─────────┴─────────┴────────────────┴──────┘
```

<!--intro-end-->

<!--install-start-->

## Install

```bash
pip install polars-country
# or
uv add polars-country
```

Prebuilt wheels cover Linux (glibc and musl, x86_64 and aarch64), macOS (Intel and Apple Silicon), and Windows (x64 and
arm64). There is one wheel per platform rather than one per Python version, because the extension is built against the
stable ABI. An sdist is published too, so anything else builds from source given a Rust toolchain — see
[CONTRIBUTING.md](https://github.com/andrewadlof/polars-country/blob/main/CONTRIBUTING.md).

`pycountry` is **not** a runtime dependency. The ISO tables are compiled into the extension; `pycountry` is installed
only for the test suite, which asserts the two agree.

<!--install-end-->

<!--usage-start-->

## Usage

Three families of expressions, all taking a string column.

### Countries — ISO 3166-1

|                     | `"USA"`                          |                                       |
| ------------------- | -------------------------------- | ------------------------------------- |
| `pc.extract`        | struct of every field            | one pass, all fields                  |
| `pc.match`          | `{"US", "alpha_3", null, false}` | *how* it resolved — see below         |
| `pc.alpha_2`        | `US`                             |                                       |
| `pc.alpha_3`        | `USA`                            |                                       |
| `pc.numeric`        | `840`                            | Utf8, zero-padded                     |
| `pc.name`           | `United States`                  | the standard's short name             |
| `pc.official_name`  | `United States of America`       | null where none differs               |
| `pc.common_name`    | null                             | e.g. `Bolivia` for `BO`               |
| `pc.flag`           | 🇺🇸                               | also accepted as *input*              |

The input may be any indexed field: alpha-2, alpha-3, numeric, name, official name, common name, or the flag emoji.
Matching is case-insensitive.

### Subdivisions — ISO 3166-2

|                            | `"California"`, country `"US"` |
| -------------------------- | ------------------------------ |
| `pc.subdivision`           | struct of every field          |
| `pc.subdivision_code`      | `US-CA`                        |
| `pc.subdivision_name`      | `California`                   |
| `pc.subdivision_type`      | `State`                        |
| `pc.subdivision_country`   | `US`                           |
| `pc.subdivision_parent_code` | null                         |

The value may be a full code (`"US-CA"`), a bare code with a country (`"CA"`), or a name (`"California"`).

**Pass `country` whenever you have it.** Subdivision names are not unique across the standard — `"Central"` names a
region in a dozen countries — so an unscoped name resolves to whichever record ISO lists first.

The argument follows the usual Polars convention: a bare string names a **column**, so a fixed country needs
`pl.lit(...)`. Either form accepts anything the country lookup does — `"US"`, `"USA"`, `"840"`, `"United States"`:

```python
df.with_columns(
    pc.subdivision_code("state", country=pl.col("country")),  # per row
    pc.subdivision_code("state", country=pl.lit("US")),  # fixed
)
```

A country that does not itself resolve yields null rather than falling back to a global search, so a bad scope cannot
quietly return another country's state.

### Currencies — ISO 4217

`pc.currency`, `pc.currency_alpha_3`, `pc.currency_numeric`, `pc.currency_name`. Input may be the code, the number, or
the name. Exact only — `pycountry` gives `search_fuzzy` to countries and subdivisions, not to currencies, so there is no
reference to match a fuzzy currency search against.

### Nulls

Every expression returns **null** when nothing matched, and null for null input. Blank and whitespace-only input is null
too — see
[fuzzy matching](https://andrewadlof.github.io/polars-country/matching/#fuzzy-matching) for why that is a
deliberate divergence from `pycountry`.

Null is also a real answer, not only a failure: most countries have no `official_name` and almost none have a
`common_name`, so `pl.coalesce(pc.common_name(c), pc.name(c))` is the usual way to get a display name.

### Expression namespace

Importing the package registers a `.country` namespace:

```python
df.with_columns(pl.col("country").country.alpha_2())
df.filter(pl.col("country").country.alpha_3() == "DEU")
```

### Scalars

For code that isn't holding a DataFrame — the same Rust core, no Polars round-trip. Each returns a `dict`, or `None`:

```python
pc.lookup_country("USA")
# {'alpha_2': 'US', 'alpha_3': 'USA', ..., 'matched_on': 'alpha_3', 'score': None}

pc.lookup_country("Texas", fuzzy=True)["alpha_2"]  # 'US'
pc.lookup_subdivision("California", country="US")  # {'code': 'US-CA', ...}
pc.lookup_currency("840")  # {'alpha_3': 'USD', ...}
pc.search_countries("guinea", limit=3)  # ranked, with scores
```

<!--usage-end-->

<!--matching-start-->

## Matching

### Exact, by default

The default is `pycountry.countries.lookup`: case-insensitive, across every field the standard indexes, and nothing
else. It is accent-**sensitive**, because `pycountry`'s indices are — `"CÔTE D'IVOIRE"` resolves and `"Cote d'Ivoire"`
does not.

Numeric codes are three digits wide and the leading zero is part of the value, so an integer column needs padding before
it will match:

```python
pl.col("n").cast(pl.String).str.zfill(3)  # 4 -> "004"
```

### Fuzzy matching

`fuzzy=True` lets an exact miss fall back to `pycountry`'s `search_fuzzy`, which folds accents away, matches partial
names and initials, and reaches countries through their subdivisions:

```python
pc.alpha_2("country", fuzzy=True)
# "Cote d'Ivoire" -> CI      (accents folded)
# "USA"           -> US      (initials of "United States of America")
# "Texas"         -> US      (via the subdivision)
# "Bavaria"       -> null    (the standard says "Bayern"; this is not a translation layer)
```

It is off by default because it is a *guess*, and a wrong guess is harder to notice than a null. `"Republic of Korea"`
resolves to **KP**, not KR — no ISO field spells the name that way, so the subdivision pass decides it. `pycountry`
answers KP too; the point is that fuzzy answers deserve review, which is what `pc.match` is for:

```python
df.with_columns(pc.match("country", fuzzy=True).alias("m")).unnest("m")
# matched_on: "alpha_3", "name", "numeric", ... or "fuzzy"
# score:      null for an exact match; the fuzzy points otherwise
```

Filtering on `pl.col("score").is_null()` separates the answers you can trust from the ones worth eyeballing.

One deliberate divergence: `pycountry.countries.search_fuzzy("")` matches *every* country — the empty string is a
substring of every name, at offset 0 — and ranks Andorra first. In a DataFrame that turns every blank cell into a
confident wrong answer, so blank input is null here.

### Historic countries — ISO 3166-3

`historic=True` consults the withdrawn-country table when ISO 3166-1 has no match. Current countries always win, so a
reused code like `AI` still resolves to Anguilla rather than to French Afars and Issas:

```python
pc.extract("country", historic=True)
# "YUCS" -> alpha_2 YU, withdrawal_date 2003-07-23, historic true
```

`alpha_4` and `withdrawal_date` are ISO 3166-3 fields, so they are populated only for a historic match. They are in the
struct either way — a keyword never changes its shape.

<!--matching-end-->

<!--performance-start-->

## Performance

<!--BENCH-TABLE-->

Measure a **release build**. `just bench` builds one; a plain `maturin develop` is unoptimized and far slower on this
workload, which measures the profile rather than the code.

Columns of 100k rows or more are split across [rayon](https://docs.rs/rayon) threads; pass `parallel=False` to force
single-threaded. The threshold sits above the streaming engine's morsel size, so when Polars is already calling the
plugin from several of its own worker threads each call stays single-threaded rather than nesting a fan-out inside it.

Fuzzy matching is a scan over ~250 countries and ~5,000 subdivisions per *distinct* input, not per row: each call
memoizes what it has already resolved. Real columns repeat themselves heavily, so the cost lands closer to the
cardinality than to the row count.

A caveat worth stating plainly: if your column has few distinct values, a `dict` built over `Series.unique()` plus
`replace_strict` can still beat any per-row approach, including this one. This package wins on high-cardinality columns,
on the fuzzy path, and on code you would rather not write.

<!--performance-end-->

<!--correctness-start-->

## Correctness

The point of this package is not "fast country lookup" — it is "fast country lookup you can swap in without your results
moving". `tests/test_parity.py` asserts agreement with `pycountry` over four corpora:

1. **Every indexed value in ISO 3166-1** — each alpha-2, alpha-3, numeric code, name, official name, common name and
   flag in the standard, in original, upper and lower case. If the two agree on every string the table contains, they
   agree on every exact lookup that can succeed.
2. **Every ISO 3166-2 code** — all ~5,000 of them, checked against `pycountry.subdivisions.get`.
3. **Every ISO 4217 value** — codes, numbers and names.
4. **A fuzzy corpus** — every country name and subdivision name, plus hand-written messy input, compared against
   `pycountry.countries.search_fuzzy` result by result, including the ranking.

Both sides are pointed at the same table files, so a disagreement can only be an algorithm difference — never two
different snapshots. `tests/test_data.py` separately asserts the vendored tables *are* the ones the installed
`pycountry` ships.

The known, deliberate divergences are blank input (null rather than "every country"), and no fuzzy search over
currencies or the historic table. Everything else agreeing is the contract; if you find an input where this package and
`pycountry` disagree, that is a bug here. Please
[open an issue](https://github.com/andrewadlof/polars-country/issues) with the input.

<!--correctness-end-->

<!--data-start-->

## The ISO tables

A snapshot of the `iso-codes` tables is compiled into the binary, so there is no network access, no cache directory, and
no first-call latency spike. `pc.iso_version()` reports the upstream release in use, and `pc.table_sizes()` how many
records each table holds.

To supply your own tables at startup, point `POLARS_COUNTRY_DATA` at a directory holding `iso3166-1.json`,
`iso3166-2.json`, `iso3166-3.json` and `iso4217.json`:

```bash
export POLARS_COUNTRY_DATA=/path/to/iso-codes
```

It is read once, on first use, so set it before the first lookup.

### Refreshing without a restart

The standard changes — countries are added, subdivisions renamed, codes withdrawn. A long-lived process would otherwise
be stuck with whatever tables it read when it resolved its first country, so two functions replace them in place:

```python
# Download the current tables and load them into this process.
pc.refresh_iso_data()

# ...and keep a copy, so the next run need not go back to the network.
pc.refresh_iso_data(save_to="iso-codes/")

# Or load ones you already have.
pc.load_iso_data("iso-codes/")
```

Both take effect for every lookup that *starts* after they return. A query already in flight keeps the tables it began
with, so no single column is ever resolved against two different snapshots.

`refresh_iso_data` is the only function here that touches the network, and only when you call it — importing the package
still does nothing. Point it at an internal mirror with `pc.refresh_iso_data(url=...)` if outbound access is restricted.

A missing file, an unparseable table, or one too small to be a complete copy raises `ValueError` and **leaves the
working tables untouched**. That last check earns its keep: a truncated download or an HTML error page is still valid
*something*, and without a floor on the record count the only symptom would be countries quietly going missing.

<!--data-end-->

<!--compat-start-->

## Compatibility

|         |                                                                        |
| ------- | ---------------------------------------------------------------------- |
| Python  | 3.10+ — one `abi3` wheel covers all versions                           |
| Polars  | 1.37+ — the plugin FFI ABI is `(0, 1)` and unchanged across that range |
| Linux   | `manylinux2014` and `musllinux_1_2`, x86_64 and aarch64                |
| macOS   | x86_64 (10.12+) and arm64 (11.0+)                                      |
| Windows | x64 and arm64                                                          |

If a future Polars release bumps the plugin ABI, this package fails loudly at load rather than miscomputing.

<!--compat-end-->

## Contributing

Contributions are welcome — see
[CONTRIBUTING.md](https://github.com/andrewadlof/polars-country/blob/main/CONTRIBUTING.md) for the development loop, the
parity requirement, and how to refresh the ISO tables.
[`docs/architecture/overview.md`](https://andrewadlof.github.io/polars-country/architecture/overview/) explains how this
implementation maps onto `pycountry`'s, which is worth reading before changing the matching rules.

## License

Licensed under either of
[Apache License, Version 2.0](https://github.com/andrewadlof/polars-country/blob/main/LICENSE-APACHE) or
[MIT license](https://github.com/andrewadlof/polars-country/blob/main/LICENSE-MIT) at your option.

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this work, as defined
in the Apache-2.0 license, shall be dual licensed as above, without any additional terms or conditions.

**The vendored ISO tables are not covered by that dual license.** They come from the `iso-codes` project and are
licensed **LGPL-2.1-or-later**, which has obligations that attach to any binary embedding them. See
[NOTICE](https://github.com/andrewadlof/polars-country/blob/main/NOTICE) before redistributing.
