"""End-to-end feature layer: joins, cardinality, nulls, ratios and policy guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.syn_wallet import build_features, config, ratios, validation

from .conftest import requires_full_data

# ---------------------------------------------------------------------------
# Declarations, checkable without data
# ---------------------------------------------------------------------------


def test_ratio_definitions_are_unique_and_documented() -> None:
    names = ratios.ratio_names()
    assert len(names) == len(set(names))
    assert all(ratio.rationale for ratio in ratios.RATIOS)


def test_ratio_numerators_are_fiscal_year_scoped() -> None:
    """A 36-month numerator over a 12-month denominator would inflate every
    ratio roughly threefold, unevenly across clients with different year ends."""
    for ratio in ratios.RATIOS:
        if ratio.numerator.startswith(("txn_", "xb_", "tf_")):
            assert ratio.numerator.endswith("_fy"), ratio.name
        assert not ratio.numerator.endswith(("_36m", "_r12m", "_p12m")), ratio.name


def test_ratio_denominators_are_zar_converted() -> None:
    """Nine of twenty clients report in USD, EUR or GBP. A ZAR numerator over a
    native denominator understates them by 17-24x."""
    exempt = {"employees"}
    for ratio in ratios.RATIOS:
        if ratio.denominator in exempt:
            continue
        assert ratio.denominator.endswith("_zar"), ratio.name
        assert not ratio.denominator.endswith("_native"), ratio.name


def test_required_columns_are_declared_without_duplicates() -> None:
    assert len(config.REQUIRED_FEATURE_COLUMNS) == len(set(config.REQUIRED_FEATURE_COLUMNS))


# ---------------------------------------------------------------------------
# The built layer
# ---------------------------------------------------------------------------


@requires_full_data
def test_all_validation_checks_pass(feature_run) -> None:
    report = feature_run["validation"]
    failures = [check for check in report["results"] if not check["passed"]]
    assert failures == [], failures
    assert report["checks_run"] > 40


@requires_full_data
def test_expected_client_count_in_every_output(features) -> None:
    for table in ("client_master", "client_features"):
        rows, distinct = features.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT entity_id) FROM {table}"
        ).fetchone()
        assert rows == distinct == config.EXPECTED_ENTITY_COUNT

    rows, entities, fields = features.execute(
        "SELECT COUNT(*), COUNT(DISTINCT entity_id), COUNT(DISTINCT field) "
        "FROM external_financials_zar"
    ).fetchone()
    assert (rows, entities, fields) == (
        config.EXPECTED_ENTITY_COUNT * len(config.EXTERNAL_FIELDS),
        config.EXPECTED_ENTITY_COUNT,
        len(config.EXTERNAL_FIELDS),
    )


@requires_full_data
def test_entity_joins_neither_drop_nor_invent_a_client(built) -> None:
    unmatched = built.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT entity_id, entity_name FROM txn
            UNION SELECT entity_id, entity_name FROM xb
            UNION SELECT entity_id, entity_name FROM tf
            EXCEPT
            SELECT entity_id, entity_name FROM entity_dim
        )
        """
    ).fetchone()[0]
    assert unmatched == 0

    orphans = built.execute(
        "SELECT COUNT(*) FROM entity_dim WHERE entity_id NOT IN "
        "(SELECT entity_id FROM entities_src)"
    ).fetchone()[0]
    assert orphans == 0

    unjoined_external = built.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM ext_norm "
        "WHERE entity_id NOT IN (SELECT entity_id FROM client_master)"
    ).fetchone()[0]
    assert unjoined_external == 0


