//! The ISO tables, their indices, and replacing them at runtime.
//!
//! Four tables are vendored from the Debian `iso-codes` project, which is also
//! where `pycountry` gets them -- it ships these bytes unchanged. Taking them
//! from the same source is what makes the parity suite mean something: a
//! disagreement can only be an algorithm difference, never two snapshots.
//!
//! | table  | contents                                   |
//! |--------|--------------------------------------------|
//! | 3166-1 | current countries                          |
//! | 3166-2 | subdivisions (states, provinces, regions)  |
//! | 3166-3 | countries withdrawn from the standard      |
//! | 4217   | currencies                                 |
//!
//! The bundled snapshot is compiled in unless `POLARS_COUNTRY_DATA` points at
//! a directory of replacements, resolved on first use. After that,
//! [`load_from_dir`] swaps the tables in a running process, which is what a
//! long-lived job needs: the standard changes, and a cluster that has already
//! resolved one country would otherwise be stuck with whatever it read first.
//!
//! Readers take a snapshot ([`current`]) rather than holding the lock, so a
//! replacement never blocks lookups and an in-flight query keeps the tables it
//! started with.
//!
//! # Reproducing `pycountry`'s index order
//!
//! `pycountry.db.Database` builds one index per field and `lookup()` walks
//! them **in the order the fields were first seen in the JSON**, returning the
//! first hit. That order is therefore part of the observable behavior, so the
//! records are parsed with their keys in file order (serde_json's
//! `preserve_order`) and the indices are built in the same sweep. The fields
//! are not modelled as a struct for the same reason: a field this crate did
//! not know about would silently drop out of the index and change what
//! `lookup` returns.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock, RwLock};

use serde_json::{Map, Value};

use crate::fold::{fold, initials, lower};

/// Environment variable holding a path to a directory of ISO table JSON files.
///
/// Read when the tables are first needed. To change them after that point,
/// call [`load_from_dir`] -- the variable is not re-read.
pub const DATA_PATH_ENV: &str = "POLARS_COUNTRY_DATA";

const BUNDLED_3166_1: &str = include_str!("data/iso3166-1.json");
const BUNDLED_3166_2: &str = include_str!("data/iso3166-2.json");
const BUNDLED_3166_3: &str = include_str!("data/iso3166-3.json");
const BUNDLED_4217: &str = include_str!("data/iso4217.json");
const BUNDLED_VERSION: &str = include_str!("data/VERSION");

/// One table: the file it lives in, the JSON key holding its records, and the
/// fewest records a believable copy of it has.
///
/// The floors are a sanity check on a *replacement*, not a schema. A truncated
/// file or an HTML error page still parses as something, and without a floor
/// the failure would surface only as countries quietly going missing.
struct TableSpec {
    file: &'static str,
    root_key: &'static str,
    floor: usize,
}

const SPEC_3166_1: TableSpec = TableSpec { file: "iso3166-1.json", root_key: "3166-1", floor: 200 };
const SPEC_3166_2: TableSpec =
    TableSpec { file: "iso3166-2.json", root_key: "3166-2", floor: 4_000 };
const SPEC_3166_3: TableSpec = TableSpec { file: "iso3166-3.json", root_key: "3166-3", floor: 20 };
const SPEC_4217: TableSpec = TableSpec { file: "iso4217.json", root_key: "4217", floor: 150 };

/// The file holding the upstream release stamp, written by `just refresh-iso`.
pub const VERSION_FILE: &str = "VERSION";

// =============================================================================
// Parsing
// =============================================================================

