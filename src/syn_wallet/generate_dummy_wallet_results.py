"""Generate a clearly-labelled DUMMY ``wallet_results.csv`` so the dashboard can be
built against the real schema before the Layers 1-4 model exists.

Every number here is illustrative and seeded for reproducibility - none of it comes
from the actual financial statements or internal transaction data. It exists only to
unblock dashboard UI work. The values still respect the model's structural rules so the
dashboard shell behaves the way it will against the real output:

* Lending & DCM is structurally unobservable in the supplied internal data (see
  CLAUDE.md section 6) - its ``observed``, ``share_p50`` and ``unaddressed_p50`` are
  left null rather than 0, and confidence is kept low.
* ``opportunity_score`` follows the real Layer 4 formula
  (0.5*norm(unaddressed_p50) + 0.3*confidence + 0.2*propensity), normalised within
  each pillar rather than across pillars, since pillars have very different
  magnitudes.

Never let this file's numbers reach a slide, the PDF, or a briefing note.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PILLARS = ("cash_mgmt", "trade_finance", "fx", "lending_dcm")

# Rough order-of-magnitude annual Rand figures per pillar, just to make the illustrative
# numbers look plausible relative to each other (cash flow >> trade finance/FX >> lending stock
# is roughly what the real data audit suggests - see data_analysis.md raw totals).
PILLAR_SCALE_ZAR = {
    "cash_mgmt": 3_000_000_000,
    "trade_finance": 900_000_000,
    "fx": 700_000_000,
    "lending_dcm": 1_500_000_000,
}


def _entities(trade_finance_csv: Path) -> pd.DataFrame:
    return duckdb.sql(
        f"SELECT DISTINCT entity_id, entity_name, sector "
        f"FROM read_csv_auto('{trade_finance_csv}') ORDER BY entity_id"
    ).df()


def generate(trade_finance_csv: Path, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    entities = _entities(trade_finance_csv)

    rows = []
    for _, entity in entities.iterrows():
        for pillar in PILLARS:
            scale = PILLAR_SCALE_ZAR[pillar] * rng.uniform(0.4, 2.2)
            addressable_p50 = scale
            addressable_p10 = addressable_p50 * rng.uniform(0.60, 0.85)
            addressable_p90 = addressable_p50 * rng.uniform(1.15, 1.55)

            propensity = rng.uniform(0.1, 0.9)

            if pillar == "lending_dcm":
                # Structurally unobservable - never scored as zero share.
                observed = np.nan
                share_p50 = np.nan
                unaddressed_p50 = np.nan
                confidence = rng.uniform(0.10, 0.30)
            else:
                # Sanity check in CLAUDE.md: real share of flow should land low single digits.
                share = rng.uniform(0.005, 0.06)
                observed = addressable_p50 * share
                share_p50 = share
                unaddressed_p50 = addressable_p50 - observed
                confidence = rng.uniform(0.35, 0.95)

            rows.append(
                {
                    "entity_id": entity["entity_id"],
                    "pillar": pillar,
                    "addressable_p10": round(addressable_p10, 2),
                    "addressable_p50": round(addressable_p50, 2),
                    "addressable_p90": round(addressable_p90, 2),
                    "observed": round(observed, 2) if pd.notna(observed) else np.nan,
                    "share_p50": round(share_p50, 4) if pd.notna(share_p50) else np.nan,
                    "unaddressed_p50": round(unaddressed_p50, 2) if pd.notna(unaddressed_p50) else np.nan,
                    "confidence": round(confidence, 3),
                    "propensity": propensity,
                }
            )

    df = pd.DataFrame(rows)

    # Layer 4: OpportunityScore = 0.5*norm(Unaddressed_P50) + 0.3*Confidence + 0.2*Propensity,
    # normalised within each pillar (never compare raw Rands across pillars).
    def normalise(series: pd.Series) -> pd.Series:
        filled = series.fillna(0.0)
        span = filled.max() - filled.min()
        return (filled - filled.min()) / span if span > 0 else filled * 0.0

    df["opportunity_score"] = 0.0
    for pillar in PILLARS:
        mask = df["pillar"] == pillar
        norm_unaddressed = normalise(df.loc[mask, "unaddressed_p50"])
        df.loc[mask, "opportunity_score"] = (
            0.5 * norm_unaddressed + 0.3 * df.loc[mask, "confidence"] + 0.2 * df.loc[mask, "propensity"]
        )

    df = df.drop(columns=["propensity"])
    df["rank"] = df["opportunity_score"].rank(ascending=False, method="first").astype(int)
    df = df.sort_values("rank").reset_index(drop=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-finance-csv", type=Path, default=Path("data/trade_finance.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/wallet_results_DUMMY.csv"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = generate(args.trade_finance_csv, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"wallet_results_DUMMY.csv: {len(df)} rows ({len(PILLARS)} pillars x {len(df) // len(PILLARS)} entities)")


if __name__ == "__main__":
    main()
