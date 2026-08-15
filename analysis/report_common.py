"""Shared plumbing for the generated markdown reports.

Every report in this repository is written by a script that reads
``data/processed/*.parquet`` back off disk. Nothing is typed by hand, so a report
cannot claim a coefficient the engine does not use or a result it did not
produce. This module holds the parts all of them need.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from src.syn_wallet import config

#: Rand at a scale a reader can hold in their head. R14,313.25bn reads as noise;
#: R14.31tn does not.
ZAR_MACRO = (
    "CREATE OR REPLACE MACRO zar(value) AS CASE "
    "WHEN value IS NULL THEN 'n/a' "
    "WHEN ABS(value) >= 1e12 THEN printf('R%.2ftn', value / 1e12) "
    "WHEN ABS(value) >= 1e9 THEN printf('R%.2fbn', value / 1e9) "
    "WHEN ABS(value) >= 1e6 THEN printf('R%.1fm', value / 1e6) "
    "ELSE printf('R%.0f', value) END"
)


def connect(tables: tuple[str, ...]) -> duckdb.DuckDBPyConnection:
    """A connection with each named Parquet registered as a view."""
    connection = duckdb.connect(":memory:")
    connection.execute(ZAR_MACRO)
    for name in tables:
        path = config.PROCESSED_DIR / f"{name}.parquet"
        if not path.is_file():
            raise FileNotFoundError(
                f"Missing {path}. Run `python -m src.syn_wallet.build_wallet "
                "--overwrite --sensitivity` first."
            )
        connection.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    return connection


def table(frame: pd.DataFrame) -> str:
    """Render a DataFrame as a GitHub-flavoured markdown table."""
    if frame.empty:
        return "_None._\n"
    header = "| " + " | ".join(str(column) for column in frame.columns) + " |"
    divider = "|" + "|".join("---" for _ in frame.columns) + "|"
    rows = [
        "| " + " | ".join("" if pd.isna(value) else str(value) for value in row) + " |"
        for row in frame.itertuples(index=False)
    ]
    return "\n".join([header, divider, *rows]) + "\n"


def model_report() -> dict[str, Any]:
    """The run report the engine wrote alongside the Parquet outputs."""
    path = config.PROCESSED_DIR / "model_report.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}. Build the wallet engine first.")
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")
