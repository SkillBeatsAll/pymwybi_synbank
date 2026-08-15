"""Model diagnostics: the places this engine is most likely to be wrong.

Every check here exists because a specific failure mode would otherwise ship
looking like a finding. They are published as data, at three scopes -- one
client's product, a whole product, or a whole sector -- so a reviewer can sort
by severity and work down rather than reading twenty explanations hoping to spot
the broken one.

Severity is ``HIGH`` when the number should not be used before review, ``MEDIUM``
when it is usable with the caveat attached, and ``INFO`` when it is a property of
the data worth stating.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import assumptions

HIGH = "HIGH"
MEDIUM = "MEDIUM"
INFO = "INFO"

#: A gap in the top decile of its product counts as extreme.
EXTREME_GAP_PERCENTILE = 0.90
#: A share above this is implausible enough to warrant review even after capping.
HIGH_SHARE_THRESHOLD = 0.80
#: A product is "insufficiently evidenced" when this fraction of clients is LOW.
LOW_CONFIDENCE_PRODUCT_FRACTION = 0.50
#: Flags that mean a number leans on something not disclosed by the client.
IMPUTATION_FLAGS = (
    "cogs_imputed",
    "foreign_revenue_imputed",
    "cogs_unavailable",
    "estimate_dominated_by_imputed_driver",
    "capex_judgement_dominates",
)


def _rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "severity",
        "scope",
        "diagnostic",
        "entity_id",
        "entity_name",
        "sector",
        "product",
        "metric",
        "metric_value",
        "detail",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)[columns]


def _client_rows(
    group: pd.DataFrame,
    mask: pd.Series,
    severity: str,
    diagnostic: str,
    metric: str,
    metric_values: pd.Series,
    detail: str,
) -> list[dict[str, Any]]:
    rows = []
    for position in group.index[mask.fillna(False)]:
        rows.append(
            {
                "severity": severity,
                "scope": "client_product",
                "diagnostic": diagnostic,
                "entity_id": group.at[position, "entity_id"],
                "entity_name": group.at[position, "entity_name"],
                "sector": group.at[position, "sector"],
                "product": group.at[position, "product"],
                "metric": metric,
                "metric_value": float(pd.to_numeric(metric_values.at[position], errors="coerce"))
                if pd.notna(metric_values.at[position])
                else np.nan,
                "detail": detail,
            }
        )
    return rows


def build(estimates: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Run every diagnostic and return the long findings table."""
    rows: list[dict[str, Any]] = []
    revenue_rank = (
        pd.to_numeric(features.set_index("entity_id")["revenue_total_zar"], errors="coerce")
        .rank(pct=True)
        .to_dict()
    )

    for product, group in estimates.groupby("product", sort=False):
        group = group.reset_index(drop=True)
        share = pd.to_numeric(group["share"], errors="coerce")
        uncapped = pd.to_numeric(group["share_uncapped"], errors="coerce")
        gap = pd.to_numeric(group["gap_zar"], errors="coerce")
        confidence = pd.to_numeric(group["confidence"], errors="coerce")
        flags = group["diagnostic_flags"].fillna("")

        # 1. Observed activity exceeds the estimated wallet.
        rows += _client_rows(
            group,
            uncapped > assumptions.SHARE_CAP,
            HIGH,
            "observed_exceeds_estimated_wallet",
            "share_uncapped",
            uncapped,
            "Syn Bank's observed activity is larger than the wallet the model estimated. The "
            "reported share is capped at 100%; the wallet driver is understated or the wrong "
            "driver for this client. Review before quoting either number.",
        )

        # 2. Unusually high share even after capping.
        rows += _client_rows(
            group,
            (share >= HIGH_SHARE_THRESHOLD) & (uncapped <= assumptions.SHARE_CAP),
            MEDIUM,
            "unusually_high_share",
            "share",
            share,
            f"Share at or above {HIGH_SHARE_THRESHOLD:.0%} of the estimated wallet. Plausible for "
            "a peer-benchmark basis, where the benchmark client is near 100% by construction, "
            "but it means there is little headroom left to sell.",
        )

        # 3. Estimate driven by an imputed or judgement input.
        imputed = flags.apply(lambda text: any(flag in text for flag in IMPUTATION_FLAGS))
        rows += _client_rows(
            group,
            imputed,
            MEDIUM,
            "estimate_driven_by_missing_variable",
            "confidence",
            confidence,
            "At least one driver behind this estimate was imputed from peers or rests on the "
            "capex judgement coefficient, because the client did not disclose it. The rand "
            "amount is a scaled peer ratio, not a client disclosure.",
        )

        # 4. Estimate driven primarily by company size.
        if product != assumptions.IB:
            size_rank = group["entity_id"].map(revenue_rank)
            gap_rank = gap.rank(pct=True)
            # An imputed driver counts as much as low confidence here: a
            # top-decile gap built on a peer ratio applied to a trillion-rand
            # revenue base is a restatement of size either way. Gating only on
            # LOW confidence missed Glencore's R232bn FX estimate, which rests
            # entirely on an imputed foreign-revenue share.
            imputed_or_uncertain = imputed | (confidence < assumptions.CONFIDENCE_BAND_HIGH)
            driven_by_size = (
                (size_rank >= EXTREME_GAP_PERCENTILE)
                & (gap_rank >= EXTREME_GAP_PERCENTILE)
                & imputed_or_uncertain
            )
            rows += _client_rows(
                group,
                driven_by_size,
                HIGH,
                "opportunity_driven_by_company_size",
                "gap_zar",
                gap,
                "This client sits in the top decile of the portfolio on both revenue and rand "
                "gap while confidence is LOW. The size of the opportunity is a restatement of "
                "the size of the company, not evidence of an under-served relationship.",
            )

            # 5. Extremely high gap.
            threshold = gap.quantile(EXTREME_GAP_PERCENTILE)
            if pd.notna(threshold):
                rows += _client_rows(
                    group,
                    gap >= threshold,
                    INFO,
                    "extreme_gap_in_product",
                    "gap_zar",
                    gap,
                    f"Gap sits in the top decile for {product}. Not a defect on its own; listed "
                    "so the largest numbers in the deck are always visible next to their "
                    "confidence.",
                )

            # 6. Large opportunity on low confidence.
            rows += _client_rows(
                group,
                (gap.rank(pct=True) >= 0.75) & (confidence < assumptions.CONFIDENCE_BAND_MEDIUM),
                HIGH,
                "large_opportunity_low_confidence",
                "confidence",
                confidence,
                "An upper-quartile rand gap on LOW confidence. The opportunity score already "
                "discounts this, but the rand amount should not be quoted without the caveat.",
            )

        # 7. Does this product's ranking just restate company size?
        if product != assumptions.IB:
            size_rank = group["entity_id"].map(revenue_rank)
            gap_rank = pd.to_numeric(group["gap_zar"], errors="coerce").rank(pct=True)
            paired = pd.DataFrame({"size": size_rank, "gap": gap_rank}).dropna()
            if len(paired) >= 5:
                # Both columns are already percentile ranks, so a Pearson
                # correlation over them is Spearman's rho by definition. Doing
                # it this way keeps scipy out of the dependency set.
                correlation = float(paired["size"].corr(paired["gap"]))
                if correlation >= 0.90:
                    rows.append(
                        {
                            "severity": INFO,
                            "scope": "product",
                            "diagnostic": "product_ranking_tracks_company_size",
                            "entity_id": None,
                            "entity_name": None,
                            "sector": None,
                            "product": product,
                            "metric": "spearman_size_vs_gap",
                            "metric_value": correlation,
                            "detail": (
                                f"Rand gap ranks {correlation:.2f} correlated with revenue rank in "
                                f"{product}. The within-product ordering is close to a size "
                                "ordering. That is the honest consequence of an identity-anchored "
                                "denominator -- the biggest payment flows really are the biggest "
                                "opportunities -- but the ranking carries little client-specific "
                                "information beyond scale, and share and confidence should be read "
                                "alongside it."
                            ),
                        }
                    )

        # 8. Product-level evidence sufficiency.
        low_fraction = float((group["confidence_band"] == "LOW").mean())
        if low_fraction >= LOW_CONFIDENCE_PRODUCT_FRACTION:
            rows.append(
                {
                    "severity": HIGH,
                    "scope": "product",
                    "diagnostic": "product_model_insufficient_evidence",
                    "entity_id": None,
                    "entity_name": None,
                    "sector": None,
                    "product": product,
                    "metric": "fraction_low_confidence",
                    "metric_value": low_fraction,
                    "detail": (
                        f"{low_fraction:.0%} of clients score LOW confidence in this product. "
                        "The model is running on imputed or weakly applicable inputs for most of "
                        "the portfolio; treat product-level totals as indicative only."
                    ),
                }
            )

        # 9. Sectors with systematically extreme results.
        for sector, sector_group in group.groupby("sector", sort=False):
            if len(sector_group) < 2:
                continue
            sector_share = pd.to_numeric(sector_group["share"], errors="coerce")
            if sector_share.notna().sum() < 2:
                continue
            median_share = float(sector_share.median())
            if median_share >= HIGH_SHARE_THRESHOLD or median_share <= 0.001:
                rows.append(
                    {
                        "severity": MEDIUM,
                        "scope": "sector_product",
                        "diagnostic": "sector_systematically_extreme_share",
                        "entity_id": None,
                        "entity_name": None,
                        "sector": sector,
                        "product": product,
                        "metric": "median_share",
                        "metric_value": median_share,
                        "detail": (
                            f"Median share for {sector} in {product} is {median_share:.4f}. Every "
                            "client in the sector lands at the same extreme, which points at the "
                            "driver being wrong for the sector rather than at twenty independent "
                            "client facts."
                        ),
                    }
                )

    return _rows_to_frame(rows).sort_values(
        ["severity", "product", "entity_id"],
        key=lambda column: column.map({HIGH: 0, MEDIUM: 1, INFO: 2}) if column.name == "severity" else column,
    ).reset_index(drop=True)


def summarise(diagnostics: pd.DataFrame, estimates: pd.DataFrame) -> dict[str, Any]:
    """A JSON-serialisable digest of the diagnostics for the run report."""
    by_severity = diagnostics["severity"].value_counts().to_dict() if len(diagnostics) else {}
    by_diagnostic = (
        diagnostics.groupby("diagnostic").size().sort_values(ascending=False).to_dict()
        if len(diagnostics)
        else {}
    )
    high = diagnostics[diagnostics["severity"] == HIGH] if len(diagnostics) else diagnostics
    return {
        "total_findings": int(len(diagnostics)),
        "by_severity": {str(key): int(value) for key, value in by_severity.items()},
        "by_diagnostic": {str(key): int(value) for key, value in by_diagnostic.items()},
        "clients_with_high_severity_findings": sorted(
            {str(value) for value in high["entity_id"].dropna()}
        ),
        "products_with_high_severity_findings": sorted(
            {str(value) for value in high["product"].dropna()}
        ),
        "confidence_bands": {
            str(band): int(count)
            for band, count in estimates["confidence_band"].value_counts().items()
        },
        "estimates_with_any_flag": int((estimates["diagnostic_flags"].fillna("") != "").sum()),
    }
