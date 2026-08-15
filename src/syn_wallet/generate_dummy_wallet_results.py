"""Generate a clearly-labelled DUMMY ``opportunities`` table so the dashboard can be
built and demoed before the real wallet engine output lands in this working tree.

Schema matches ``opportunities.parquet`` from the teammate-owned wallet engine
(``src/syn_wallet/wallet/opportunity.py`` on the ``feat/data-anal`` branch) column for
column, so the dashboard's loader needs zero changes the moment that real file appears
at ``data/processed/opportunities.parquet`` — it is picked up automatically ahead of this
dummy. See ``dashboard/app.py`` for the auto-detection.

Every number here is illustrative and seeded for reproducibility - none of it comes from
the actual financial statements or internal transaction data. Never let this file's
numbers reach a slide, the PDF, or a briefing note.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

CASH, FX, TRADE, LENDING, IB = "cash_management", "fx_global_markets", "trade_finance", "lending", "investment_banking"
PRODUCTS = (CASH, FX, TRADE, LENDING, IB)

PRODUCT_LABELS = {
    CASH: "Transactional / Cash Management",
    FX: "FX / Global Markets",
    TRADE: "Trade Finance",
    LENDING: "Lending",
    IB: "Investment Banking / Capital Markets",
}
ESTIMATE_BASIS = {
    CASH: "total_addressable_market",
    FX: "peer_benchmark_addressable",
    TRADE: "peer_benchmark_addressable",
    LENDING: "financing_opportunity",
    IB: "signal_only",
}
ESTIMATE_KIND = {
    CASH: "addressable_wallet",
    FX: "addressable_wallet",
    TRADE: "addressable_wallet",
    LENDING: "opportunity_estimate",
    IB: "signal_only",
}
# No rand estimate at all for IB; no defensible share for lending or IB.
HAS_RAND_ESTIMATE = {CASH: True, FX: True, TRADE: True, LENDING: True, IB: False}
HAS_SHARE = {CASH: True, FX: True, TRADE: True, LENDING: False, IB: False}

PILLAR_SCALE_ZAR = {CASH: 6_000_000_000, FX: 700_000_000, TRADE: 900_000_000, LENDING: 1_500_000_000}
METHODOLOGY_VERSION = "wallet-1.0.0-dummy"

OUTPUT_COLUMNS = (
    "rank_overall", "rank_in_product", "entity_id", "entity_name", "sector", "product",
    "product_label", "estimate_basis", "estimate_kind", "observed_zar", "estimate_zar",
    "share", "gap_zar", "confidence", "confidence_band", "opportunity_score",
    "opportunity_gap_scale", "opportunity_headroom", "diagnostic_flags", "explanation",
    "methodology_version",
)


def _entities(trade_finance_csv: Path) -> pd.DataFrame:
    return duckdb.sql(
        f"SELECT DISTINCT entity_id, entity_name, sector "
        f"FROM read_csv_auto('{trade_finance_csv}') ORDER BY entity_id"
    ).df()


def _confidence_band(confidence: float) -> str:
    if confidence >= 0.70:
        return "HIGH"
    if confidence >= 0.45:
        return "MEDIUM"
    return "LOW"


def generate(trade_finance_csv: Path, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    entities = _entities(trade_finance_csv)

    rows = []
    for _, entity in entities.iterrows():
        for product in PRODUCTS:
            confidence = float(rng.uniform(0.15, 0.95))

            estimate_zar = gap_zar = observed_zar = share = np.nan
            if HAS_RAND_ESTIMATE[product]:
                estimate_zar = PILLAR_SCALE_ZAR[product] * rng.uniform(0.4, 2.2)
                if HAS_SHARE[product]:
                    share = float(rng.uniform(0.005, 0.06))  # low single digits, per the sanity check
                    observed_zar = estimate_zar * share
                    gap_zar = estimate_zar - observed_zar
                else:
                    gap_zar = estimate_zar  # lending: opportunity estimate, no observed loan book

            rows.append(
                {
                    "entity_id": entity["entity_id"],
                    "entity_name": entity["entity_name"],
                    "sector": entity["sector"],
                    "product": product,
                    "product_label": PRODUCT_LABELS[product],
                    "estimate_basis": ESTIMATE_BASIS[product],
                    "estimate_kind": ESTIMATE_KIND[product],
                    "observed_zar": round(observed_zar, 2) if pd.notna(observed_zar) else np.nan,
                    "estimate_zar": round(estimate_zar, 2) if pd.notna(estimate_zar) else np.nan,
                    "share": round(share, 4) if pd.notna(share) else np.nan,
                    "gap_zar": round(gap_zar, 2) if pd.notna(gap_zar) else np.nan,
                    "confidence": round(confidence, 3),
                    "confidence_band": _confidence_band(confidence),
                    "diagnostic_flags": "",
                    "explanation": f"Illustrative placeholder for {PRODUCT_LABELS[product]} - not a real estimate.",
                    "methodology_version": METHODOLOGY_VERSION,
                }
            )

    df = pd.DataFrame(rows)

    # Mirrors opportunity.py: percentile rank of gap within product (IB has no gap - scores 0
    # on this factor, same as the real engine), headroom = 1 - share (neutral 0.5 where no
    # defensible share exists), then the declared 0.45/0.30/0.25 weights.
    df["opportunity_gap_scale"] = 0.0
    for product in PRODUCTS:
        mask = df["product"] == product
        df.loc[mask, "opportunity_gap_scale"] = df.loc[mask, "gap_zar"].rank(pct=True, na_option="keep").fillna(0.0)
    df["opportunity_headroom"] = (1.0 - df["share"]).fillna(0.5).clip(0.0, 1.0)
    df["opportunity_score"] = (
        0.45 * df["opportunity_gap_scale"] + 0.30 * df["confidence"] + 0.25 * df["opportunity_headroom"]
    ).clip(0.0, 1.0)

    ordering = ["opportunity_score", "gap_zar", "entity_id"]
    ascending = [False, False, True]
    df["rank_in_product"] = (
        df.sort_values(ordering, ascending=ascending).groupby("product", sort=False).cumcount().add(1).reindex(df.index)
    )
    df["rank_overall"] = (
        df.sort_values(ordering, ascending=ascending)
        .assign(_rank=lambda frame: np.arange(1, len(frame) + 1))["_rank"]
        .reindex(df.index)
    )

    return df[list(OUTPUT_COLUMNS)].sort_values("rank_overall").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-finance-csv", type=Path, default=Path("data/trade_finance.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/opportunities_DUMMY.csv"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = generate(args.trade_finance_csv, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"opportunities_DUMMY.csv: {len(df)} rows ({len(PRODUCTS)} products x {len(df) // len(PRODUCTS)} entities)")


if __name__ == "__main__":
    main()
