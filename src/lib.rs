//! Polars expression plugin exposing `pycountry`-compatible ISO code lookup.
//!
//! The compiled cdylib serves two masters:
//!
//! * Polars `dlopen`s it and calls the C-ABI symbols emitted by
//!   `#[polars_expr]`. That is the vectorized path.
//! * Python imports it as `polars_country._internal` for the scalar helpers,
//!   which exist so non-DataFrame callers do not have to round-trip through a
//!   one-row frame.
//!
//! Both go through [`resolve`], so there is exactly one implementation of the
//! matching rules.

mod db;
mod fold;
mod fuzzy;
mod resolve;

use polars::prelude::*;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3_polars::derive::polars_expr;
use rayon::prelude::*;
use serde::Deserialize;

use crate::db::{CodeTable, Db, Subdivision};
use crate::resolve::{CountryResolver, SubdivisionResolver};

/// Below this many rows, fanning out costs more than it saves.
///
/// It is also deliberately above the streaming engine's morsel size (~50k), so
/// when Polars is already calling this plugin from several of its own threads
/// each call stays single-threaded instead of nesting a rayon fan-out inside
/// it.
const PARALLEL_THRESHOLD: usize = 100_000;

/// Keyword arguments for the country expressions, pickled across by Polars.
#[derive(Deserialize)]
struct CountryKwargs {
    /// Whether an exact miss may fall back to `search_fuzzy`.
    fuzzy: bool,
    /// Whether ISO 3166-3 (withdrawn countries) is consulted after 3166-1.
    historic: bool,
    /// Whether to fan the column out across rayon threads.
    parallel: bool,
}

/// Keyword arguments for the subdivision expressions.
#[derive(Deserialize)]
struct SubdivisionKwargs {
    fuzzy: bool,
    parallel: bool,
}

/// Keyword arguments for the currency expressions, which have no fuzzy path.
#[derive(Deserialize)]
struct CurrencyKwargs {
    parallel: bool,
}

// =============================================================================
// Parallel fan-out
// =============================================================================

/// Column-shaped output that can be concatenated back together after a
/// parallel split.
trait Concat: Sized {
    fn concat(&mut self, other: &Self) -> PolarsResult<()>;
}

impl Concat for StringChunked {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.append(other)
    }
}

impl Concat for BooleanChunked {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.append(other)
    }
}

impl Concat for UInt32Chunked {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.append(other)
    }
}

/// Several string columns built in one pass, e.g. the fields of a struct.
struct StringColumns(Vec<StringChunked>);

impl Concat for StringColumns {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        for (mine, theirs) in self.0.iter_mut().zip(&other.0) {
            mine.append(theirs)?;
        }
        Ok(())
    }
}

impl StringColumns {
    /// Wrap the columns into a struct Series named `name`.
    fn into_struct(self, name: PlSmallStr) -> PolarsResult<Series> {
        let len = self.0.first().map_or(0, ChunkedArray::len);
        let fields: Vec<Series> = self.0.into_iter().map(IntoSeries::into_series).collect();
        Ok(StructChunked::from_series(name, len, fields.iter())?.into_series())
    }
}

/// Run `build` over one column, fanning out across rayon threads when the
/// column is large enough to be worth it.
///
/// Order is preserved: each thread owns one contiguous index range and the
/// pieces are concatenated back in order.
fn build_column<T, F>(ca: &StringChunked, parallel: bool, build: F) -> PolarsResult<T>
where
    T: Concat + Send,
    F: Fn(&StringChunked) -> T + Sync,
{
    build_column2(ca, None, parallel, |values, _| build(values))
}

