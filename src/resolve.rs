//! Turning one input string into a record, for every table.
//!
//! This is where `pycountry`'s exact lookup, the fuzzy search, and the
//! subdivision handling are stitched into the order the expressions promise:
//!
//! 1. exact against ISO 3166-1,
//! 2. exact against ISO 3166-3, only when `historic` is set,
//! 3. fuzzy, only when `fuzzy` is set.
//!
//! Exact always beats fuzzy, and a current country always beats a withdrawn
//! one -- `AI` is Anguilla today and was French Afars and Issas until 1977, so
//! the order is what keeps the common case right.
//!
//! # Blank input
//!
//! Input that is empty or all whitespace resolves to `None`, and that is a
//! deliberate divergence. `pycountry.countries.search_fuzzy("")` matches every
//! country -- the empty string is a substring of every name, at offset 0 --
//! and ranks Andorra first. In a DataFrame that turns every blank cell into a
//! confident wrong answer, so blanks stay null here.
//!
//! # Memoization
//!
//! Real columns hold far fewer distinct country strings than rows, and the
//! fuzzy search is a scan over ~250 countries and ~5,000 subdivisions. The
//! resolvers therefore cache by input string. The cache lives for one call on
//! one slice of one column, so it cannot outlive a table swap or grow without
//! bound across queries.
//!
//! A per-slice cache is not enough on its own, though. When the column is
//! large enough to fan out, each thread would build its own -- so a column of
//! N distinct values would run the search up to `threads * N` times instead of
//! N, and the parallel path could end up *slower* than the serial one on a
//! high-cardinality column. [`prefetch_countries`] closes that: it resolves
//! every distinct value in the column exactly once, itself in parallel, and
//! the row fill afterwards is a hash probe. Both phases scale, and no search
//! is ever repeated.

use std::collections::{HashMap, HashSet};

use polars::prelude::StringChunked;
use rayon::prelude::*;

use crate::db::{CodeTable, Db};
use crate::fold::{char_find, fold_query, lower_into};
use crate::fuzzy;

/// A resolved country, and how it was reached.
#[derive(Clone, Copy)]
pub struct CountryMatch<'a> {
    /// The table the record lives in: 3166-1, or 3166-3 when `historic`.
    pub table: &'a CodeTable,
    /// Index into `table`.
    pub record: usize,
    /// The field that matched, or `"fuzzy"` when the fuzzy search found it.
    pub matched_on: &'a str,
    /// The fuzzy score, or `None` for an exact match.
    pub score: Option<u32>,
    /// Whether the record came from ISO 3166-3 rather than 3166-1.
    pub historic: bool,
}

impl CountryMatch<'_> {
    /// The record's alpha-2 code, which every table here carries.
    pub fn alpha_2(&self) -> Option<&str> {
        self.table.alpha_2(self.record)
    }
}

/// Resolves country strings, caching what it has already seen.
pub struct CountryResolver<'a> {
    db: &'a Db,
    fuzzy: bool,
    historic: bool,
    /// Answers computed once for the whole column, before any fan-out. `None`
    /// on the exact path and for scalar callers, where there is nothing to
    /// share.
    shared: Option<&'a Prefetched<'a>>,
    /// Only allocated when fuzzy is on: an exact lookup is already a hash
    /// probe, so caching it would cost more than it saves. Backs up `shared`
    /// for anything it does not hold.
    memo: Option<HashMap<String, Option<CountryMatch<'a>>>>,
    /// Reused lowercase buffer, so the exact path allocates once per call
    /// rather than once per row.
    scratch: String,
}

/// Every distinct value of one column, already resolved.
pub type Prefetched<'a> = HashMap<String, Option<CountryMatch<'a>>>;

impl<'a> CountryResolver<'a> {
    /// Build a resolver over `db` with the given behavior.
    pub fn new(db: &'a Db, fuzzy: bool, historic: bool) -> Self {
        Self::with_shared(db, fuzzy, historic, None)
    }

