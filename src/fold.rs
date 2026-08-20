//! Case folding and accent removal, matching `pycountry`'s normalization.
//!
//! Two different normalizations are in play, and conflating them is the
//! easiest way to diverge:
//!
//! * The **exact** indices are built with plain `str.lower()`. They are
//!   case-insensitive but accent-*sensitive*: `pycountry.countries.lookup`
//!   finds `"CÔTE D'IVOIRE"` and misses `"Cote d'Ivoire"`.
//! * **Fuzzy** search folds accents away on both sides, via
//!   `remove_accents(value.lower())`, so `"cote d'ivoire"` matches there.
//!
//! [`lower`] is the first, [`fold`] the second. Both are ported directly from
//! `pycountry.remove_accents` and `pycountry.db.Database._index_object`.

use std::borrow::Cow;

use unicode_normalization::char::canonical_combining_class;
use unicode_normalization::UnicodeNormalization;

/// Lowercase, as `pycountry` does when building and probing its indices.
///
/// Borrows when the input is already lowercase ASCII, which covers most codes.
pub fn lower(s: &str) -> Cow<'_, str> {
    if s.is_ascii() && !s.bytes().any(|b| b.is_ascii_uppercase()) {
        Cow::Borrowed(s)
    } else {
        Cow::Owned(s.to_lowercase())
    }
}

/// Lowercase `s` into `buffer`, returning a borrow of the result.
///
/// The buffer is reused across rows, so the ASCII path -- nearly all input --
/// stops allocating after the first row. That matters more than it looks:
/// lowering is the only per-row allocation on the exact-lookup path, so
/// removing it is most of the difference between this and a plain hash probe.
pub fn lower_into<'b>(buffer: &'b mut String, s: &str) -> &'b str {
    buffer.clear();
    if s.is_ascii() {
        buffer.push_str(s);
        buffer.make_ascii_lowercase();
    } else {
        // `str::to_lowercase` applies context-sensitive rules -- Greek final
        // sigma among them -- that a char-by-char fold would get wrong, so
        // non-ASCII keeps the allocating path. It is the rare case.
        buffer.push_str(&s.to_lowercase());
    }
    buffer.as_str()
}

/// Strip combining marks, mirroring `pycountry.remove_accents`.
///
/// NFKD decomposes `é` into `e` plus a combining acute, and dropping every
/// character with a nonzero canonical combining class leaves the base letters.
///
/// The ASCII fast path is not just an optimization: `pycountry` guards the
/// whole function with `if not input_str.isascii()`, so ASCII input is
/// returned byte-for-byte and any behavior difference in NFKD on ASCII (there
/// is none, but the guard is what the reference does) cannot arise.
pub fn remove_accents(s: &str) -> Cow<'_, str> {
    if s.is_ascii() {
        return Cow::Borrowed(s);
    }
    Cow::Owned(s.nfkd().filter(|c| canonical_combining_class(*c) == 0).collect())
}

/// The form fuzzy search compares on: `remove_accents(value.lower())`.
pub fn fold(s: &str) -> String {
    remove_accents(&lower(s)).into_owned()
}

/// The query form: `remove_accents(query.strip().lower())`.
pub fn fold_query(s: &str) -> String {
    fold(s.trim())
}

/// `pycountry`'s initials test: the uppercase characters of a name, folded.
///
/// `"United States of America"` yields `"usa"`, which is why `search_fuzzy`
/// resolves `"USA"` even though no field holds that string.
pub fn initials(s: &str) -> String {
    let caps: String = s.chars().filter(|c| c.is_uppercase()).collect();
    fold(&caps)
}

/// Python's `str.find`: the **character** offset of `needle`, or `None`.
///
/// The character offset is what matters, not the byte offset: `search_fuzzy`
/// scores a match by how early in the name it starts, and a byte offset would
/// score an accented or non-Latin name differently from `pycountry`.
pub fn char_find(haystack: &str, needle: &str) -> Option<usize> {
    haystack.find(needle).map(|byte| haystack[..byte].chars().count())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lower_borrows_when_it_can() {
        assert!(matches!(lower("us"), Cow::Borrowed("us")));
        assert_eq!(lower("US"), "us");
        assert_eq!(lower("ÅLAND"), "åland");
    }

    #[test]
    fn lower_into_agrees_with_lower_and_reuses_its_buffer() {
        let mut buffer = String::new();
        for probe in ["US", "us", "\u{c5}LAND", "Côte d'Ivoire", ""] {
            assert_eq!(lower_into(&mut buffer, probe), lower(probe));
        }
        // The buffer holds only the last value, not an accumulation.
        assert_eq!(buffer, "");
    }

    #[test]
    fn accents_are_stripped_only_from_non_ascii() {
        assert!(matches!(remove_accents("plain"), Cow::Borrowed("plain")));
        assert_eq!(remove_accents("Côte d'Ivoire"), "Cote d'Ivoire");
        assert_eq!(remove_accents("Åland"), "Aland");
        assert_eq!(remove_accents("Curaçao"), "Curacao");
    }

    #[test]
    fn fold_lowercases_and_strips() {
        assert_eq!(fold("CÔTE D'IVOIRE"), "cote d'ivoire");
        assert_eq!(fold_query("  Åland Islands "), "aland islands");
    }

    #[test]
    fn initials_are_the_uppercase_letters() {
        assert_eq!(initials("United States of America"), "usa");
        // Only the uppercase letters: the lowercase "of" contributes nothing.
        assert_eq!(initials("Bolivia, Plurinational State of"), "bps");
        assert_eq!(initials("france"), "");
    }

    #[test]
    fn char_find_counts_characters_not_bytes() {
        assert_eq!(char_find("abcdef", "cd"), Some(2));
        // Four two-byte characters ahead of the needle: byte offset 8,
        // character offset 4.
        assert_eq!(char_find("ÄÄÄÄxy", "xy"), Some(4));
        assert_eq!(char_find("abc", "z"), None);
    }
}