/// Run `build` over a value column and an optional scope column.
///
/// The scope column is either the same length as the values or a broadcast
/// literal of length 1; slicing keeps that relationship, so the callback can
/// rely on it.
fn build_column2<T, F>(
    ca: &StringChunked,
    scope: Option<&StringChunked>,
    parallel: bool,
    build: F,
) -> PolarsResult<T>
where
    T: Concat + Send,
    F: Fn(&StringChunked, Option<&StringChunked>) -> T + Sync,
{
    if !parallel || ca.len() < PARALLEL_THRESHOLD {
        return Ok(build(ca, scope));
    }
    let threads = rayon::current_num_threads().max(1);
    let stride = ca.len().div_ceil(threads);
    let mut pieces: Vec<T> = (0..threads)
        .into_par_iter()
        .filter_map(|i| {
            let offset = i * stride;
            (offset < ca.len()).then(|| {
                let len = stride.min(ca.len() - offset);
                // A broadcast literal applies to every row, so it is passed
                // through whole rather than sliced.
                let piece = scope.map(|s| {
                    if s.len() == ca.len() {
                        s.slice(offset as i64, len)
                    } else {
                        s.clone()
                    }
                });
                build(&ca.slice(offset as i64, len), piece.as_ref())
            })
        })
        .collect();

    let mut out = pieces.remove(0);
    for piece in &pieces {
        out.concat(piece)?;
    }
    Ok(out)
}

/// Read the scope value for row `i`, honoring a broadcast literal.
fn scope_at(scope: Option<&StringChunked>, rows: usize, i: usize) -> Option<&str> {
    let scope = scope?;
    if scope.len() == rows {
        scope.get(i)
    } else {
        scope.get(0)
    }
}

/// The scope column, rechunked so `get` is a constant-time probe.
///
/// A column arriving in many chunks would otherwise make every row's lookup
/// walk the chunk list.
fn scope_column(inputs: &[Series]) -> PolarsResult<Option<StringChunked>> {
    match inputs.get(1) {
        None => Ok(None),
        // `rechunk` borrows when there is already one chunk; the clone that
        // `into_owned` then makes is of Arc'd buffers, not of the data.
        Some(series) => Ok(Some(series.str()?.rechunk().into_owned())),
    }
}

// =============================================================================
// Countries
// =============================================================================

/// One field of a country record, as the struct output exposes it.
type CountryGetter = for<'a> fn(&'a CodeTable, usize) -> Option<&'a str>;

/// The string fields of `country_extract`, in output order.
///
/// `alpha_4` and `withdrawal_date` exist only in ISO 3166-3, so they are
/// always null unless `historic` is on and the match came from there. They are
/// carried in every case so the struct's schema does not depend on a keyword.
const COUNTRY_FIELDS: [(&str, CountryGetter); 8] = [
    ("alpha_2", CodeTable::alpha_2),
    ("alpha_3", CodeTable::alpha_3),
    ("alpha_4", CodeTable::alpha_4),
    ("numeric", CodeTable::numeric),
    ("name", CodeTable::name),
    ("official_name", CodeTable::official_name),
    ("common_name", CodeTable::common_name),
    ("flag", CodeTable::flag),
];

/// `country_extract`'s columns: every ISO field, plus the historic flag.
struct CountryColumns {
    strings: StringColumns,
    withdrawal_date: StringChunked,
    historic: BooleanChunked,
}

impl Concat for CountryColumns {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.strings.concat(&other.strings)?;
        self.withdrawal_date.append(&other.withdrawal_date)?;
        self.historic.append(&other.historic)
    }
}

