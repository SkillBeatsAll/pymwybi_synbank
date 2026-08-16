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
from .wallet import assumptions, confidence, contract, engine, sensitivity

WALLET_OUTPUTS = (
    # The analytical contract. Downstream applications read these two and
    # nothing else.
    "opportunity_engine",
    "client_opportunity_profile",
    # Supporting detail, for the model report and for anyone auditing a number.
    "wallet_estimates",
    "opportunities",
    "wallet_components",
    "wallet_confidence_detail",
    "model_diagnostics",
    "portfolio_summary",
    "product_classification",
    "product_confidence",
    "model_assumptions",
    "model_benchmarks",
    "model_benchmark_metrics",
    "model_sector_rules",
)

#: Written by ``--sensitivity``, which rebuilds the engine 36 times and takes
#: several seconds, so it is off by default.
SENSITIVITY_OUTPUTS = (
    "model_sensitivity",
    "model_sensitivity_summary",
    "model_sensitivity_by_product",
    "model_sensitivity_robustness",
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
    with_sensitivity: bool = False,
) -> dict[str, Any]:
    """Build every wallet output and return the run report."""
    processed_dir = (processed_dir or config.PROCESSED_DIR).resolve()
    output_dir = (output_dir or config.PROCESSED_DIR).resolve()

    names = list(WALLET_OUTPUTS) + (list(SENSITIVITY_OUTPUTS) if with_sensitivity else [])
    targets = [output_dir / f"{name}.parquet" for name in names]
    targets += [output_dir / MODEL_REPORT_NAME, output_dir / WORKED_EXAMPLES_NAME]
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"Output already exists ({names}). Re-run with --overwrite.")

    features = load_features(processed_dir)
    model = engine.run(features)

    output_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "opportunity_engine": model.opportunity_engine,
        "client_opportunity_profile": model.client_profiles,
        "wallet_estimates": model.estimates,
        "opportunities": model.opportunities,
        "wallet_components": model.components,
        "wallet_confidence_detail": model.confidence_detail,
        "model_diagnostics": model.diagnostics,
        "portfolio_summary": model.portfolio_summary,
        "product_classification": model.product_classification,
        "product_confidence": model.product_confidence,
        "model_assumptions": model.assumption_registry,
        "model_benchmarks": model.benchmarks,
        "model_benchmark_metrics": model.benchmark_metrics,
        "model_sector_rules": model.sector_rules,
    }

    sensitivity_report: dict[str, Any] = {}
    if with_sensitivity:
        result = sensitivity.run(features)
        frames.update(
            {
                "model_sensitivity": result.detail,
                "model_sensitivity_summary": result.summary,
                "model_sensitivity_by_product": result.product_summary,
                "model_sensitivity_robustness": result.robustness,
            }
        )
        sensitivity_report = {
            "scenarios": int(len(result.scenarios)),
            "base_scenario": sensitivity.base_config().label,
            "robustness": result.robustness.to_dict(orient="records"),
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
            "no_self_benchmarking": (
                "No client contributes to the peer population that sets its own coefficient. A "
                "sector benchmark needs three peers after that exclusion, or the portfolio "
                "benchmark is used and the reason is recorded per client."
            ),
            "flow_is_not_revenue": (
                "addressable_cash_flow_zar is the client's own operating turnover. The fee wallet "
                "on it, cash_management_wallet_zar, is NULL for every client because Syn Bank "
                "discloses no pricing."
            ),
        },
        "confidence_weights": confidence.WEIGHTS,
        "confidence_registry": confidence.weights_registry(),
        "worked_example_entities": list(EXAMPLE_ENTITIES),
        "analytical_contract": {
            "opportunity_engine_columns": list(contract.OPPORTUNITY_ENGINE_COLUMNS),
            "grain": "one row per client x product",
            "null_policy": (
                "A product with no defensible rand denominator keeps NULL in every rand column. "
                "Never zero: 'we cannot size this' and 'this is worth nothing' are opposite "
                "statements."
            ),
        },
        "sensitivity": sensitivity_report,
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
    parser.add_argument(
        "--sensitivity",
        action="store_true",
        help="Also run the 36-scenario sensitivity sweep (several seconds).",
    )
    args = parser.parse_args()
    report = run(
        processed_dir=args.processed_dir,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
        with_sensitivity=args.sensitivity,
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