/// Pull the record array out of one table file, keys in file order.
fn parse_records(text: &str, spec: &TableSpec) -> Result<Vec<Map<String, Value>>, String> {
    let root: Value =
        serde_json::from_str(text).map_err(|e| format!("{} is not valid JSON: {e}", spec.file))?;
    let Some(array) = root.get(spec.root_key).and_then(Value::as_array) else {
        return Err(format!(
            "{} has no {:?} array. Expected the shape the iso-codes project \
             publishes: {{\"{}\": [ ... ]}}.",
            spec.file, spec.root_key, spec.root_key
        ));
    };

    let mut records = Vec::with_capacity(array.len());
    for (i, entry) in array.iter().enumerate() {
        let Some(object) = entry.as_object() else {
            return Err(format!("{}: record {i} is not a JSON object", spec.file));
        };
        // Every value in these tables is a string. Anything else would be
        // indexed as its Debug spelling and match nothing, so reject it here
        // where the message can say which record is at fault.
        let mut fields = Map::new();
        for (key, value) in object {
            let Some(text) = value.as_str() else {
                return Err(format!(
                    "{}: record {i} field {key:?} is {value}, not a string",
                    spec.file
                ));
            };
            fields.insert(key.clone(), Value::String(text.to_owned()));
        }
        records.push(fields);
    }

    if records.len() < spec.floor {
        return Err(format!(
            "{} holds {} records, fewer than the {} a complete table has",
            spec.file,
            records.len(),
            spec.floor
        ));
    }
    Ok(records)
}

// =============================================================================
// CodeTable: 3166-1, 3166-3 and 4217
// =============================================================================

/// The name/official-name/comment forms `search_fuzzy` compares against,
/// precomputed once at load rather than per row.
pub struct FuzzyField {
    /// `remove_accents(value.lower())`.
    pub folded: String,
    /// The value's uppercase characters, folded: `"usa"` for the USA.
    pub initials: String,
}

/// Byte offsets of the fields this crate exposes by name, resolved once.
#[derive(Default)]
struct Slots {
    alpha_2: Option<usize>,
    alpha_3: Option<usize>,
    alpha_4: Option<usize>,
    numeric: Option<usize>,
    name: Option<usize>,
    official_name: Option<usize>,
    common_name: Option<usize>,
    flag: Option<usize>,
    comment: Option<usize>,
    withdrawal_date: Option<usize>,
}

/// A flat table with one index per field, mirroring `pycountry.db.Database`.
pub struct CodeTable {
    /// Field names in `pycountry`'s index insertion order.
    fields: Vec<String>,
    /// `rows[record][field]`, aligned to `fields`.
    rows: Vec<Vec<Option<String>>>,
    /// One index per field, aligned to `fields`: lowercased value -> record.
    /// Backs `get`, which probes a named field the way `Database.get` does.
    indices: Vec<HashMap<String, usize>>,
    /// The same indices flattened into one: lowercased value -> `(record,
    /// field)`. `lookup` walks the per-field indices in order and takes the
    /// first hit, which is a fixed function of the data -- so it is resolved
    /// once at load and answered here in a single probe instead of up to
    /// seven. See `merge_indices`.
    index: HashMap<String, (usize, usize)>,
    slots: Slots,
    /// Per record, the `[name, official_name, comment]` forms that are
    /// present, in that order -- the order `search_fuzzy` checks them in.
    fuzzy: Vec<Vec<FuzzyField>>,
}

impl CodeTable {
    fn build(records: Vec<Map<String, Value>>) -> Self {
        // First-seen field order across the records, which is the order
        // `pycountry` ends up indexing (and therefore probing) them in.
        let mut fields: Vec<String> = Vec::new();
        for record in &records {
            for key in record.keys() {
                if !fields.iter().any(|f| f == key) {
                    fields.push(key.clone());
                }
            }
        }

        let mut rows: Vec<Vec<Option<String>>> = Vec::with_capacity(records.len());
        let mut indices: Vec<HashMap<String, usize>> = vec![HashMap::new(); fields.len()];
        for (r, record) in records.iter().enumerate() {
            let mut row = vec![None; fields.len()];
            for (f, field) in fields.iter().enumerate() {
                let Some(value) = record.get(field).and_then(Value::as_str) else {
                    continue;
                };
                // Last write wins on a duplicate, which is what `pycountry`
                // does: it logs the collision and then overwrites anyway.
                indices[f].insert(lower(value).into_owned(), r);
                row[f] = Some(value.to_owned());
            }
            rows.push(row);
        }

        let slot = |name: &str| fields.iter().position(|f| f == name);
        let slots = Slots {
            alpha_2: slot("alpha_2"),
            alpha_3: slot("alpha_3"),
            alpha_4: slot("alpha_4"),
            numeric: slot("numeric"),
            name: slot("name"),
            official_name: slot("official_name"),
            common_name: slot("common_name"),
            flag: slot("flag"),
            comment: slot("comment"),
            withdrawal_date: slot("withdrawal_date"),
        };

        // `search_fuzzy` checks name, then official_name, then comment, and
        // stops at the first of them that matches. Absent fields are skipped
        // without stopping, so leaving them out of this list is equivalent.
        let fuzzy = rows
            .iter()
            .map(|row| {
                [slots.name, slots.official_name, slots.comment]
                    .into_iter()
                    .flatten()
                    .filter_map(|s| row[s].as_deref())
                    .map(|v| FuzzyField { folded: fold(v), initials: initials(v) })
                    .collect()
            })
            .collect();

        let index = merge_indices(&indices);
        Self { fields, rows, indices, index, slots, fuzzy }
    }