fn build_country_extract(db: &Db, ca: &StringChunked, kwargs: &CountryKwargs) -> CountryColumns {
    let mut resolver = CountryResolver::new(db, kwargs.fuzzy, kwargs.historic);
    let mut strings: Vec<StringChunkedBuilder> = COUNTRY_FIELDS
        .iter()
        .map(|(name, _)| StringChunkedBuilder::new((*name).into(), ca.len()))
        .collect();
    let mut withdrawal_date = StringChunkedBuilder::new("withdrawal_date".into(), ca.len());
    let mut historic = BooleanChunkedBuilder::new("historic".into(), ca.len());

    for opt in ca.iter() {
        match opt.and_then(|value| resolver.resolve(value)) {
            None => {
                for builder in &mut strings {
                    builder.append_null();
                }
                withdrawal_date.append_null();
                historic.append_null();
            },
            Some(matched) => {
                for (builder, (_, get)) in strings.iter_mut().zip(COUNTRY_FIELDS) {
                    builder.append_option(get(matched.table, matched.record));
                }
                withdrawal_date.append_option(matched.table.withdrawal_date(matched.record));
                historic.append_value(matched.historic);
            },
        }
    }

    CountryColumns {
        strings: StringColumns(strings.into_iter().map(StringChunkedBuilder::finish).collect()),
        withdrawal_date: withdrawal_date.finish(),
        historic: historic.finish(),
    }
}

fn country_extract_output(_: &[Field]) -> PolarsResult<Field> {
    let mut fields: Vec<Field> = COUNTRY_FIELDS
        .iter()
        .map(|(name, _)| Field::new((*name).into(), DataType::String))
        .collect();
    fields.push(Field::new("withdrawal_date".into(), DataType::String));
    fields.push(Field::new("historic".into(), DataType::Boolean));
    Ok(Field::new("country".into(), DataType::Struct(fields)))
}

/// Every ISO field of the matched country, as one struct.
#[polars_expr(output_type_func=country_extract_output)]
fn country_extract(inputs: &[Series], kwargs: CountryKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let db = db::current();
    let out = build_column(ca, kwargs.parallel, |c| build_country_extract(&db, c, &kwargs))?;
    let len = out.historic.len();
    let mut fields: Vec<Series> = out.strings.0.into_iter().map(IntoSeries::into_series).collect();
    fields.push(out.withdrawal_date.into_series());
    fields.push(out.historic.into_series());
    Ok(StructChunked::from_series(ca.name().clone(), len, fields.iter())?.into_series())
}

/// Build one string field of the country record.
fn build_country_field(
    db: &Db,
    ca: &StringChunked,
    kwargs: &CountryKwargs,
    get: CountryGetter,
) -> StringChunked {
    let mut resolver = CountryResolver::new(db, kwargs.fuzzy, kwargs.historic);
    let mut builder = StringChunkedBuilder::new(ca.name().clone(), ca.len());
    for opt in ca.iter() {
        builder.append_option(
            opt.and_then(|value| resolver.resolve(value)).and_then(|m| get(m.table, m.record)),
        );
    }
    builder.finish()
}

/// Define one single-field country expression.
macro_rules! country_field_expr {
    ($fn_name:ident, $getter:path, $doc:expr) => {
        #[doc = $doc]
        #[polars_expr(output_type=String)]
        fn $fn_name(inputs: &[Series], kwargs: CountryKwargs) -> PolarsResult<Series> {
            let ca = inputs[0].str()?;
            let db = db::current();
            let out = build_column(ca, kwargs.parallel, |c| {
                build_country_field(&db, c, &kwargs, $getter)
            })?;
            Ok(out.into_series())
        }
    };
}

country_field_expr!(country_alpha_2, CodeTable::alpha_2, "The ISO 3166-1 alpha-2 code, e.g. `US`.");
country_field_expr!(country_alpha_3, CodeTable::alpha_3, "The alpha-3 code, e.g. `USA`.");
country_field_expr!(
    country_numeric,
    CodeTable::numeric,
    "The numeric code, zero-padded, e.g. `840`."
);
country_field_expr!(country_name, CodeTable::name, "The short name, e.g. `United States`.");
country_field_expr!(
    country_official_name,
    CodeTable::official_name,
    "The official name, where one differs."
);
country_field_expr!(
    country_common_name,
    CodeTable::common_name,
    "The common name, where one differs."
);
country_field_expr!(country_flag, CodeTable::flag, "The regional-indicator flag emoji.");

