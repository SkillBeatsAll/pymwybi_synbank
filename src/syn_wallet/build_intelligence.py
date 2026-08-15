"""Stage 4 entry point: build the commercial intelligence layer.

::

    python -m src.syn_wallet.build_intelligence --overwrite

Reads only the analytical contract -- ``opportunity_engine.parquet`` and
``client_opportunity_profile.parquet`` -- plus ``model_sensitivity.parquet``
where it exists, and writes the banker-facing tables beside them in both Parquet
(for analysis) and JSON (for an API or dashboard).

If the sensitivity sweep has not been run, the layer still builds: every
sensitivity field comes out NULL and every flag reads ``NOT_APPLICABLE``, so the
output says "not tested" rather than implying stability it cannot demonstrate.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from . import config
from .intelligence import config as intelligence_config
from .intelligence import engine

#: The two contract tables. Nothing else in ``data/processed`` is an input to
#: this stage except the sensitivity sweep.
CONTRACT_INPUTS = ("opportunity_engine", "client_opportunity_profile")
SENSITIVITY_INPUT = "model_sensitivity"

#: Written as both Parquet and JSON.
INTELLIGENCE_OUTPUTS = (
    "client_opportunity_intelligence",
    "portfolio_opportunity_intelligence",
    "banker_questions",
    "opportunity_explanations",
)

#: Written as Parquet only -- supporting detail rather than published contract.
SUPPORTING_OUTPUTS = (
    "client_opportunity_cards",
    "opportunity_selection_detail",
    "opportunity_sensitivity_summary",
)

INTELLIGENCE_REPORT_NAME = "intelligence_report.json"


def load_contract(processed_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Read the contract, and the sensitivity sweep if it has been built."""
    connection = duckdb.connect(":memory:")
    try:
        frames = []
        for name in CONTRACT_INPUTS:
            path = processed_dir / f"{name}.parquet"
            if not path.is_file():
                raise FileNotFoundError(
                    f"Missing {path}. Build the wallet engine first: "
                    "`python -m src.syn_wallet.build_wallet --overwrite --sensitivity`."
                )
            frames.append(connection.execute(f"SELECT * FROM read_parquet('{path}')").df())

        sensitivity_path = processed_dir / f"{SENSITIVITY_INPUT}.parquet"
        sensitivity = (
            connection.execute(f"SELECT * FROM read_parquet('{sensitivity_path}')").df()
            if sensitivity_path.is_file()
            else None
        )
        return frames[0], frames[1], sensitivity
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


def _json_safe(value: Any) -> Any:
    """Convert one cell to something ``json.dumps`` will accept.

    NaN becomes ``null`` rather than the bare token ``NaN``, which is what
    pandas' own JSON writer emits and which no strict JSON parser will read.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def frame_to_json(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """A list of records with JSON-legal values, ready for an API response."""
    return [
        {column: _json_safe(value) for column, value in zip(frame.columns, row)}
        for row in frame.itertuples(index=False, name=None)
    ]


def run(
    processed_dir: Path | None = None,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build every intelligence output and return the run report."""
    processed_dir = (processed_dir or config.PROCESSED_DIR).resolve()
    output_dir = (output_dir or config.PROCESSED_DIR).resolve()

    targets = [output_dir / f"{name}.parquet" for name in INTELLIGENCE_OUTPUTS]
    targets += [output_dir / f"{name}.json" for name in INTELLIGENCE_OUTPUTS]
    targets += [output_dir / f"{name}.parquet" for name in SUPPORTING_OUTPUTS]
    targets.append(output_dir / INTELLIGENCE_REPORT_NAME)
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"Output already exists ({names}). Re-run with --overwrite.")

    engine_table, client_profile, sensitivity = load_contract(processed_dir)
    intelligence = engine.run(engine_table, client_profile, sensitivity)

    output_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "client_opportunity_intelligence": intelligence.client_intelligence,
        "portfolio_opportunity_intelligence": intelligence.portfolio_intelligence,
        "banker_questions": intelligence.banker_questions,
        "opportunity_explanations": intelligence.opportunity_explanations,
        "client_opportunity_cards": intelligence.client_cards,
        "opportunity_selection_detail": intelligence.opportunity_detail,
        "opportunity_sensitivity_summary": intelligence.sensitivity,
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

    written_json: dict[str, str] = {}
    for name in INTELLIGENCE_OUTPUTS:
        path = output_dir / f"{name}.json"
        path.write_text(
            json.dumps(frame_to_json(frames[name]), indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        written_json[name] = str(path)

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "inputs": {
            "contract": [str(processed_dir / f"{name}.parquet") for name in CONTRACT_INPUTS],
            "sensitivity": (
                str(processed_dir / f"{SENSITIVITY_INPUT}.parquet")
                if sensitivity is not None
                else None
            ),
        },
        "policy": {
            "deterministic": (
                "Every sentence is a template filled from a published field. No LLM is called and "
                "no value is invented, so identical inputs always produce identical output."
            ),
            "contract_only": (
                "The only analytical inputs are opportunity_engine.parquet, "
                "client_opportunity_profile.parquet and model_sensitivity.parquet."
            ),
            "no_cross_pillar_total": (
                "No output sums rand across the five pillars. Two of them overlap on the SWIFT "
                "channel by an unresolvable amount and the five bases are not commensurable."
            ),
            "no_ownership_claim": (
                "An opportunity is addressable activity not observed in Syn Bank's data. It is "
                "never described as competitor-held, lost, or confirmed revenue."
            ),
            "no_share_for_signal_pillars": (
                "Lending and investment banking never receive share-of-wallet language. Lending "
                "publishes a financing opportunity; investment banking publishes a signal."
            ),
            "low_confidence_cannot_be_priority": (
                "PRIORITY requires HIGH confidence or a named, reasoned override. No combination "
                "of size and score can promote a LOW-confidence estimate."
            ),
        },
        "outputs": written,
        "outputs_json": written_json,
        **intelligence.report,
    }
    (output_dir / INTELLIGENCE_REPORT_NAME).write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the deterministic commercial intelligence layer."
    )
    parser.add_argument("--processed-dir", type=Path, default=config.PROCESSED_DIR)
    parser.add_argument("--output-dir", type=Path, default=config.PROCESSED_DIR)
    parser.add_argument("--overwrite", action="store_true", help="Replace existing outputs.")
    args = parser.parse_args()
    report = run(
        processed_dir=args.processed_dir, output_dir=args.output_dir, overwrite=args.overwrite
    )
    print(
        f"{report['intelligence_version']} on {report['methodology_version']}: "
        f"{report['clients']} clients, "
        f"{report['clients_with_primary_opportunity']} with a primary opportunity, "
        f"{report['explanations_generated']} explanations, "
        f"{report['questions_generated']} banker questions"
    )
    for status in intelligence_config.STATUS_ORDER:
        print(f"  {status:<26} {report['status_counts'].get(status, 0):>3} client-product rows")
    if not report["scenarios_tested"]:
        print(
            "  NOTE: model_sensitivity.parquet was absent, so every sensitivity field is NULL. "
            "Rebuild stage 3 with --sensitivity."
        )
    for name, path in report["outputs"].items():
        print(f"  wrote {name} -> {path}")


if __name__ == "__main__":
    main()