    /// Number of records.
    pub fn len(&self) -> usize {
        self.rows.len()
    }

    /// `pycountry`'s `Database.lookup`: lowercase, then take the first field
    /// index that holds the value.
    ///
    /// Returns the record index and the field that matched, which is what the
    /// `matched_on` column reports.
    pub fn lookup(&self, value: &str) -> Option<(usize, &str)> {
        self.lookup_lowered(lower(value).as_ref())
    }

    /// [`lookup`](Self::lookup) for a caller that has already lowercased.
    ///
    /// The vectorized paths lower once into a reusable buffer and probe both
    /// the current and the historic table with the result, so they take this
    /// rather than paying for the same fold twice per row.
    pub fn lookup_lowered(&self, lowered: &str) -> Option<(usize, &str)> {
        self.index.get(lowered).map(|&(record, f)| (record, self.fields[f].as_str()))
    }

    /// `pycountry`'s `Database.get(field=value)`: one index, case-insensitive.
    pub fn get(&self, field: &str, value: &str) -> Option<usize> {
        let f = self.fields.iter().position(|name| name == field)?;
        self.indices[f].get(lower(value).as_ref()).copied()
    }

    fn field(&self, record: usize, slot: Option<usize>) -> Option<&str> {
        slot.and_then(|s| self.rows[record][s].as_deref())
    }

    /// The `[name, official_name, comment]` forms `search_fuzzy` scans.
    pub fn fuzzy_fields(&self, record: usize) -> &[FuzzyField] {
        &self.fuzzy[record]
    }
}

/// Flatten per-field indices into one, preserving `lookup`'s answer.
///
/// `pycountry.db.Database.lookup` probes the field indices in insertion order
/// and returns the first hit, so for any given value the field that answers is
/// fixed by the data. Merging in that same order with "first insert wins"
/// therefore records exactly that field -- and collapses a walk over every
/// index into one probe.
///
/// Last-write-wins *within* a field has already happened by the time this
/// runs, so the two collision rules stay separate, as they are upstream.
fn merge_indices(indices: &[HashMap<String, usize>]) -> HashMap<String, (usize, usize)> {
    let mut merged: HashMap<String, (usize, usize)> =
        HashMap::with_capacity(indices.iter().map(HashMap::len).sum());
    for (f, index) in indices.iter().enumerate() {
        for (value, &record) in index {
            merged.entry(value.clone()).or_insert((record, f));
        }
    }
    merged
}

/// Accessors for the fields the expressions expose. Each is `None` when the
/// table does not carry that field at all (4217 has no `alpha_2`) or when this
/// record leaves it out (most countries have no `common_name`).
macro_rules! code_table_accessors {
    ($($name:ident),* $(,)?) => {
        impl CodeTable {
            $(
                #[doc = concat!("The record's `", stringify!($name), "`, if it has one.")]
                pub fn $name(&self, record: usize) -> Option<&str> {
                    self.field(record, self.slots.$name)
                }
            )*
        }
    };
}

code_table_accessors!(
    alpha_2,
    alpha_3,
    alpha_4,
    numeric,
    name,
    official_name,
    common_name,
    flag,
    withdrawal_date,
);