@requires_full_data
def test_no_accidental_duplication_in_the_join_chain(features) -> None:
    """The feature table is built by joining a dozen by-scope projections. A fan-out
    would inflate every aggregate silently, so cardinality is asserted directly."""
    duplicates = features.execute(
        "SELECT COUNT(*) FROM (SELECT entity_id FROM client_features "
        "GROUP BY entity_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert duplicates == 0

    external_duplicates = features.execute(
        "SELECT COUNT(*) FROM (SELECT entity_id, field FROM external_financials_zar "
        "GROUP BY 1, 2 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert external_duplicates == 0


@requires_full_data
def test_expected_sector_population(features) -> None:
    observed = dict(
        features.execute(
            "SELECT sector, COUNT(*) FROM client_features GROUP BY sector"
        ).fetchall()
    )
    assert set(observed) == set(config.EXPECTED_SECTORS)
    assert sum(observed.values()) == config.EXPECTED_ENTITY_COUNT
    assert all(count > 0 for count in observed.values())
    assert features.execute(
        "SELECT COUNT(*) FROM client_features WHERE sector IS NULL"
    ).fetchone()[0] == 0


@requires_full_data
def test_no_unexplained_nulls_in_required_fields(features) -> None:
    condition = " OR ".join(f'"{column}" IS NULL' for column in config.REQUIRED_FEATURE_COLUMNS)
    assert features.execute(
        f"SELECT COUNT(*) FROM client_features WHERE {condition}"
    ).fetchone()[0] == 0


@requires_full_data
def test_every_null_ratio_traces_to_an_absent_input(features) -> None:
    """A NULL ratio must trace to a disclosed data gap in its numerator or its
    denominator, never to a pipeline defect."""
    unexplained = []
    for ratio in ratios.RATIOS:
        rows = features.execute(
            f"SELECT COUNT(*) FROM client_features WHERE {ratio.name} IS NULL "
            f"AND {ratio.numerator} IS NOT NULL "
            f"AND {ratio.denominator} IS NOT NULL AND {ratio.denominator} > 0"
        ).fetchone()[0]
        if rows:
            unexplained.append((ratio.name, rows))
    assert unexplained == []


@requires_full_data
def test_headline_ratios_are_populated_for_every_client(features) -> None:
    """revenue_total is available for all 20 clients, so any ratio denominated
    on it must be too."""
    for name in ("txn_volume_to_revenue", "cross_border_volume_to_revenue",
                 "collections_to_revenue", "debt_to_revenue", "capex_to_revenue"):
        nulls = features.execute(
            f"SELECT COUNT(*) FROM client_features WHERE {name} IS NULL"
        ).fetchone()[0]
        assert nulls == 0, name


@requires_full_data
def test_ratios_recompute_from_their_own_columns(features) -> None:
    """Guards against a ratio expression drifting from the columns it names."""
    for ratio in ratios.RATIOS:
        mismatches = features.execute(
            f"""
            SELECT COUNT(*) FROM client_features
            WHERE {ratio.denominator} > 0
              AND ABS({ratio.name} - CAST({ratio.numerator} AS DOUBLE)
                      / CAST({ratio.denominator} AS DOUBLE)) > 1e-9
            """
        ).fetchone()[0]
        assert mismatches == 0, ratio.name


@requires_full_data
def test_pillars_are_never_summed(features) -> None:
    """The transactional and cross-border pillars overlap on 279,389 SWIFT-channel
    rows by an amount the supplied fields cannot resolve. No feature may add them."""
    columns = [row[0] for row in features.execute("DESCRIBE client_features").fetchall()]
    banned = [name for name in columns
              if any(token in name.lower() for token in ("combined", "all_pillar", "total_flow"))]
    assert banned == []

    blended = features.execute(
        """
        SELECT COUNT(*) FROM client_features
        WHERE ABS(CAST(txn_total_volume_zar_36m AS DOUBLE)
                  + CAST(xb_total_volume_zar_36m AS DOUBLE)
                  - CAST(txn_total_volume_zar_36m AS DOUBLE)) < 0
        """
    ).fetchone()[0]
    assert blended == 0


@requires_full_data
def test_no_pricing_assumption_leaks_into_the_features(features) -> None:
    """Syn Bank is fictional and has no disclosed pricing. Any fee, margin or
    basis-point column would be invented."""
    columns = [row[0].lower() for row in features.execute("DESCRIBE client_features").fetchall()]
    forbidden = ("fee", "margin", "bps", "basis_point", "revenue_estimate", "wallet", "share_of_wallet")
    offenders = [name for name in columns if any(token in name for token in forbidden)]
    assert offenders == []


@requires_full_data
def test_run_report_records_the_conversion_and_period_policy(feature_run) -> None:
    policy = feature_run["policy"]
    assert "average" in policy["fx_flow_fields"]
    assert "closing" in policy["fx_stock_fields"]
    assert "fiscal-year" in policy["period_alignment"]
    assert "never summed" in policy["pillar_policy"]


@requires_full_data
def test_pipeline_refuses_to_overwrite_without_the_flag(tmp_path: Path) -> None:
    build_features.run(output_dir=tmp_path, overwrite=True)
    with pytest.raises(FileExistsError):
        build_features.run(output_dir=tmp_path)


def test_validation_report_raises_on_failure() -> None:
    report = validation.ValidationReport(
        checks=[
            validation.Check("a", True, "fine"),
            validation.Check("b", False, "broken", {"rows": 3}),
        ]
    )
    assert not report.passed
    with pytest.raises(validation.ValidationError, match="broken"):
        validation.assert_all(report)
    validation.assert_all(validation.ValidationReport(checks=[validation.Check("a", True, "ok")]))