/// `country_match`'s diagnostic columns.
struct MatchColumns {
    alpha_2: StringChunked,
    matched_on: StringChunked,
    score: UInt32Chunked,
    historic: BooleanChunked,
}

impl Concat for MatchColumns {
    fn concat(&mut self, other: &Self) -> PolarsResult<()> {
        self.alpha_2.append(&other.alpha_2)?;
        self.matched_on.append(&other.matched_on)?;
        self.score.append(&other.score)?;
        self.historic.append(&other.historic)
    }
}

fn build_country_match(db: &Db, ca: &StringChunked, kwargs: &CountryKwargs) -> MatchColumns {
    let mut resolver = CountryResolver::new(db, kwargs.fuzzy, kwargs.historic);
    let mut alpha_2 = StringChunkedBuilder::new("alpha_2".into(), ca.len());
    let mut matched_on = StringChunkedBuilder::new("matched_on".into(), ca.len());
    let mut score = PrimitiveChunkedBuilder::<UInt32Type>::new("score".into(), ca.len());
    let mut historic = BooleanChunkedBuilder::new("historic".into(), ca.len());

    for opt in ca.iter() {
        match opt.and_then(|value| resolver.resolve(value)) {
            None => {
                alpha_2.append_null();
                matched_on.append_null();
                score.append_null();
                historic.append_null();
            },
            Some(matched) => {
                alpha_2.append_option(matched.alpha_2());
                matched_on.append_value(matched.matched_on);
                score.append_option(matched.score);
                historic.append_value(matched.historic);
            },
        }
    }

    MatchColumns {
        alpha_2: alpha_2.finish(),
        matched_on: matched_on.finish(),
        score: score.finish(),
        historic: historic.finish(),
    }
}

fn country_match_output(_: &[Field]) -> PolarsResult<Field> {
    Ok(Field::new(
        "country_match".into(),
        DataType::Struct(vec![
            Field::new("alpha_2".into(), DataType::String),
            Field::new("matched_on".into(), DataType::String),
            Field::new("score".into(), DataType::UInt32),
            Field::new("historic".into(), DataType::Boolean),
        ]),
    ))
}

/// How each value resolved: the code, the field that matched, and the score.
#[polars_expr(output_type_func=country_match_output)]
fn country_match(inputs: &[Series], kwargs: CountryKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let db = db::current();
    let out = build_column(ca, kwargs.parallel, |c| build_country_match(&db, c, &kwargs))?;
    let len = out.alpha_2.len();
    let fields = [
        out.alpha_2.into_series(),
        out.matched_on.into_series(),
        out.score.into_series(),
        out.historic.into_series(),
    ];
    Ok(StructChunked::from_series(ca.name().clone(), len, fields.iter())?.into_series())
}

// =============================================================================
// Currencies
// =============================================================================

const CURRENCY_FIELDS: [(&str, CountryGetter); 3] =
    [("alpha_3", CodeTable::alpha_3), ("numeric", CodeTable::numeric), ("name", CodeTable::name)];

fn build_currency_extract(db: &Db, ca: &StringChunked) -> StringColumns {
    let mut builders: Vec<StringChunkedBuilder> = CURRENCY_FIELDS
        .iter()
        .map(|(name, _)| StringChunkedBuilder::new((*name).into(), ca.len()))
        .collect();
    for opt in ca.iter() {
        let hit = opt.and_then(|value| resolve::resolve_currency(db, value));
        for (builder, (_, get)) in builders.iter_mut().zip(CURRENCY_FIELDS) {
            builder.append_option(hit.and_then(|(record, _)| get(&db.currencies, record)));
        }
    }
    StringColumns(builders.into_iter().map(StringChunkedBuilder::finish).collect())
}

fn currency_extract_output(_: &[Field]) -> PolarsResult<Field> {
    Ok(Field::new(
        "currency".into(),
        DataType::Struct(
            CURRENCY_FIELDS
                .iter()
                .map(|(name, _)| Field::new((*name).into(), DataType::String))
                .collect(),
        ),
    ))
}

