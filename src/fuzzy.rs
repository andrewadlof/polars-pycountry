//! A port of `pycountry.ExistingCountries.search_fuzzy`.
//!
//! The reference implementation scores every country against the query and
//! returns them ranked. Four passes contribute points, and a country
//! accumulates across all of them:
//!
//! | pass | what it matches                                 | points          |
//! |------|-------------------------------------------------|-----------------|
//! | 1    | an exact `lookup()` hit                         | 50              |
//! | 2    | a subdivision field equal to the query          | 49 *per hit*    |
//! | 3    | a country name containing the query, or its initials | 40, or 30 - 2*offset (min 5) |
//! | 4    | a subdivision name containing the query         | 5 - offset (min 1) |
//!
//! Ties break on alpha-2, ascending, so the ranking is total and stable.
//!
//! Two of these are surprising and are reproduced deliberately, because
//! `pycountry` compatibility is the point:
//!
//! * Pass 2 scans **every** field of a subdivision -- its code, its type, and
//!   its country code included -- so `"state"` scores 49 for each of the
//!   hundreds of subdivisions typed `State`, and `"us"` scores 49 for each of
//!   the 57 US ones.
//! * Pass 3 checks `name`, `official_name` and `comment`, but never
//!   `common_name`, despite the upstream comment claiming otherwise.
//!
//! What is *not* reproduced is the empty query. `search_fuzzy("")` matches
//! every country at offset 0 and ranks Andorra first; the expressions here
//! return null for blank input instead. See [`crate::resolve`].

use crate::db::Db;
use crate::fold::char_find;

/// One country and the points it accumulated.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Scored {
    /// Index into the 3166-1 table.
    pub record: usize,
    /// Total points across all four passes.
    pub points: u32,
}

/// Rank every country that scores against `folded_query`, best first.
///
/// `folded_query` must already be `remove_accents(query.strip().lower())` --
/// [`crate::fold::fold_query`] produces it. It is taken pre-folded because the
/// vectorized callers fold once and reuse the result.
///
/// Returns an empty vector when nothing scores, which is the reference's
/// `LookupError`.
pub fn search(db: &Db, folded_query: &str) -> Vec<Scored> {
    let countries = &db.countries;
    // Points per country record. A dense vector rather than a map: there are
    // ~250 countries, and this runs once per distinct query.
    let mut points = vec![0u32; countries.len()];

    // Pass 1: an exact hit on any indexed field.
    if let Some((record, _)) = countries.lookup(folded_query) {
        points[record] += 50;
    }

    // Pass 2: subdivision fields equal to the query, 49 points each.
    for &(record, hits) in db.subdivisions.exact_field_hits(folded_query) {
        points[record] += 49 * hits;
    }

    // Pass 3: the country's own names. The first of name / official_name /
    // comment to match wins and the rest are skipped, so this breaks.
    for (record, earned) in points.iter_mut().enumerate() {
        for field in countries.fuzzy_fields(record) {
            if field.initials == folded_query {
                *earned += 40;
                break;
            }
            if let Some(offset) = char_find(&field.folded, folded_query) {
                // Earlier in the name scores higher, so "new zealand" beats
                // "papua new guinea" for the query "new".
                *earned += (30i64 - 2 * offset as i64).max(5) as u32;
                break;
            }
        }
    }

    // Pass 4: subdivision names containing the query, worth very little --
    // enough to break a tie, not enough to outrank a real name match.
    for sub in &db.subdivisions.records {
        let Some(record) = sub.country else {
            continue;
        };
        if let Some(offset) = char_find(&sub.folded_name, folded_query) {
            points[record] += (5i64 - offset as i64).max(1) as u32;
        }
    }

    // Every award is at least one point, so a zero is "did not match".
    let mut ranked: Vec<Scored> = points
        .into_iter()
        .enumerate()
        .filter(|&(_, p)| p > 0)
        .map(|(record, points)| Scored { record, points })
        .collect();
    // Descending by points, then ascending by alpha-2 so the order is total
    // and does not depend on the table's file order.
    ranked.sort_by(|a, b| {
        b.points
            .cmp(&a.points)
            .then_with(|| countries.alpha_2(a.record).cmp(&countries.alpha_2(b.record)))
    });
    ranked
}

/// The single best match, or `None` when nothing scores.
pub fn best(db: &Db, folded_query: &str) -> Option<Scored> {
    // Ranking all of them to take the first is what the reference does, and
    // the list is short enough that a partial selection would not pay for
    // itself. Callers memoize across rows, so this runs once per distinct
    // query rather than once per row.
    search(db, folded_query).into_iter().next()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::current;
    use crate::fold::fold_query;

    fn top(query: &str) -> Option<String> {
        let db = current();
        best(&db, &fold_query(query))
            .and_then(|s| db.countries.alpha_2(s.record).map(str::to_owned))
    }

    #[test]
    fn exact_names_and_codes_win() {
        assert_eq!(top("United States").as_deref(), Some("US"));
        assert_eq!(top("de").as_deref(), Some("DE"));
        assert_eq!(top("Germany").as_deref(), Some("DE"));
    }

    #[test]
    fn initials_resolve() {
        // No field holds "USA", but the uppercase letters of "United States
        // of America" do.
        assert_eq!(top("USA").as_deref(), Some("US"));
    }

    #[test]
    fn accents_are_folded_away_on_both_sides() {
        // The exact indices are accent-sensitive, so this only resolves here.
        assert_eq!(top("Cote d'Ivoire").as_deref(), Some("CI"));
        assert_eq!(top("Aland Islands").as_deref(), Some("AX"));
    }

    #[test]
    fn partial_names_resolve_to_the_earliest_match() {
        assert_eq!(top("Bolivia").as_deref(), Some("BO"));
        assert_eq!(top("Dominican").as_deref(), Some("DO"));
    }

    #[test]
    fn fuzzy_is_a_guess_and_sometimes_a_wrong_one() {
        // "Republic of Korea" is the official name of KR, but no ISO field
        // spells it that way, so no pass matches it as a substring. What
        // scores instead is the subdivision pass, and North Korea's
        // subdivisions reach further. `pycountry` answers KP here too; this
        // test pins that agreement, and makes concrete why `fuzzy` is opt-in.
        assert_eq!(top("Republic of Korea").as_deref(), Some("KP"));
    }

    #[test]
    fn subdivision_names_reach_their_country() {
        assert_eq!(top("Texas").as_deref(), Some("US"));
        // The standard spells it "Bayern", and only the standard's spelling
        // is in the table -- fuzzy search is not a translation layer.
        assert_eq!(top("Bayern").as_deref(), Some("DE"));
        assert_eq!(top("Bavaria"), None);
    }

    #[test]
    fn nothing_scores_for_a_string_no_field_contains() {
        let db = current();
        assert!(search(&db, &fold_query("zzzzzzzznotacountry")).is_empty());
    }

    #[test]
    fn results_are_ranked_and_stable() {
        let db = current();
        let ranked = search(&db, &fold_query("guinea"));
        assert!(ranked.len() > 1, "several countries carry 'guinea'");
        for pair in ranked.windows(2) {
            assert!(pair[0].points >= pair[1].points);
            if pair[0].points == pair[1].points {
                assert!(
                    db.countries.alpha_2(pair[0].record) < db.countries.alpha_2(pair[1].record)
                );
            }
        }
    }
}