    /// Build a resolver that answers from a column-wide prefetch first.
    pub fn with_shared(
        db: &'a Db,
        fuzzy: bool,
        historic: bool,
        shared: Option<&'a Prefetched<'a>>,
    ) -> Self {
        let memo = fuzzy.then(HashMap::new);
        Self { db, fuzzy, historic, shared, memo, scratch: String::new() }
    }

    /// Resolve one value, or `None` if nothing matched.
    pub fn resolve(&mut self, value: &str) -> Option<CountryMatch<'a>> {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            return None;
        }
        if let Some(hit) = self.shared.and_then(|shared| shared.get(trimmed)) {
            return *hit;
        }
        let Some(memo) = &self.memo else {
            return Self::compute(self.db, self.fuzzy, self.historic, &mut self.scratch, trimmed);
        };
        if let Some(hit) = memo.get(trimmed) {
            return *hit;
        }
        let resolved =
            Self::compute(self.db, self.fuzzy, self.historic, &mut self.scratch, trimmed);
        self.memo
            .as_mut()
            .expect("the memo was just borrowed as present")
            .insert(trimmed.to_owned(), resolved);
        resolved
    }

    /// The resolution order itself, taking its state as arguments so the
    /// scratch buffer can be borrowed mutably alongside the memo, and so
    /// [`prefetch_countries`] can drive it without building a resolver per
    /// value.
    fn compute(
        db: &'a Db,
        fuzzy: bool,
        historic: bool,
        scratch: &mut String,
        trimmed: &str,
    ) -> Option<CountryMatch<'a>> {
        // Lowered once and reused for both tables: the fold is the same for
        // either, and it is the only allocation on this path.
        let lowered = lower_into(scratch, trimmed);
        if let Some((record, matched_on)) = db.countries.lookup_lowered(lowered) {
            return Some(CountryMatch {
                table: &db.countries,
                record,
                matched_on,
                score: None,
                historic: false,
            });
        }
        if historic {
            if let Some((record, matched_on)) = db.historic.lookup_lowered(lowered) {
                return Some(CountryMatch {
                    table: &db.historic,
                    record,
                    matched_on,
                    score: None,
                    historic: true,
                });
            }
        }
        if fuzzy {
            if let Some(scored) = fuzzy::best(db, &fold_query(trimmed)) {
                return Some(CountryMatch {
                    table: &db.countries,
                    record: scored.record,
                    matched_on: "fuzzy",
                    score: Some(scored.points),
                    historic: false,
                });
            }
        }
        None
    }
}

/// Resolve every distinct value in `ca` once, fanning the searches out.
///
/// Worth doing only for the fuzzy path: an exact lookup is already a hash
/// probe, so collecting the distinct set would cost more than it saves.
///
/// `parallel` follows the caller's keyword rather than the column length --
/// the work here is proportional to the number of *distinct* values, and a
/// short column of expensive-to-resolve strings is exactly the case that
/// benefits.
pub fn prefetch_countries<'a>(
    db: &'a Db,
    ca: &StringChunked,
    historic: bool,
    parallel: bool,
) -> Prefetched<'a> {
    let mut distinct: HashSet<&str> = HashSet::new();
    for value in ca.iter().flatten() {
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            distinct.insert(trimmed);
        }
    }

    let resolve_one = |value: &str| {
        let mut scratch = String::new();
        (value.to_owned(), CountryResolver::compute(db, true, historic, &mut scratch, value))
    };

    if parallel && distinct.len() > 1 {
        distinct.into_iter().par_bridge().map(resolve_one).collect()
    } else {
        distinct.into_iter().map(resolve_one).collect()
    }
}

/// Resolves subdivision strings, optionally scoped to a country.
///
/// Scoping matters more than it looks. Subdivision names are not unique --
/// `"Central"` names a region in a dozen countries, and `"Georgia"` is both a
/// US state and a country -- so an unscoped name lookup answers with whichever
/// record the standard happens to list first. Passing a country makes the
/// answer well defined.
pub struct SubdivisionResolver<'a> {
    db: &'a Db,
    fuzzy: bool,
    countries: CountryResolver<'a>,
    memo: HashMap<(String, String), Option<usize>>,
}