// =============================================================================
// SubdivisionTable: 3166-2
// =============================================================================

/// One ISO 3166-2 subdivision.
pub struct Subdivision {
    /// The full code, e.g. `"US-CA"`.
    pub code: String,
    pub name: String,
    /// The JSON `type` field, e.g. `"State"`. Named `kind` because `type` is
    /// a Rust keyword.
    pub kind: String,
    /// The parent subdivision's code, normalized to carry the country prefix.
    pub parent_code: Option<String>,
    /// The leading segment of `code`, e.g. `"US"`.
    pub country_code: String,
    /// Index into the 3166-1 table, resolved at load.
    pub country: Option<usize>,
    /// `remove_accents(name.lower())`, for the fuzzy substring pass.
    pub folded_name: String,
}

/// The 3166-2 table.
///
/// `pycountry` indexes only `code` here, plus a `country_code` index mapping
/// to a *set*. Names are deliberately not indexed upstream, because they are
/// not unique -- there is a `"Central"` region in a dozen countries. This
/// carries name maps anyway, since resolving a state name is the whole point
/// of the subdivision expressions, and the expressions take a country to scope
/// them by.
pub struct SubdivisionTable {
    pub records: Vec<Subdivision>,
    /// Lowercased full code -> record.
    by_code: HashMap<String, usize>,
    /// Lowercased country alpha-2 -> its records, in file order.
    by_country: HashMap<String, Vec<usize>>,
    /// Lowercased name -> records, in file order.
    by_name: HashMap<String, Vec<usize>>,
    /// Accent-folded name -> records, in file order.
    by_folded_name: HashMap<String, Vec<usize>>,
    /// `search_fuzzy`'s second priority, precomputed: a folded field value
    /// mapped to the countries it hits and how many `(record, field)` pairs
    /// hit them. `pycountry` scores 49 points *per pair*, so the multiplicity
    /// has to survive.
    exact_fields: HashMap<String, Vec<(usize, u32)>>,
}

impl SubdivisionTable {
    fn build(records: Vec<Map<String, Value>>, countries: &CodeTable) -> Result<Self, String> {
        let mut parsed: Vec<Subdivision> = Vec::with_capacity(records.len());
        // Field values per record, in `pycountry`'s `_fields` order, for the
        // exact-match pass below.
        let mut per_record_fields: Vec<Vec<String>> = Vec::with_capacity(records.len());

        for (i, record) in records.iter().enumerate() {
            let Some(code) = record.get("code").and_then(Value::as_str) else {
                return Err(format!("{}: record {i} has no \"code\"", SPEC_3166_2.file));
            };
            let name = record.get("name").and_then(Value::as_str).unwrap_or_default();
            let kind = record.get("type").and_then(Value::as_str).unwrap_or_default();
            let parent = record.get("parent").and_then(Value::as_str);

            let country_code = code.split('-').next().unwrap_or(code).to_owned();
            // `pycountry` prefixes a bare parent with the country code, so
            // `parent: "AN"` under `CL-AN` becomes `CL-AN`.
            let parent_code = parent.map(|p| {
                if p.split('-').next() == Some(country_code.as_str()) {
                    p.to_owned()
                } else {
                    format!("{country_code}-{p}")
                }
            });

            // `Subdivisions.match` scans every value in `_fields`, which for a
            // subdivision is code, name, type, the raw parent when present,
            // the normalized parent_code, and the derived country_code.
            let mut fields: Vec<String> = vec![code.to_owned(), name.to_owned(), kind.to_owned()];
            if let Some(p) = parent {
                fields.push(p.to_owned());
            }
            if let Some(p) = &parent_code {
                fields.push(p.clone());
            }
            fields.push(country_code.clone());
            per_record_fields.push(fields);

            parsed.push(Subdivision {
                folded_name: fold(name),
                code: code.to_owned(),
                name: name.to_owned(),
                kind: kind.to_owned(),
                parent_code,
                country: countries.get("alpha_2", &country_code),
                country_code,
            });
        }

        let mut by_code = HashMap::with_capacity(parsed.len());
        let mut by_country: HashMap<String, Vec<usize>> = HashMap::new();
        let mut by_name: HashMap<String, Vec<usize>> = HashMap::new();
        let mut by_folded_name: HashMap<String, Vec<usize>> = HashMap::new();
        for (r, sub) in parsed.iter().enumerate() {
            by_code.insert(lower(&sub.code).into_owned(), r);
            by_country.entry(lower(&sub.country_code).into_owned()).or_default().push(r);
            by_name.entry(lower(&sub.name).into_owned()).or_default().push(r);
            by_folded_name.entry(sub.folded_name.clone()).or_default().push(r);
        }

        // Build the fuzzy exact-match index. Within one field, `pycountry`
        // stops at the first `;`-separated alternative that matches, so a
        // field contributes at most one hit however many of its alternatives
        // equal the query -- hence the per-field dedup.
        let mut hits: HashMap<String, Vec<usize>> = HashMap::new();
        for (r, fields) in per_record_fields.iter().enumerate() {
            let Some(country) = parsed[r].country else {
                continue;
            };
            for value in fields {
                let folded = fold(value);
                let mut seen: Vec<&str> = Vec::new();
                for part in folded.split(';') {
                    if seen.contains(&part) {
                        continue;
                    }
                    seen.push(part);
                    hits.entry(part.to_owned()).or_default().push(country);
                }
            }
        }
        let exact_fields = hits
            .into_iter()
            .map(|(key, mut countries)| {
                countries.sort_unstable();
                let mut runs: Vec<(usize, u32)> = Vec::new();
                for c in countries {
                    match runs.last_mut() {
                        Some(last) if last.0 == c => last.1 += 1,
                        _ => runs.push((c, 1)),
                    }
                }
                (key, runs)
            })
            .collect();

        Ok(Self { records: parsed, by_code, by_country, by_name, by_folded_name, exact_fields })
    }

