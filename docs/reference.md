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

::: polars_country.extract

::: polars_country.match

::: polars_country.alpha_2

::: polars_country.alpha_3

::: polars_country.numeric

::: polars_country.name

::: polars_country.official_name

::: polars_country.common_name

::: polars_country.flag

### Subdivisions — ISO 3166-2

Each takes an optional `country` to scope the search by, which you should pass whenever you have it — subdivision names
are not unique across the standard.

::: polars_country.subdivision

::: polars_country.subdivision_code

::: polars_country.subdivision_name

::: polars_country.subdivision_type

::: polars_country.subdivision_country

::: polars_country.subdivision_parent_code

### Currencies — ISO 4217

Exact matching only; there is no fuzzy path, because `pycountry` has none to match.

::: polars_country.currency

::: polars_country.currency_alpha_3

::: polars_country.currency_numeric

::: polars_country.currency_name

## The `.country` namespace

Importing `polars_country` registers this, so `pl.col("c").country.alpha_2()` works without importing anything else.
Each method mirrors the module-level function of the same name.

::: polars_country.CountryNamespace
    options:
      members_order: source

## Scalars

For code that is not holding a DataFrame — the same Rust core, no Polars round-trip. Each returns a `dict`, or `None`
when nothing matched.

::: polars_country.lookup_country

::: polars_country.search_countries

::: polars_country.lookup_subdivision

::: polars_country.lookup_currency

## The ISO tables

::: polars_country.iso_version

::: polars_country.table_sizes

::: polars_country.load_iso_data

::: polars_country.refresh_iso_data