impl<'a> SubdivisionResolver<'a> {
    /// Build a resolver over `db`. `fuzzy` applies to both the country scope
    /// and the subdivision name itself.
    pub fn new(db: &'a Db, fuzzy: bool) -> Self {
        Self::with_shared(db, fuzzy, None)
    }

    /// Build a resolver whose country scopes come from a column-wide
    /// prefetch, so the fan-out does not re-resolve them per thread.
    pub fn with_shared(db: &'a Db, fuzzy: bool, countries: Option<&'a Prefetched<'a>>) -> Self {
        Self {
            db,
            fuzzy,
            countries: CountryResolver::with_shared(db, fuzzy, false, countries),
            memo: HashMap::new(),
        }
    }

    /// Resolve one value within an optional country, returning a 3166-2
    /// record index.
    pub fn resolve(&mut self, value: &str, country: Option<&str>) -> Option<usize> {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            return None;
        }
        // Both halves are part of the answer, so both are part of the key.
        let key = (trimmed.to_owned(), country.unwrap_or_default().trim().to_owned());
        if let Some(hit) = self.memo.get(&key) {
            return *hit;
        }

        let scope = country
            .map(str::trim)
            .filter(|c| !c.is_empty())
            .map(|c| self.countries.resolve(c).and_then(|m| m.alpha_2().map(str::to_owned)));
        let resolved = match scope {
            // A country was asked for and could not be resolved: the answer is
            // unknown, not "search everywhere". Falling back to a global
            // search here would silently return another country's state.
            Some(None) => None,
            Some(Some(alpha_2)) => self.within(trimmed, &alpha_2),
            None => self.anywhere(trimmed),
        };

        self.memo.insert(key, resolved);
        resolved
    }

    /// Resolve inside one country.
    fn within(&self, value: &str, alpha_2: &str) -> Option<usize> {
        let subs = &self.db.subdivisions;
        // The country's records, ascending -- they are collected in file
        // order, so membership is a binary search rather than a scan.
        let scope = subs.in_country(alpha_2);
        let in_country = |record: usize| scope.binary_search(&record).is_ok();

        // A full code as given, e.g. "US-CA".
        if let Some(record) = subs.by_code(value).filter(|&r| in_country(r)) {
            return Some(record);
        }
        // A bare code, e.g. "CA" under "US".
        if let Some(record) = subs.by_code(&format!("{alpha_2}-{value}")) {
            return Some(record);
        }
        // A name, e.g. "California".
        if let Some(&record) = subs.by_name(value).iter().find(|&&r| in_country(r)) {
            return Some(record);
        }
        if !self.fuzzy {
            return None;
        }
        let folded = fold_query(value);
        if let Some(&record) = subs.by_folded_name(&folded).iter().find(|&&r| in_country(r)) {
            return Some(record);
        }
        self.earliest_substring(&folded, Some(scope))
    }

    /// Resolve without a country scope, taking the standard's first listing.
    fn anywhere(&self, value: &str) -> Option<usize> {
        let subs = &self.db.subdivisions;
        if let Some(record) = subs.by_code(value) {
            return Some(record);
        }
        if let Some(&record) = subs.by_name(value).first() {
            return Some(record);
        }
        if !self.fuzzy {
            return None;
        }
        let folded = fold_query(value);
        if let Some(&record) = subs.by_folded_name(&folded).first() {
            return Some(record);
        }
        self.earliest_substring(&folded, None)
    }

    /// The subdivision whose name contains `folded` earliest, ties going to
    /// the standard's listing order.
    ///
    /// `scope` narrows the candidates to one country's records; `None` scans
    /// the whole table.
    fn earliest_substring(&self, folded: &str, scope: Option<&[usize]>) -> Option<usize> {
        let subs = &self.db.subdivisions;
        let earliest = |records: &mut dyn Iterator<Item = usize>| -> Option<usize> {
            records
                .filter_map(|record| {
                    char_find(&subs.records[record].folded_name, folded)
                        .map(|offset| (offset, record))
                })
                .min()
                .map(|(_, record)| record)
        };
        match scope {
            Some(records) => earliest(&mut records.iter().copied()),
            None => earliest(&mut (0..subs.records.len())),
        }
    }
}