    /// Number of records.
    pub fn len(&self) -> usize {
        self.records.len()
    }

    /// Exact, case-insensitive match on the full code, e.g. `"us-ca"`.
    pub fn by_code(&self, code: &str) -> Option<usize> {
        self.by_code.get(lower(code).as_ref()).copied()
    }

    /// Every record belonging to a country, in file order.
    pub fn in_country(&self, alpha_2: &str) -> &[usize] {
        self.by_country.get(lower(alpha_2).as_ref()).map_or(&[], Vec::as_slice)
    }

    /// Records whose name matches case-insensitively, in file order.
    pub fn by_name(&self, name: &str) -> &[usize] {
        self.by_name.get(lower(name).as_ref()).map_or(&[], Vec::as_slice)
    }

    /// Records whose accent-folded name matches, in file order.
    pub fn by_folded_name(&self, folded: &str) -> &[usize] {
        self.by_folded_name.get(folded).map_or(&[], Vec::as_slice)
    }

    /// `search_fuzzy`'s priority-2 hits: `(country, pair count)` for a query.
    pub fn exact_field_hits(&self, folded_query: &str) -> &[(usize, u32)] {
        self.exact_fields.get(folded_query).map_or(&[], Vec::as_slice)
    }
}

// =============================================================================
// The database
// =============================================================================

/// Every table, plus the upstream release they were taken from.
pub struct Db {
    /// ISO 3166-1: current countries.
    pub countries: CodeTable,
    /// ISO 3166-3: countries withdrawn from the standard.
    pub historic: CodeTable,
    /// ISO 3166-2: subdivisions.
    pub subdivisions: SubdivisionTable,
    /// ISO 4217: currencies.
    pub currencies: CodeTable,
    /// The `iso-codes` release stamp, or `"unknown"`.
    pub version: String,
}

