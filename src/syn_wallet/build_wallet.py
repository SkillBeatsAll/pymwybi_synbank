"""Stage 3 entry point: build the wallet and opportunity engine.

::

    python -m src.syn_wallet.build_wallet --overwrite

Reads ``data/processed/client_features.parquet`` and writes the wallet outputs
beside it. DuckDB does the Parquet I/O; the models themselves run in pandas,
where twenty rows of branching economic logic and generated English are far
easier to read and to review than the equivalent SQL.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from . import config
from .wallet import assumptions, confidence, engine

WALLET_OUTPUTS = (
    "wallet_estimates",
    "opportunities",
    "wallet_components",
    "wallet_confidence_detail",
    "model_diagnostics",
    "portfolio_summary",
    "model_assumptions",
    "model_benchmarks",
    "model_sector_rules",
)

MODEL_REPORT_NAME = "model_report.json"
WORKED_EXAMPLES_NAME = "worked_examples.json"

#: Clients used for the worked examples in the model report. Chosen to span the
#: three situations a reviewer most needs to see: a well-observed domestic
#: retailer, a foreign-currency reporter whose drivers are largely imputed, and
#: a client whose sector suppresses two sub-models.
EXAMPLE_ENTITIES = ("E09", "E17", "E08")


def load_features(processed_dir: Path) -> pd.DataFrame:
    """Read the feature table into pandas via DuckDB."""
    path = processed_dir / "client_features.parquet"
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {path}. Build the feature layer first: "
            "`python -m src.syn_wallet.build_features --overwrite`."
        )
    connection = duckdb.connect(":memory:")
    try:
        return connection.execute(f"SELECT * FROM read_parquet('{path}')").df()
    finally:
        connection.close()


def write_frame(connection: duckdb.DuckDBPyConnection, frame: pd.DataFrame, path: Path) -> None:
    """Write one frame to ZSTD Parquet without a pyarrow dependency."""
    connection.register("frame_to_write", frame)
    try:
        connection.execute(
            f"COPY (SELECT * FROM frame_to_write) TO '{path}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        connection.unregister("frame_to_write")


def run(
    processed_dir: Path | None = None,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build every wallet output and return the run report."""
    processed_dir = (processed_dir or config.PROCESSED_DIR).resolve()
    output_dir = (output_dir or config.PROCESSED_DIR).resolve()

    targets = [output_dir / f"{name}.parquet" for name in WALLET_OUTPUTS]
    targets += [output_dir / MODEL_REPORT_NAME, output_dir / WORKED_EXAMPLES_NAME]
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"Output already exists ({names}). Re-run with --overwrite.")

    features = load_features(processed_dir)
    model = engine.run(features)

    output_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "wallet_estimates": model.estimates,
        "opportunities": model.opportunities,
        "wallet_components": model.components,
        "wallet_confidence_detail": model.confidence_detail,
        "model_diagnostics": model.diagnostics,
        "portfolio_summary": model.portfolio_summary,
        "model_assumptions": model.assumption_registry,
        "model_benchmarks": model.benchmarks,
        "model_sector_rules": model.sector_rules,
    }
    connection = duckdb.connect(":memory:")
    written: dict[str, str] = {}
    try:
        for name, frame in frames.items():
            path = output_dir / f"{name}.parquet"
            write_frame(connection, frame, path)
            written[name] = str(path)
    finally:
        connection.close()

    examples = [
        engine.worked_example(model, features, entity_id) for entity_id in EXAMPLE_ENTITIES
    ]
    (output_dir / WORKED_EXAMPLES_NAME).write_text(
        json.dumps(examples, indent=2, default=str) + "\n", encoding="utf-8"
    )

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "methodology_version": assumptions.METHODOLOGY_VERSION,
        "policy": {
            "no_pricing": (
                "No fee, margin or basis-point assumption is applied. Every figure is a flow or "
                "balance magnitude, never bank revenue."
            ),
            "no_competitor_wallet": (
                "A gap is addressable business not observed in Syn Bank's supplied data. It is "
                "never described as confirmed competitor-held business."
            ),
            "no_pillar_blending": (
                "The five pillars are estimated and reported separately. The transactional and "
                "cross-border pillars are never summed."
            ),
            "swift_overlap": (
                "SWIFT-channel transactional volume is excluded from the cash numerator and not "
                "added to the FX numerator, so it is counted in neither pillar. The amount is "
                "published per client."
            ),
            "null_handling": (
                "A missing driver is resolved through a documented cascade or left NULL. It is "
                "never imputed to zero, and a NULL denominator produces a NULL share, not a "
                "division."
            ),
            "no_machine_learning": (
                "Every estimate is a transparent arithmetic function of declared assumptions and "
                "feature values, reproducible by hand from the component breakdown."
            ),
        },
        "confidence_weights": confidence.WEIGHTS,
        "confidence_registry": confidence.weights_registry(),
        "worked_example_entities": list(EXAMPLE_ENTITIES),
        "outputs": written,
        **model.report,
    }
    (output_dir / MODEL_REPORT_NAME).write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the share-of-wallet engine.")
    parser.add_argument("--processed-dir", type=Path, default=config.PROCESSED_DIR)
    parser.add_argument("--output-dir", type=Path, default=config.PROCESSED_DIR)
    parser.add_argument("--overwrite", action="store_true", help="Replace existing outputs.")
    args = parser.parse_args()
    report = run(
        processed_dir=args.processed_dir, output_dir=args.output_dir, overwrite=args.overwrite
    )
    print(
        f"wallet engine {report['methodology_version']}: "
        f"{report['clients']} clients x {len(report['products'])} products "
        f"= {report['estimate_rows']} estimates"
    )
    for row in report["portfolio_summary"]:
        share = row["portfolio_share"]
        share_text = f"{share:.4%}" if share is not None and pd.notna(share) else "n/a"
        print(
            f"  {row['product']:<20} observed "
            f"{_bn(row['total_observed_zar']):>12}  estimate "
            f"{_bn(row['total_estimate_zar']):>12}  share {share_text:>9}  "
            f"mean confidence {row['mean_confidence']:.2f}"
        )
    findings = report["diagnostics"]
    print(
        f"  diagnostics: {findings['total_findings']} findings "
        f"({findings['by_severity'].get('HIGH', 0)} HIGH, "
        f"{findings['by_severity'].get('MEDIUM', 0)} MEDIUM)"
    )
    for name, path in report["outputs"].items():
        print(f"  wrote {name} -> {path}")


def _bn(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    return f"R{value / 1e9:,.2f}bn"


if __name__ == "__main__":
    main()
