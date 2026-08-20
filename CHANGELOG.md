# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-20

First release.

### Added

- **Country expressions** over ISO 3166-1: `extract`, `match`, `alpha_2`, `alpha_3`, `numeric`, `name`, `official_name`,
  `common_name`, `flag`. Input may be any indexed field — code, number, name, or flag emoji — matched
  case-insensitively, reproducing `pycountry.countries.lookup` including the order it probes its field indices in.
- **Subdivision expressions** over ISO 3166-2: `subdivision`, `subdivision_code`, `subdivision_name`,
  `subdivision_type`, `subdivision_country`, `subdivision_parent_code`. Each takes an optional `country` column or
  literal to scope the search, because subdivision names are not unique across the standard.
- **Currency expressions** over ISO 4217: `currency`, `currency_alpha_3`, `currency_numeric`, `currency_name`.
- **Fuzzy matching** behind `fuzzy=True`, a port of `pycountry.countries.search_fuzzy` — accent folding, partial names,
  initials, and reaching a country through its subdivisions — with the scores exposed through `match` and
  `search_countries`, which the reference keeps to itself.
- **Historic countries** behind `historic=True`, consulting ISO 3166-3 only after ISO 3166-1 has missed, so a reused
  alpha-2 code still resolves to the current country.
- The `.country` expression namespace, registered on import.
- Scalar helpers `lookup_country`, `lookup_subdivision`, `lookup_currency` and `search_countries`.
- Runtime table replacement: `POLARS_COUNTRY_DATA`, `load_iso_data`, and `refresh_iso_data`, all validating before they
  swap so a bad table leaves the working ones in place.
- Parity tests against `pycountry` over every value in all four tables, plus a fuzzy corpus.
- Fuzzy matching is memoized per call, and above the 100,000-row fan-out threshold the column's distinct values are
  resolved once up front rather than once per thread.

### Notes

- The vendored ISO tables are LGPL-2.1-or-later and are compiled into the wheel. See
  [NOTICE](https://github.com/andrewadlof/polars-country/blob/main/NOTICE) before redistributing.
- Two deliberate divergences from `pycountry`: blank input resolves to null rather than matching every country, and
  there is no fuzzy search over currencies or the historic table.

[0.1.0]: https://github.com/andrewadlof/polars-country/releases/tag/v0.1.0
[unreleased]: https://github.com/andrewadlof/polars-country/compare/v0.1.0...HEAD