impl Db {
    /// Build from the four table texts and an optional version stamp.
    ///
    /// # Errors
    ///
    /// Returns a message naming the table and the reason it is unusable.
    pub fn from_texts(
        iso3166_1: &str,
        iso3166_2: &str,
        iso3166_3: &str,
        iso4217: &str,
        version: Option<&str>,
    ) -> Result<Self, String> {
        let countries = CodeTable::build(parse_records(iso3166_1, &SPEC_3166_1)?);
        let historic = CodeTable::build(parse_records(iso3166_3, &SPEC_3166_3)?);
        let currencies = CodeTable::build(parse_records(iso4217, &SPEC_4217)?);
        let subdivisions =
            SubdivisionTable::build(parse_records(iso3166_2, &SPEC_3166_2)?, &countries)?;

        // A country table with no alpha-2 codes is not a country table, and
        // every expression here keys off them. Catching it at load turns a
        // silently all-null column into a loud error.
        if countries.lookup("US").is_none() && countries.lookup("us").is_none() {
            return Err("the 3166-1 table has no entry reachable as \"US\"; it does not \
                 look like an ISO 3166-1 country table"
                .to_owned());
        }

        Ok(Self {
            countries,
            historic,
            subdivisions,
            currencies,
            version: version.map_or_else(|| "unknown".to_owned(), |v| v.trim().to_owned()),
        })
    }

    /// Build from the snapshot compiled into this binary.
    fn bundled() -> Result<Self, String> {
        Self::from_texts(
            BUNDLED_3166_1,
            BUNDLED_3166_2,
            BUNDLED_3166_3,
            BUNDLED_4217,
            Some(BUNDLED_VERSION),
        )
    }

    /// Build from a directory holding the four table files.
    ///
    /// # Errors
    ///
    /// Returns a message if a file is missing or unreadable, or if any table
    /// fails to parse. Nothing is built until every file has been read, so a
    /// half-populated directory cannot produce a half-loaded database.
    pub fn from_dir(dir: &Path) -> Result<Self, String> {
        let read = |spec: &TableSpec| -> Result<String, String> {
            let path: PathBuf = dir.join(spec.file);
            std::fs::read_to_string(&path)
                .map_err(|e| format!("could not read {}: {e}", path.display()))
        };
        let iso3166_1 = read(&SPEC_3166_1)?;
        let iso3166_2 = read(&SPEC_3166_2)?;
        let iso3166_3 = read(&SPEC_3166_3)?;
        let iso4217 = read(&SPEC_4217)?;
        // The stamp is informational, so its absence is not an error -- the
        // tables are still perfectly usable, just unlabelled.
        let version = std::fs::read_to_string(dir.join(VERSION_FILE)).ok();
        Self::from_texts(&iso3166_1, &iso3166_2, &iso3166_3, &iso4217, version.as_deref())
    }
}

static CURRENT: OnceLock<RwLock<Arc<Db>>> = OnceLock::new();

/// The slot holding the live database, initialized from the environment
/// override or the bundled snapshot on first access.
///
/// # Panics
///
/// Panics if `POLARS_COUNTRY_DATA` is set but the directory it names is
/// unusable. That is deliberate: quietly falling back to the bundled snapshot
/// would produce the wrong codes with no signal, and there is no caller to
/// return an error to on a lazily-initialized static.
fn slot() -> &'static RwLock<Arc<Db>> {
    CURRENT.get_or_init(|| {
        let initial = match std::env::var(DATA_PATH_ENV) {
            Ok(dir) if !dir.trim().is_empty() => {
                Db::from_dir(Path::new(&dir)).unwrap_or_else(|e| {
                    panic!("{DATA_PATH_ENV} is set to {dir:?} but it is not usable: {e}")
                })
            },
            _ => Db::bundled().expect("bundled ISO tables failed to parse"),
        };
        RwLock::new(Arc::new(initial))
    })
}

/// A snapshot of the database in use.
///
/// Callers hold the [`Arc`] for the duration of one operation -- an expression
/// evaluation takes it once and shares it across the rayon fan-out -- so every
/// row of one query resolves against the same tables even if another thread
/// swaps them mid-flight.
pub fn current() -> Arc<Db> {
    Arc::clone(&slot().read().expect("ISO table lock poisoned"))
}