/// The matched ISO 4217 currency, as one struct.
#[polars_expr(output_type_func=currency_extract_output)]
fn currency_extract(inputs: &[Series], kwargs: CurrencyKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let db = db::current();
    let out = build_column(ca, kwargs.parallel, |c| build_currency_extract(&db, c))?;
    out.into_struct(ca.name().clone())
}

fn build_currency_field(db: &Db, ca: &StringChunked, get: CountryGetter) -> StringChunked {
    let mut builder = StringChunkedBuilder::new(ca.name().clone(), ca.len());
    for opt in ca.iter() {
        builder.append_option(
            opt.and_then(|value| resolve::resolve_currency(db, value))
                .and_then(|(record, _)| get(&db.currencies, record)),
        );
    }
    builder.finish()
}

macro_rules! currency_field_expr {
    ($fn_name:ident, $getter:path, $doc:expr) => {
        #[doc = $doc]
        #[polars_expr(output_type=String)]
        fn $fn_name(inputs: &[Series], kwargs: CurrencyKwargs) -> PolarsResult<Series> {
            let ca = inputs[0].str()?;
            let db = db::current();
            let out = build_column(ca, kwargs.parallel, |c| build_currency_field(&db, c, $getter))?;
            Ok(out.into_series())
        }
    };
}

currency_field_expr!(
    currency_alpha_3,
    CodeTable::alpha_3,
    "The ISO 4217 alpha-3 code, e.g. `USD`."
);
currency_field_expr!(
    currency_numeric,
    CodeTable::numeric,
    "The ISO 4217 numeric code, e.g. `840`."
);
currency_field_expr!(currency_name, CodeTable::name, "The currency's name, e.g. `US Dollar`.");

// =============================================================================
// Subdivisions
// =============================================================================

/// One field of a subdivision record.
type SubdivisionGetter = for<'a> fn(&'a Subdivision) -> Option<&'a str>;

fn sub_code(s: &Subdivision) -> Option<&str> {
    Some(s.code.as_str())
}
fn sub_name(s: &Subdivision) -> Option<&str> {
    Some(s.name.as_str())
}
fn sub_type(s: &Subdivision) -> Option<&str> {
    Some(s.kind.as_str())
}
fn sub_country(s: &Subdivision) -> Option<&str> {
    Some(s.country_code.as_str())
}
fn sub_parent_code(s: &Subdivision) -> Option<&str> {
    s.parent_code.as_deref()
}

const SUBDIVISION_FIELDS: [(&str, SubdivisionGetter); 5] = [
    ("code", sub_code),
    ("name", sub_name),
    ("type", sub_type),
    ("country_code", sub_country),
    ("parent_code", sub_parent_code),
];

fn build_subdivision_extract(
    db: &Db,
    ca: &StringChunked,
    scope: Option<&StringChunked>,
    kwargs: &SubdivisionKwargs,
) -> StringColumns {
    let mut resolver = SubdivisionResolver::new(db, kwargs.fuzzy);
    let mut builders: Vec<StringChunkedBuilder> = SUBDIVISION_FIELDS
        .iter()
        .map(|(name, _)| StringChunkedBuilder::new((*name).into(), ca.len()))
        .collect();

    for (i, opt) in ca.iter().enumerate() {
        let hit = opt.and_then(|value| resolver.resolve(value, scope_at(scope, ca.len(), i)));
        for (builder, (_, get)) in builders.iter_mut().zip(SUBDIVISION_FIELDS) {
            builder.append_option(hit.and_then(|r| get(&db.subdivisions.records[r])));
        }
    }
    StringColumns(builders.into_iter().map(StringChunkedBuilder::finish).collect())
}

fn subdivision_extract_output(_: &[Field]) -> PolarsResult<Field> {
    Ok(Field::new(
        "subdivision".into(),
        DataType::Struct(
            SUBDIVISION_FIELDS
                .iter()
                .map(|(name, _)| Field::new((*name).into(), DataType::String))
                .collect(),
        ),
    ))
}

