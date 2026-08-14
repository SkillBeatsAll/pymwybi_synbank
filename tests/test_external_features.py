"""External financials: fiscal-period alignment, coverage and gap handling."""

from __future__ import annotations

from src.syn_wallet import config

from .conftest import requires_full_data


@requires_full_data
def test_fiscal_periods_align_with_the_entity_register(built) -> None:
    """entities.csv is authoritative for fy_label, currency and year end; the
    normalised financials must not disagree with it."""
    mismatches = built.execute(
        """
        SELECT COUNT(*) FROM ext_norm n
        JOIN entity_dim e USING (entity_id)
        WHERE n.fy_label <> e.fy_label
           OR (n.reporting_currency IS NOT NULL
               AND n.reporting_currency <> e.reporting_currency)
        """
    ).fetchone()[0]
    assert mismatches == 0


@requires_full_data
def test_fiscal_windows_are_well_formed_and_inside_the_flow_window(built) -> None:
    violations = built.execute(
        """
        SELECT COUNT(*) FROM entity_dim
        WHERE fy_start <> CAST(fiscal_year_end - INTERVAL 1 YEAR + INTERVAL 1 DAY AS DATE)
           OR fy_start < DATE '2023-07-01'
           OR fiscal_year_end > DATE '2026-06-30'
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_five_distinct_year_ends_span_nine_months(features) -> None:
    """Aligning 36 months of flow to 20 reporting periods is a five-bucket
    problem, not three -- E11 and E12 sit on year ends no other client shares."""
    year_ends = features.execute(
        "SELECT fiscal_year_end, COUNT(*) FROM client_master GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert [(str(date), count) for date, count in year_ends] == [
        ("2025-06-30", 6),
        ("2025-08-31", 1),
        ("2025-09-30", 1),
        ("2025-12-31", 9),
        ("2026-03-31", 3),
    ]


@requires_full_data
def test_a_revenue_denominator_exists_for_every_client(features) -> None:
    missing = features.execute(
        "SELECT COUNT(*) FROM client_master WHERE revenue_total_zar IS NULL OR revenue_total_zar <= 0"
    ).fetchone()[0]
    assert missing == 0


@requires_full_data
def test_gaps_are_preserved_rather_than_zeroed(features) -> None:
    """The wide CSV's 86 absent cells are indistinguishable from its 10 genuine
    zeros after any fillna. The long store keeps them apart, and so must this."""
    zeroed_gaps = features.execute(
        "SELECT COUNT(*) FROM external_financials_zar WHERE NOT is_usable AND value_zar IS NOT NULL"
    ).fetchone()[0]
    assert zeroed_gaps == 0

    genuine_zeros = features.execute(
        "SELECT COUNT(*) FROM external_financials_zar WHERE is_usable AND value_native = 0"
    ).fetchone()[0]
    assert genuine_zeros == 10

    explained = features.execute(
        "SELECT COUNT(*) FROM external_financials_zar "
        "WHERE NOT is_usable AND unit_type = 'currency' AND gap_reason IS NULL AND value_text IS NULL"
    ).fetchone()[0]
    assert explained == 0


@requires_full_data
def test_status_and_basis_survive_into_the_feature_layer(features) -> None:
    """basis, not source provenance, is the denominator-quality signal that
    matters. A model must be able to down-weight a constructed revenue total."""
    unknown = features.execute(
        "SELECT COUNT(*) FROM external_financials_zar WHERE status NOT IN "
        + "(" + ", ".join(f"'{value}'" for value in config.KNOWN_STATUSES) + ")"
    ).fetchone()[0]
    assert unknown == 0

    soft = dict(
        features.execute(
            "SELECT entity_id, revenue_total_basis FROM client_master "
            "WHERE revenue_total_is_soft_basis"
        ).fetchall()
    )
    assert soft == {
        "E06": "commentary",
        "E08": "constructed",
        "E09": "pro_forma",
        "E10": "commentary",
    }


@requires_full_data
def test_identity_checks_flag_the_three_known_revenue_split_failures(features) -> None:
    """E08, E18 and E06 do not reconcile; a geographic wallet split built on
    their revenue legs will not add up."""
    failures = features.execute(
        "SELECT entity_id FROM client_master WHERE revenue_split_identity_ok = FALSE ORDER BY 1"
    ).fetchall()
    assert [row[0] for row in failures] == ["E06", "E08", "E18"]

    debt_failures = features.execute(
        "SELECT COUNT(*) FROM client_master WHERE gross_debt_identity_ok = FALSE"
    ).fetchone()[0]
    assert debt_failures == 0


@requires_full_data
def test_wide_csv_remains_a_faithful_display_only_projection(feature_run) -> None:
    assert feature_run["external_coverage"]["wide_projection_discrepancies"] == 0


@requires_full_data
def test_derived_balances_follow_their_components(features) -> None:
    mismatches = features.execute(
        """
        SELECT COUNT(*) FROM client_master
        WHERE (net_debt_zar IS NOT NULL
               AND ABS(net_debt_zar - (gross_debt_zar - cash_and_equivalents_zar)) > 1e-6)
           OR (working_capital_zar IS NOT NULL
               AND ABS(working_capital_zar
                       - (trade_receivables_zar + inventory_zar - trade_payables_zar)) > 1e-6)
        """
    ).fetchone()[0]
    assert mismatches == 0


@requires_full_data
def test_named_lenders_are_carried_as_competitor_evidence(features) -> None:
    """The banks a client names in its facilities note are read against the
    competitor-lending memos in the transactional ledger."""
    named = features.execute(
        "SELECT entity_id, named_lender_count FROM client_master "
        "WHERE lenders_named IS NOT NULL ORDER BY entity_id"
    ).fetchall()
    assert [row[0] for row in named] == ["E08", "E10", "E13", "E16"]
    assert all(count >= 1 for _, count in named)


@requires_full_data
def test_every_entity_carries_every_field(features) -> None:
    incomplete = features.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT entity_id, COUNT(DISTINCT field) AS n FROM external_financials_zar
            GROUP BY entity_id
        ) WHERE n <> {len(config.EXTERNAL_FIELDS)}
        """
    ).fetchone()[0]
    assert incomplete == 0
