# API reference

Generated from the source, so it cannot drift from the code.

## Expressions

### Countries — ISO 3166-1

Every country expression takes a string column and the same three keywords:

- `fuzzy` — whether an exact miss may fall back to `pycountry`'s `search_fuzzy`. Off by default; see
  [Matching](matching.md#fuzzy-matching).
- `historic` — whether ISO 3166-3 is consulted after ISO 3166-1. Off by default.
- `parallel` — whether the plugin may split large columns across rayon threads. Columns below 100k rows run
  single-threaded regardless.

::: polars_pycountry.extract

::: polars_pycountry.match

::: polars_pycountry.alpha_2

::: polars_pycountry.alpha_3

::: polars_pycountry.numeric

::: polars_pycountry.name

::: polars_pycountry.official_name

::: polars_pycountry.common_name

::: polars_pycountry.flag

### Subdivisions — ISO 3166-2

Each takes an optional `country` to scope the search by, which you should pass whenever you have it — subdivision names
are not unique across the standard.

::: polars_pycountry.subdivision

::: polars_pycountry.subdivision_code

::: polars_pycountry.subdivision_name

::: polars_pycountry.subdivision_type

::: polars_pycountry.subdivision_country

::: polars_pycountry.subdivision_parent_code

### Currencies — ISO 4217

Exact matching only; there is no fuzzy path, because `pycountry` has none to match.

::: polars_pycountry.currency

::: polars_pycountry.currency_alpha_3

::: polars_pycountry.currency_numeric

::: polars_pycountry.currency_name

## The `.country` namespace

Importing `polars_pycountry` registers this, so `pl.col("c").country.alpha_2()` works without importing anything else.
Each method mirrors the module-level function of the same name.

::: polars_pycountry.CountryNamespace
    options:
      members_order: source

## Scalars

For code that is not holding a DataFrame — the same Rust core, no Polars round-trip. Each returns a `dict`, or `None`
when nothing matched.

::: polars_pycountry.lookup_country

::: polars_pycountry.search_countries

::: polars_pycountry.lookup_subdivision

::: polars_pycountry.lookup_currency

## The ISO tables

::: polars_pycountry.iso_version

::: polars_pycountry.table_sizes

::: polars_pycountry.load_iso_data

::: polars_pycountry.refresh_iso_data