/// The matched ISO 3166-2 subdivision, as one struct.
#[polars_expr(output_type_func=subdivision_extract_output)]
fn subdivision_extract(inputs: &[Series], kwargs: SubdivisionKwargs) -> PolarsResult<Series> {
    let ca = inputs[0].str()?;
    let scope = scope_column(inputs)?;
    let db = db::current();
    let out = build_column2(ca, scope.as_ref(), kwargs.parallel, |values, piece| {
        build_subdivision_extract(&db, values, piece, &kwargs)
    })?;
    out.into_struct(ca.name().clone())
}

fn build_subdivision_field(
    db: &Db,
    ca: &StringChunked,
    scope: Option<&StringChunked>,
    kwargs: &SubdivisionKwargs,
    get: SubdivisionGetter,
) -> StringChunked {
    let mut resolver = SubdivisionResolver::new(db, kwargs.fuzzy);
    let mut builder = StringChunkedBuilder::new(ca.name().clone(), ca.len());
    for (i, opt) in ca.iter().enumerate() {
        builder.append_option(
            opt.and_then(|value| resolver.resolve(value, scope_at(scope, ca.len(), i)))
                .and_then(|r| get(&db.subdivisions.records[r])),
        );
    }
    builder.finish()
}

macro_rules! subdivision_field_expr {
    ($fn_name:ident, $getter:path, $doc:expr) => {
        #[doc = $doc]
        #[polars_expr(output_type=String)]
        fn $fn_name(inputs: &[Series], kwargs: SubdivisionKwargs) -> PolarsResult<Series> {
            let ca = inputs[0].str()?;
            let scope = scope_column(inputs)?;
            let db = db::current();
            let out = build_column2(ca, scope.as_ref(), kwargs.parallel, |values, piece| {
                build_subdivision_field(&db, values, piece, &kwargs, $getter)
            })?;
            Ok(out.into_series())
        }
    };
}

subdivision_field_expr!(subdivision_code, sub_code, "The ISO 3166-2 code, e.g. `US-CA`.");
subdivision_field_expr!(subdivision_name, sub_name, "The subdivision's name, e.g. `California`.");
subdivision_field_expr!(subdivision_type, sub_type, "The subdivision's type, e.g. `State`.");
subdivision_field_expr!(
    subdivision_country,
    sub_country,
    "The alpha-2 code of the country it belongs to."
);
subdivision_field_expr!(
    subdivision_parent_code,
    sub_parent_code,
    "The parent subdivision's code, where there is one."
);

// =============================================================================
// Scalar Python API
// =============================================================================

/// Build the dict a scalar country lookup returns.
fn country_dict<'py>(
    py: Python<'py>,
    matched: &resolve::CountryMatch<'_>,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (name, get) in COUNTRY_FIELDS {
        dict.set_item(name, get(matched.table, matched.record))?;
    }
    dict.set_item("withdrawal_date", matched.table.withdrawal_date(matched.record))?;
    dict.set_item("historic", matched.historic)?;
    dict.set_item("matched_on", matched.matched_on)?;
    dict.set_item("score", matched.score)?;
    Ok(dict)
}

/// Resolve one country string, for callers that are not holding a DataFrame.
///
/// Returns `None` when nothing matched.
#[pyfunction]
#[pyo3(signature = (value, *, fuzzy = false, historic = false))]
fn lookup_country<'py>(
    py: Python<'py>,
    value: Option<&str>,
    fuzzy: bool,
    historic: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some(value) = value else {
        return Ok(None);
    };
    let db = db::current();
    let mut resolver = CountryResolver::new(&db, fuzzy, historic);
    resolver.resolve(value).map(|m| country_dict(py, &m)).transpose()
}

