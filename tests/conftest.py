"""Shared fixtures for the feature-layer tests.

The feature layer builds in well under a second against the full dataset, so the
suite runs against the real cleaned Parquet and the real prepared financials
rather than a synthetic stand-in. Tests that need the full data skip cleanly
when it is absent; the pure-Python configuration tests always run.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.syn_wallet import build_features, config, sources

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def missing_feature_inputs() -> list[Path]:
    return sources.missing_sources(config.PROCESSED_DIR, config.FINANCES_DIR)


requires_full_data = pytest.mark.skipif(
    bool(missing_feature_inputs()),
    reason=(
        "analytical inputs absent ("
        + ", ".join(path.name for path in missing_feature_inputs())
        + "); restore with `tar -xzf data/data.tgz -C data/` then "
        "`python -m src.syn_wallet.clean_data --overwrite`"
    ),
)


@pytest.fixture(scope="session")
def feature_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build the whole feature layer once into a temporary directory.

    Never writes to ``data/processed``, so a test run cannot disturb the
    committed outputs.
    """
    if missing_feature_inputs():
        pytest.skip("analytical inputs absent")
    output_dir = tmp_path_factory.mktemp("features")
    return build_features.run(output_dir=output_dir, overwrite=True, strict=False)


@pytest.fixture(scope="session")
def features(feature_run: dict) -> duckdb.DuckDBPyConnection:
    """A connection with every generated Parquet output registered as a view."""
    connection = duckdb.connect(":memory:")
    for name, path in feature_run["outputs"].items():
        connection.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def built() -> duckdb.DuckDBPyConnection:
    """A live connection holding every intermediate table, not just the outputs.

    Lets tests assert on ``entity_dim``, ``fy_fx_rates``, ``entity_windows`` and
    the by-scope feature tables, which are not written to disk.
    """
    if missing_feature_inputs():
        pytest.skip("analytical inputs absent")
    from src.syn_wallet import external_features, fx, internal_features

    connection = sources.connect()
    build_features.build_entity_dim(connection)
    fx.build(connection)
    external_features.build(connection)
    internal_features.build(connection)
    build_features.build_client_master(connection)
    build_features.build_client_features(connection)
    yield connection
    connection.close()