/// Resolve a currency against ISO 4217.
///
/// Exact only, with no fuzzy pass: `pycountry` gives `search_fuzzy` to
/// countries and subdivisions and not to currencies, and inventing one here
/// would be a behavior this package could not point at a reference for.
pub fn resolve_currency<'a>(db: &'a Db, value: &str) -> Option<(usize, &'a str)> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return None;
    }
    db.currencies.lookup(trimmed)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::current;

    fn country(value: &str, fuzzy: bool, historic: bool) -> Option<String> {
        let db = current();
        let mut resolver = CountryResolver::new(&db, fuzzy, historic);
        resolver.resolve(value).and_then(|m| m.alpha_2().map(str::to_owned))
    }

    #[test]
    fn exact_lookup_spans_every_field() {
        for probe in ["US", "usa", "840", "United States", "  us  "] {
            assert_eq!(country(probe, false, false).as_deref(), Some("US"), "{probe:?}");
        }
    }

    #[test]
    fn fuzzy_is_opt_in() {
        // The exact indices are accent-sensitive, so the unaccented spelling
        // misses until the fuzzy pass folds both sides.
        assert_eq!(country("Cote d'Ivoire", false, false), None);
        assert_eq!(country("Cote d'Ivoire", true, false).as_deref(), Some("CI"));
    }

    #[test]
    fn historic_is_opt_in_and_never_shadows_a_current_country() {
        // ISO 3166-3 keys on the four-letter code and the standard's own
        // name; "Yugoslavia" alone is not a field value in it, and neither
        // this crate nor `pycountry` resolves it.
        assert_eq!(country("YUCS", false, true).as_deref(), Some("YU"));
        assert_eq!(country("YUCS", false, false), None);
        assert_eq!(country("Yugoslavia", false, true), None);
        // AI is Anguilla now and was French Afars and Issas until 1977. The
        // current table is consulted first, so the reuse cannot bite.
        assert_eq!(country("AI", false, true).as_deref(), Some("AI"));
        let db = current();
        let mut resolver = CountryResolver::new(&db, false, true);
        assert_eq!(resolver.resolve("AI").map(|m| m.historic), Some(false));
    }

    #[test]
    fn blank_input_never_resolves() {
        for probe in ["", "   ", "\t\n"] {
            assert_eq!(country(probe, true, true), None, "{probe:?}");
        }
    }

    #[test]
    fn subdivisions_resolve_by_code_and_by_name() {
        let db = current();
        let mut resolver = SubdivisionResolver::new(&db, false);
        let mut code = |v: &str, c: Option<&str>| {
            resolver.resolve(v, c).map(|r| db.subdivisions.records[r].code.clone())
        };
        assert_eq!(code("US-CA", None).as_deref(), Some("US-CA"));
        assert_eq!(code("CA", Some("US")).as_deref(), Some("US-CA"));
        assert_eq!(code("California", Some("United States")).as_deref(), Some("US-CA"));
        assert_eq!(code("Bavaria", Some("DE")), None, "the standard spells it Bayern");
    }

    #[test]
    fn a_country_scope_that_does_not_resolve_yields_nothing() {
        let db = current();
        let mut resolver = SubdivisionResolver::new(&db, false);
        // Not a global search: "California" exists, but not in a country
        // called "Atlantis".
        assert_eq!(resolver.resolve("California", Some("Atlantis")), None);
    }

    #[test]
    fn a_scope_keeps_colliding_names_apart() {
        let db = current();
        let mut resolver = SubdivisionResolver::new(&db, false);
        let country_of = |r: usize| db.subdivisions.records[r].country_code.clone();
        let georgia_us = resolver.resolve("Georgia", Some("US")).map(country_of);
        assert_eq!(georgia_us.as_deref(), Some("US"));
    }

    #[test]
    fn currencies_resolve_by_code_name_and_number() {
        let db = current();
        for probe in ["USD", "usd", "840", "US Dollar"] {
            let (record, _) =
                resolve_currency(&db, probe).unwrap_or_else(|| panic!("{probe:?} should resolve"));
            assert_eq!(db.currencies.alpha_3(record), Some("USD"), "{probe:?}");
        }
    }
}