/// Rank every country the fuzzy search scores against `value`, best first.
///
/// This is `pycountry.countries.search_fuzzy` with the scores exposed, which
/// the reference keeps to itself.
#[pyfunction]
#[pyo3(signature = (value, *, limit = None))]
fn search_countries<'py>(
    py: Python<'py>,
    value: &str,
    limit: Option<usize>,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let db = db::current();
    if value.trim().is_empty() {
        return Ok(Vec::new());
    }
    let ranked = fuzzy::search(&db, &fold::fold_query(value));
    let take = limit.unwrap_or(ranked.len());
    ranked
        .into_iter()
        .take(take)
        .map(|scored| {
            let matched = resolve::CountryMatch {
                table: &db.countries,
                record: scored.record,
                matched_on: "fuzzy",
                score: Some(scored.points),
                historic: false,
            };
            country_dict(py, &matched)
        })
        .collect()
}

/// Resolve one subdivision string, optionally scoped to a country.
#[pyfunction]
#[pyo3(signature = (value, *, country = None, fuzzy = false))]
fn lookup_subdivision<'py>(
    py: Python<'py>,
    value: Option<&str>,
    country: Option<&str>,
    fuzzy: bool,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some(value) = value else {
        return Ok(None);
    };
    let db = db::current();
    let mut resolver = SubdivisionResolver::new(&db, fuzzy);
    let Some(record) = resolver.resolve(value, country) else {
        return Ok(None);
    };
    let sub = &db.subdivisions.records[record];
    let dict = PyDict::new(py);
    for (name, get) in SUBDIVISION_FIELDS {
        dict.set_item(name, get(sub))?;
    }
    Ok(Some(dict))
}

/// Resolve one currency string against ISO 4217.
#[pyfunction]
#[pyo3(signature = (value,))]
fn lookup_currency<'py>(
    py: Python<'py>,
    value: Option<&str>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some(value) = value else {
        return Ok(None);
    };
    let db = db::current();
    let Some((record, matched_on)) = resolve::resolve_currency(&db, value) else {
        return Ok(None);
    };
    let dict = PyDict::new(py);
    for (name, get) in CURRENCY_FIELDS {
        dict.set_item(name, get(&db.currencies, record))?;
    }
    dict.set_item("matched_on", matched_on)?;
    Ok(Some(dict))
}

/// The `iso-codes` release stamp of the tables actually in use.
#[pyfunction]
fn iso_version() -> String {
    db::version()
}

/// How many records each table holds, for provenance and smoke tests.
#[pyfunction]
fn table_sizes(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let db = db::current();
    let dict = PyDict::new(py);
    dict.set_item("3166-1", db.countries.len())?;
    dict.set_item("3166-2", db.subdivisions.len())?;
    dict.set_item("3166-3", db.historic.len())?;
    dict.set_item("4217", db.currencies.len())?;
    Ok(dict)
}

/// Replace the live tables with those in `path`, a directory of JSON files.
///
/// Returns the new `VERSION` stamp. Raises `ValueError` if the directory is
/// unusable, in which case the process keeps the tables it already had.
#[pyfunction]
fn load_iso_dir(path: &str) -> PyResult<String> {
    db::load_from_dir(path).map_err(PyValueError::new_err)
}

#[pymodule]
fn _internal(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(lookup_country, m)?)?;
    m.add_function(wrap_pyfunction!(search_countries, m)?)?;
    m.add_function(wrap_pyfunction!(lookup_subdivision, m)?)?;
    m.add_function(wrap_pyfunction!(lookup_currency, m)?)?;
    m.add_function(wrap_pyfunction!(iso_version, m)?)?;
    m.add_function(wrap_pyfunction!(table_sizes, m)?)?;
    m.add_function(wrap_pyfunction!(load_iso_dir, m)?)?;
    m.add("DATA_PATH_ENV", db::DATA_PATH_ENV)?;
    m.add("VERSION_FILE", db::VERSION_FILE)?;
    Ok(())
}