/// Replace the live database with one read from `dir`, returning its version.
///
/// Everything is parsed *before* anything is swapped, so a bad directory
/// leaves the process running on the tables it already had.
///
/// # Errors
///
/// Returns the failure from [`Db::from_dir`], unchanged.
pub fn load_from_dir(dir: &str) -> Result<String, String> {
    let db = Db::from_dir(Path::new(dir))?;
    let version = db.version.clone();
    *slot().write().expect("ISO table lock poisoned") = Arc::new(db);
    Ok(version)
}

/// The `iso-codes` release stamp of the tables in use, for provenance.
pub fn version() -> String {
    current().version.clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_bundled_tables_parse_and_are_versioned() {
        let db = current();
        assert!(db.countries.len() >= 200, "{}", db.countries.len());
        assert!(db.subdivisions.len() >= 4_000, "{}", db.subdivisions.len());
        assert!(db.historic.len() >= 20, "{}", db.historic.len());
        assert!(db.currencies.len() >= 150, "{}", db.currencies.len());
        assert_ne!(db.version, "unknown");
    }

    #[test]
    fn field_order_matches_the_upstream_file() {
        // `lookup` probes the indices in this order and takes the first hit,
        // so the order is observable behavior rather than an implementation
        // detail. If upstream reshuffles its keys, this is where it shows up.
        let db = current();
        assert_eq!(
            db.countries.fields,
            ["alpha_2", "alpha_3", "flag", "name", "numeric", "official_name", "common_name"]
        );
        assert_eq!(
            db.historic.fields,
            ["alpha_2", "alpha_3", "alpha_4", "name", "numeric", "withdrawal_date", "comment"]
        );
        assert_eq!(db.currencies.fields, ["alpha_3", "name", "numeric"]);
    }

    #[test]
    fn lookup_is_case_insensitive_across_every_field() {
        let db = current();
        for probe in ["US", "us", "USA", "united states", "840", "\u{1f1fa}\u{1f1f8}"] {
            let (record, _) = db.countries.lookup(probe).unwrap_or_else(|| {
                panic!("{probe:?} should resolve");
            });
            assert_eq!(db.countries.alpha_2(record), Some("US"), "{probe:?}");
        }
        assert_eq!(db.countries.lookup("Republic of Korea"), None);
    }

    #[test]
    fn lookup_reports_the_field_that_matched() {
        let db = current();
        assert_eq!(db.countries.lookup("DE").map(|(_, f)| f), Some("alpha_2"));
        assert_eq!(db.countries.lookup("DEU").map(|(_, f)| f), Some("alpha_3"));
        assert_eq!(db.countries.lookup("276").map(|(_, f)| f), Some("numeric"));
        assert_eq!(db.countries.lookup("Germany").map(|(_, f)| f), Some("name"));
    }

    #[test]
    fn subdivision_parents_carry_the_country_prefix() {
        let db = current();
        for sub in &db.subdivisions.records {
            if let Some(parent) = &sub.parent_code {
                assert!(parent.starts_with(&sub.country_code), "{} has parent {parent}", sub.code);
            }
        }
    }

    #[test]
    fn every_subdivision_resolves_to_a_country() {
        let db = current();
        let orphans: Vec<&str> = db
            .subdivisions
            .records
            .iter()
            .filter(|s| s.country.is_none())
            .map(|s| s.code.as_str())
            .collect();
        assert!(orphans.is_empty(), "subdivisions with no 3166-1 country: {orphans:?}");
    }

    #[test]
    fn a_table_that_is_too_small_is_rejected() {
        let tiny = r#"{"3166-1": [{"alpha_2": "US", "name": "United States"}]}"#;
        let Err(err) = Db::from_texts(tiny, BUNDLED_3166_2, BUNDLED_3166_3, BUNDLED_4217, None)
        else {
            panic!("a one-record country table must be rejected")
        };
        assert!(err.contains("fewer than"), "{err}");
    }

    #[test]
    fn a_table_with_the_wrong_root_key_is_rejected() {
        let Err(err) = Db::from_texts(
            r#"{"countries": []}"#,
            BUNDLED_3166_2,
            BUNDLED_3166_3,
            BUNDLED_4217,
            None,
        ) else {
            panic!("a table with no 3166-1 array must be rejected")
        };
        assert!(err.contains("3166-1"), "{err}");
    }
}
