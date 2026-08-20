"""The `.country` expression namespace.

Importing `polars_country` registers this, so `pl.col("c").country.alpha_2()`
works without importing anything else. Every method mirrors the module-level
function of the same name in `polars_country._expr`, and takes the same
keywords.
"""

from __future__ import annotations

from polars_country import _expr

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from polars._typing import IntoExprColumn


@pl.api.register_expr_namespace("country")
class CountryNamespace:
    """ISO 3166 and ISO 4217 lookup, as `pl.col(...).country.*`."""

    def __init__(self, expr: pl.Expr) -> None:
        """Bind the namespace to an expression.

        Parameters
        ----------
        expr : pl.Expr
            The string expression the namespace methods operate on.
        """
        self._expr = expr

    # ------------------------------------------------------------------
    # Countries
    # ------------------------------------------------------------------

    def extract(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Resolve a country and return every ISO field as a struct.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Struct of every ISO 3166 field, plus `historic`.
        """
        return _expr.extract(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    def match(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Report *how* each value resolved, not just what it resolved to.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Struct of `alpha_2`, `matched_on`, `score` and `historic`.
        """
        return _expr.match(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    def alpha_2(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the ISO 3166-1 alpha-2 code, e.g. `"US"`.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of alpha-2 codes, null where nothing matched.
        """
        return _expr.alpha_2(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    def alpha_3(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the ISO 3166-1 alpha-3 code, e.g. `"USA"`.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of alpha-3 codes, null where nothing matched.
        """
        return _expr.alpha_3(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    def numeric(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the ISO 3166-1 numeric code, e.g. `"840"`.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of zero-padded numeric codes, null where nothing
            matched.
        """
        return _expr.numeric(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    def name(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the country's short name, e.g. `"United States"`.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of short names, null where nothing matched.
        """
        return _expr.name(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    def official_name(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the official name, e.g. `"United States of America"`.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column, null where the country has no distinct official name.
        """
        return _expr.official_name(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    def common_name(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the common name, e.g. `"Bolivia"` for `BO`.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column, null for the majority of countries, which have none.
        """
        return _expr.common_name(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    def flag(
        self,
        *,
        fuzzy: bool = False,
        historic: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the flag emoji, e.g. the regional indicators for `US`.

        Parameters
        ----------
        fuzzy : bool, default False
            Whether an exact miss may fall back to `pycountry`'s
            `search_fuzzy`.
        historic : bool, default False
            Whether ISO 3166-3 is consulted when ISO 3166-1 has no match.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of flag emoji, null where nothing matched.
        """
        return _expr.flag(
            self._expr, fuzzy=fuzzy, historic=historic, parallel=parallel
        )

    # ------------------------------------------------------------------
    # Subdivisions
    # ------------------------------------------------------------------

    def subdivision(
        self,
        country: IntoExprColumn | None = None,
        *,
        fuzzy: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Resolve an ISO 3166-2 subdivision and return it as a struct.

        Parameters
        ----------
        country : IntoExprColumn | None, optional
            The country to scope the search to. A bare `str` names a column,
            per the usual Polars convention; use `pl.lit("US")` for a fixed
            country. Pass it whenever you have it: subdivision names are not
            unique across the standard.
        fuzzy : bool, default False
            Whether to fall back to accent-folded and partial name matches.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Struct of `code`, `name`, `type`, `country_code` and
            `parent_code`.
        """
        return _expr.subdivision(
            self._expr, country, fuzzy=fuzzy, parallel=parallel
        )

    def subdivision_code(
        self,
        country: IntoExprColumn | None = None,
        *,
        fuzzy: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the ISO 3166-2 code, e.g. `"US-CA"`.

        Parameters
        ----------
        country : IntoExprColumn | None, optional
            The country to scope the search to. A bare `str` names a column,
            per the usual Polars convention; use `pl.lit("US")` for a fixed
            country. Pass it whenever you have it: subdivision names are not
            unique across the standard.
        fuzzy : bool, default False
            Whether to fall back to accent-folded and partial name matches.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of subdivision codes, null where nothing matched.
        """
        return _expr.subdivision_code(
            self._expr, country, fuzzy=fuzzy, parallel=parallel
        )

    def subdivision_name(
        self,
        country: IntoExprColumn | None = None,
        *,
        fuzzy: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the subdivision's name, e.g. `"California"`.

        Parameters
        ----------
        country : IntoExprColumn | None, optional
            The country to scope the search to. A bare `str` names a column,
            per the usual Polars convention; use `pl.lit("US")` for a fixed
            country. Pass it whenever you have it: subdivision names are not
            unique across the standard.
        fuzzy : bool, default False
            Whether to fall back to accent-folded and partial name matches.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of subdivision names, null where nothing matched.
        """
        return _expr.subdivision_name(
            self._expr, country, fuzzy=fuzzy, parallel=parallel
        )

    def subdivision_type(
        self,
        country: IntoExprColumn | None = None,
        *,
        fuzzy: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the subdivision's type, e.g. `"State"`.

        Parameters
        ----------
        country : IntoExprColumn | None, optional
            The country to scope the search to. A bare `str` names a column,
            per the usual Polars convention; use `pl.lit("US")` for a fixed
            country. Pass it whenever you have it: subdivision names are not
            unique across the standard.
        fuzzy : bool, default False
            Whether to fall back to accent-folded and partial name matches.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of subdivision types, null where nothing matched.
        """
        return _expr.subdivision_type(
            self._expr, country, fuzzy=fuzzy, parallel=parallel
        )

    def subdivision_country(
        self,
        country: IntoExprColumn | None = None,
        *,
        fuzzy: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the alpha-2 code of the country it belongs to.

        Parameters
        ----------
        country : IntoExprColumn | None, optional
            The country to scope the search to. A bare `str` names a column,
            per the usual Polars convention; use `pl.lit("US")` for a fixed
            country. Pass it whenever you have it: subdivision names are not
            unique across the standard.
        fuzzy : bool, default False
            Whether to fall back to accent-folded and partial name matches.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of alpha-2 codes, null where nothing matched.
        """
        return _expr.subdivision_country(
            self._expr, country, fuzzy=fuzzy, parallel=parallel
        )

    def subdivision_parent_code(
        self,
        country: IntoExprColumn | None = None,
        *,
        fuzzy: bool = False,
        parallel: bool = True,
    ) -> pl.Expr:
        """Extract the parent subdivision's code, where there is one.

        Parameters
        ----------
        country : IntoExprColumn | None, optional
            The country to scope the search to. A bare `str` names a column,
            per the usual Polars convention; use `pl.lit("US")` for a fixed
            country. Pass it whenever you have it: subdivision names are not
            unique across the standard.
        fuzzy : bool, default False
            Whether to fall back to accent-folded and partial name matches.
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of parent codes, null for a top-level subdivision.
        """
        return _expr.subdivision_parent_code(
            self._expr, country, fuzzy=fuzzy, parallel=parallel
        )

    # ------------------------------------------------------------------
    # Currencies
    # ------------------------------------------------------------------

    def currency(self, *, parallel: bool = True) -> pl.Expr:
        """Resolve an ISO 4217 currency and return every field as a struct.

        Parameters
        ----------
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Struct of `alpha_3`, `numeric` and `name`.
        """
        return _expr.currency(self._expr, parallel=parallel)

    def currency_alpha_3(self, *, parallel: bool = True) -> pl.Expr:
        """Extract the ISO 4217 alpha-3 code, e.g. `"USD"`.

        Parameters
        ----------
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of currency codes, null where nothing matched.
        """
        return _expr.currency_alpha_3(self._expr, parallel=parallel)

    def currency_numeric(self, *, parallel: bool = True) -> pl.Expr:
        """Extract the ISO 4217 numeric code, e.g. `"840"`.

        Parameters
        ----------
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of zero-padded numeric codes, null where nothing
            matched.
        """
        return _expr.currency_numeric(self._expr, parallel=parallel)

    def currency_name(self, *, parallel: bool = True) -> pl.Expr:
        """Extract the currency's name, e.g. `"US Dollar"`.

        Parameters
        ----------
        parallel : bool, default True
            Whether the plugin may split large columns across rayon threads.

        Returns
        -------
        pl.Expr
            Utf8 column of currency names, null where nothing matched.
        """
        return _expr.currency_name(self._expr, parallel=parallel)
