"""The commercial intelligence layer: rules, terminology and fidelity to the contract.

Two kinds of test here. The pure-function ones run against a synthetic portfolio
and are fast. The fidelity ones run against the real twenty clients, end to end
through stages 3 and 4, and check that every number a banker would read matches
the analytical contract it came from.

The terminology tests are not cosmetic. Calling Addressable Cash Flow a "fee
pool", or giving lending a share of wallet, is a commercial misstatement that
would survive review precisely because it reads fluently. They are asserted
against every generated string.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.syn_wallet.intelligence import config, engine, questions, selection
from src.syn_wallet.intelligence import sensitivity_view
from src.syn_wallet.wallet import assumptions
from src.syn_wallet.wallet import engine as wallet_engine
from src.syn_wallet.wallet import sensitivity as sweep

from .conftest import requires_full_data
from .wallet_fixtures import synthetic_features

EXPECTED_CLIENTS = 20

#: A four-scenario grid, so the synthetic fixtures exercise the sensitivity path
#: without paying for all 36 runs.
REDUCED_GRID = (
    sweep.base_config(),
    assumptions.ModelConfig(label="median_loo_sector_capex30", benchmark_percentile=0.50),
    assumptions.ModelConfig(label="p80_loo_sector_capex30", benchmark_percentile=0.80),
    assumptions.ModelConfig(
        label="p75_loo_portfolio_capex40",
        benchmark_scope=assumptions.PORTFOLIO_ONLY,
        capex_debt_funded_share=0.40,
    ),
)


def _features() -> pd.DataFrame:
    """Eight clients whose peer intensities genuinely differ.

    The default fixture scales every client uniformly, so its intensity ratios
    are identical and every percentile of them is the same number -- which would
    leave the sensitivity path untested. Varying the internal flows against
    fixed external financials is what makes one client more penetrated than
    another, which is the thing the benchmark percentile is measuring.
    """
    features = synthetic_features(count=8)
    for position in range(len(features)):
        multiplier = 1.0 + position * 0.6
        for column in (
            "xb_inbound_volume_zar_fy",
            "xb_outbound_volume_zar_fy",
            "tf_import_value_zar_fy",
            "tf_export_value_zar_fy",
            "tf_guarantees_value_zar_fy",
        ):
            features.loc[position, column] = features.loc[position, column] * multiplier
    return features


@pytest.fixture(scope="module")
def synthetic() -> engine.Intelligence:
    features = _features()
    model = wallet_engine.run(features)
    sweep_result = sweep.run(features, list(REDUCED_GRID))
    return engine.run(
        model.opportunity_engine, model.client_profiles, sweep_result.detail
    )


def _all_text(intelligence: engine.Intelligence) -> list[str]:
    """Every banker-facing string the layer produced."""
    text: list[str] = []
    for frame, columns in (
        (
            intelligence.opportunity_explanations,
            ("what", "why", "evidence", "confidence_statement", "limitation", "next_action"),
        ),
        (intelligence.banker_questions, ("question", "rationale")),
        (intelligence.client_intelligence, ("opportunity_summary", "no_opportunity_reason")),
        (intelligence.portfolio_intelligence, ("value_text", "note")),
    ):
        for column in columns:
            text.extend(str(value) for value in frame[column].dropna())
    return text


# ---------------------------------------------------------------------------
# Coverage: every client gets a profile and exactly one primary or a stated none
# ---------------------------------------------------------------------------


def test_every_client_receives_exactly_one_profile(synthetic: engine.Intelligence) -> None:
    profiles = synthetic.client_intelligence
    assert len(profiles) == 8
    assert profiles["entity_id"].is_unique
    assert profiles["entity_id"].notna().all()


def test_every_client_has_one_primary_opportunity_or_an_explicit_none(
    synthetic: engine.Intelligence,
) -> None:
    detail = synthetic.opportunity_detail
    for entity_id, group in detail.groupby("entity_id"):
        primaries = group[group["selection_slot"] == selection.PRIMARY]
        assert len(primaries) <= 1, entity_id
        profile = synthetic.client_intelligence.set_index("entity_id").loc[entity_id]
        if len(primaries) == 1:
            assert profile["has_primary_opportunity"]
            assert profile["primary_product"] == primaries.iloc[0]["product"]
            assert profile["no_opportunity_reason"] == ""
        else:
            assert not profile["has_primary_opportunity"]
            assert profile["no_opportunity_reason"]


def test_a_client_with_no_headroom_anywhere_gets_a_stated_no_opportunity_state() -> None:
    """Constructed so every pillar is fully served, which must not crash or fake one."""
    features = synthetic_features(count=8)
    # Observed activity above every modelled driver floors each wallet at
    # observed, leaving no headroom in any rand pillar.
    for column in (
        "txn_collections_domestic_volume_zar_fy",
        "txn_supplier_payments_domestic_volume_zar_fy",
        "xb_total_volume_zar_fy",
        "tf_total_value_zar_fy",
    ):
        features.loc[0, column] = 900e9
    for column in ("debt_current_zar", "undrawn_facilities_zar", "capex_zar", "working_capital_zar"):
        features.loc[0, column] = 0.0

    model = wallet_engine.run(features)
    result = engine.run(model.opportunity_engine, model.client_profiles, None)
    profile = result.client_intelligence.set_index("entity_id").loc["T01"]
    detail = result.opportunity_detail
    rand_pillars = detail[
        (detail["entity_id"] == "T01") & (detail["product"] != assumptions.IB)
    ]
    assert (rand_pillars["opportunity_status"] == config.NO_HEADROOM).all()
    # Investment banking remains as a MONITOR signal, so a primary still exists
    # -- but it is explicitly signal-only, never a rand opportunity.
    if profile["has_primary_opportunity"]:
        assert profile["primary_product"] == assumptions.IB
        assert profile["primary_status"] == config.MONITOR
        assert pd.isna(profile["primary_opportunity_zar"])
    else:
        assert profile["no_opportunity_reason"]


# ---------------------------------------------------------------------------
# Status rules
# ---------------------------------------------------------------------------


def test_low_confidence_can_never_reach_priority(synthetic: engine.Intelligence) -> None:
    detail = synthetic.opportunity_detail
    offenders = detail[
        (detail["opportunity_status"] == config.PRIORITY)
        & (detail["confidence_band"] != config.PRIORITY_REQUIRED_BAND)
    ]
    assert offenders.empty, offenders[["entity_id", "product", "confidence_band"]]


def test_priority_requires_high_confidence_at_the_rule_level() -> None:
    """Asserted on the rule itself, not only on this portfolio's output."""
    for band in ("LOW", "MEDIUM"):
        status, reason = config.classify_status(
            product_class=assumptions.CORE,
            confidence_band=band,
            commercial_score=0.99,
            high_severity_diagnostic=False,
            headroom_fraction=0.9,
            has_rand_basis=True,
        )
        assert status != config.PRIORITY, (band, reason)


def test_a_named_override_is_the_only_route_to_priority_below_high_confidence() -> None:
    key = ("T01", assumptions.FX)
    config.PRIORITY_OVERRIDES[key] = "test override, reviewed by a human"
    try:
        status, reason = config.classify_status(
            product_class=assumptions.CORE,
            confidence_band="LOW",
            commercial_score=0.99,
            high_severity_diagnostic=False,
            headroom_fraction=0.9,
            has_rand_basis=True,
            entity_id="T01",
            product=assumptions.FX,
        )
        assert status == config.PRIORITY
        assert "override" in reason
    finally:
        del config.PRIORITY_OVERRIDES[key]


def test_the_shipped_override_registry_is_empty() -> None:
    """An override is a signed human decision. None has been made."""
    assert config.PRIORITY_OVERRIDES == {}


def test_a_high_severity_diagnostic_blocks_priority() -> None:
    status, reason = config.classify_status(
        product_class=assumptions.CORE,
        confidence_band="HIGH",
        commercial_score=0.99,
        high_severity_diagnostic=True,
        headroom_fraction=0.9,
        has_rand_basis=True,
    )
    assert status == config.INVESTIGATE
    assert "HIGH-severity" in reason


def test_a_signal_only_pillar_never_rises_above_monitor(
    synthetic: engine.Intelligence,
) -> None:
    detail = synthetic.opportunity_detail
    ib = detail[detail["product"] == assumptions.IB]
    assert (ib["opportunity_status"] == config.MONITOR).all()


def test_no_headroom_is_assigned_when_the_pillar_is_fully_served() -> None:
    status, reason = config.classify_status(
        product_class=assumptions.CORE,
        confidence_band="HIGH",
        commercial_score=0.99,
        high_severity_diagnostic=False,
        headroom_fraction=0.01,
        has_rand_basis=True,
    )
    assert status == config.NO_HEADROOM
    assert "below the" in reason


def test_status_is_deterministic_and_reproducible(synthetic: engine.Intelligence) -> None:
    """Recompute every status from its published inputs and compare."""
    detail = synthetic.opportunity_detail
    engine_table = detail.set_index(["entity_id", "product"])
    for row in detail.itertuples():
        status, reason = config.classify_status(
            product_class=row.product_class,
            confidence_band=row.confidence_band,
            commercial_score=float(row.commercial_opportunity_score),
            high_severity_diagnostic=bool(row.high_severity_diagnostic),
            headroom_fraction=row.headroom_fraction,
            has_rand_basis=row.product != assumptions.IB,
            entity_id=row.entity_id,
            product=row.product,
        )
        assert status == row.opportunity_status, (row.entity_id, row.product)
        assert reason == row.status_reason
    del engine_table


def test_every_status_carries_an_action_and_a_reason(synthetic: engine.Intelligence) -> None:
    detail = synthetic.opportunity_detail
    assert detail["opportunity_status"].isin(config.STATUS_ORDER).all()
    assert (detail["status_reason"].str.len() > 20).all()
    assert detail["status_action"].notna().all()


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------


def test_selection_score_reproduces_from_its_declared_factors(
    synthetic: engine.Intelligence,
) -> None:
    detail = synthetic.opportunity_detail
    expected = (
        pd.to_numeric(detail["commercial_opportunity_score"])
        * detail["selection_role_weight"]
        * detail["selection_confidence_weight"]
        * detail["selection_diagnostic_factor"]
        * detail["selection_sensitivity_factor"]
    ).clip(0.0, 1.0)
    pd.testing.assert_series_equal(
        detail["selection_score"], expected, check_names=False
    )


def test_a_low_confidence_giant_does_not_outrank_a_high_confidence_smaller_opportunity() -> None:
    """The brief's own example, asserted arithmetically on the weights."""
    low_confidence_fx = (
        0.75
        * config.ROLE_WEIGHT[assumptions.CORE]
        * config.CONFIDENCE_WEIGHT["LOW"]
    )
    high_confidence_lending = (
        0.60
        * config.ROLE_WEIGHT[assumptions.SUPPORTING]
        * config.CONFIDENCE_WEIGHT["HIGH"]
    )
    assert high_confidence_lending > low_confidence_fx


def test_the_primary_is_the_highest_selection_score_with_headroom(
    synthetic: engine.Intelligence,
) -> None:
    detail = synthetic.opportunity_detail
    for entity_id, group in detail.groupby("entity_id"):
        eligible = group[group["opportunity_status"] != config.NO_HEADROOM]
        if eligible.empty:
            continue
        primary = group[group["selection_slot"] == selection.PRIMARY].iloc[0]
        assert primary["selection_score"] == pytest.approx(
            eligible["selection_score"].max()
        ), entity_id


def test_a_no_headroom_pillar_is_never_selected(synthetic: engine.Intelligence) -> None:
    detail = synthetic.opportunity_detail
    selected = detail[detail["selection_slot"].notna()]
    assert (selected["opportunity_status"] != config.NO_HEADROOM).all()


def test_the_three_slots_are_distinct_products(synthetic: engine.Intelligence) -> None:
    detail = synthetic.opportunity_detail
    for entity_id, group in detail.groupby("entity_id"):
        slots = group[group["selection_slot"].notna()]
        assert slots["selection_slot"].is_unique, entity_id
        assert slots["product"].is_unique, entity_id


def test_selection_is_deterministic_under_row_reordering() -> None:
    features = synthetic_features(count=8)
    model = wallet_engine.run(features)
    forward = engine.run(model.opportunity_engine, model.client_profiles, None)
    shuffled = model.opportunity_engine.iloc[::-1].reset_index(drop=True)
    backward = engine.run(shuffled, model.client_profiles, None)
    key = ["entity_id", "product"]
    pd.testing.assert_series_equal(
        forward.opportunity_detail.set_index(key)["selection_slot"].sort_index(),
        backward.opportunity_detail.set_index(key)["selection_slot"].sort_index(),
    )


def test_two_runs_produce_identical_output(synthetic: engine.Intelligence) -> None:
    features = synthetic_features(count=8)
    model = wallet_engine.run(features)
    first = engine.run(model.opportunity_engine, model.client_profiles, None)
    second = engine.run(model.opportunity_engine, model.client_profiles, None)
    pd.testing.assert_frame_equal(first.client_intelligence, second.client_intelligence)
    pd.testing.assert_frame_equal(
        first.opportunity_explanations, second.opportunity_explanations
    )
    pd.testing.assert_frame_equal(first.banker_questions, second.banker_questions)


# ---------------------------------------------------------------------------
# Terminology
# ---------------------------------------------------------------------------


def test_no_forbidden_phrase_appears_anywhere(synthetic: engine.Intelligence) -> None:
    for text in _all_text(synthetic):
        lowered = text.lower()
        for phrase in config.FORBIDDEN_PHRASES:
            assert phrase not in lowered, f"{phrase!r} in: {text[:200]}"


def test_lending_text_never_uses_share_of_wallet_language(
    synthetic: engine.Intelligence,
) -> None:
    lending = synthetic.opportunity_explanations[
        synthetic.opportunity_explanations["product"] == assumptions.LENDING
    ]
    assert not lending.empty
    for row in lending.itertuples():
        body = f"{row.what} {row.why}".lower()
        assert "share of wallet" not in body or "no share of wallet is computed" in body
        assert "financing opportunity" in body


def test_investment_banking_text_never_claims_a_rand_amount_or_a_share(
    synthetic: engine.Intelligence,
) -> None:
    ib = synthetic.opportunity_explanations[
        synthetic.opportunity_explanations["product"] == assumptions.IB
    ]
    assert not ib.empty
    for row in ib.itertuples():
        body = f"{row.what} {row.why} {row.limitation}"
        assert "opportunity signal" in body.lower()
        assert "No rand amount is estimated" in body
        assert "share of wallet" not in body.lower()


def test_cash_text_uses_the_required_addressable_cash_flow_phrasing(
    synthetic: engine.Intelligence,
) -> None:
    cash = synthetic.opportunity_explanations[
        synthetic.opportunity_explanations["product"] == assumptions.CASH
    ]
    assert not cash.empty
    for row in cash.itertuples():
        assert "Addressable Cash Flow" in row.what or "Addressable Cash Flow" in row.why
        assert "observable addressable cash flow" in row.why
        assert "not bank income" in row.limitation


def test_fx_and_trade_text_label_the_denominator_as_a_peer_benchmark(
    synthetic: engine.Intelligence,
) -> None:
    for product, phrase in (
        (assumptions.FX, "peer-benchmark addressable FX activity"),
        (assumptions.TRADE, "peer-benchmark addressable trade-finance activity"),
    ):
        rows = synthetic.opportunity_explanations[
            synthetic.opportunity_explanations["product"] == product
        ]
        assert not rows.empty
        for row in rows.itertuples():
            assert phrase in f"{row.what} {row.why}"
            assert "not a disclosed market total" in row.limitation


def test_the_pillar_labels_come_from_the_model_not_from_this_layer() -> None:
    assert set(config.DENOMINATOR_LABEL) == set(assumptions.PRODUCTS)
    assert set(config.DENOMINATOR_CAVEAT) == set(assumptions.PRODUCTS)
    assert config.SHARE_OF_WALLET_PILLARS == frozenset(assumptions.WALLET_PILLARS)


# ---------------------------------------------------------------------------
# Explanations
# ---------------------------------------------------------------------------


def test_every_client_product_pair_gets_a_six_part_explanation(
    synthetic: engine.Intelligence,
) -> None:
    explanations = synthetic.opportunity_explanations
    assert len(explanations) == 8 * len(assumptions.PRODUCTS)
    assert not explanations.duplicated(subset=["entity_id", "product"]).any()
    for part in ("what", "why", "evidence", "confidence_statement", "limitation", "next_action"):
        assert explanations[part].notna().all()
        assert (explanations[part].str.len() > 40).all(), part


def test_the_narrative_contains_all_six_labelled_sections(
    synthetic: engine.Intelligence,
) -> None:
    for narrative in synthetic.opportunity_explanations["narrative"]:
        for label in ("WHAT:", "WHY:", "EVIDENCE:", "CONFIDENCE:", "LIMITATION:", "NEXT ACTION:"):
            assert label in narrative


def test_explanations_cite_fields_that_exist_in_the_contract(
    synthetic: engine.Intelligence,
) -> None:
    from src.syn_wallet.wallet import contract

    published = set(contract.OPPORTUNITY_ENGINE_COLUMNS)
    for row in synthetic.opportunity_explanations.itertuples():
        cited = {field.strip() for field in row.source_fields.split(",")}
        assert cited <= published, cited - published
        # ...and each cited field is actually named in the evidence text.
        for field in cited:
            assert f"`{field}`" in row.evidence, (row.entity_id, row.product, field)


def test_the_confidence_statement_matches_the_published_band(
    synthetic: engine.Intelligence,
) -> None:
    for row in synthetic.opportunity_explanations.itertuples():
        assert row.confidence_statement.startswith(row.confidence_band)


def test_a_low_confidence_explanation_always_carries_the_validation_caveat(
    synthetic: engine.Intelligence,
) -> None:
    low = synthetic.opportunity_explanations[
        synthetic.opportunity_explanations["confidence_band"] == "LOW"
    ]
    for row in low.itertuples():
        assert "validated" in row.limitation or "validate" in row.limitation


# ---------------------------------------------------------------------------
# Banker questions
# ---------------------------------------------------------------------------


def test_every_primary_opportunity_gets_two_to_four_questions(
    synthetic: engine.Intelligence,
) -> None:
    asked = synthetic.banker_questions
    primaries = synthetic.opportunity_detail[
        synthetic.opportunity_detail["selection_slot"] == selection.PRIMARY
    ]
    for row in primaries.itertuples():
        client_questions = asked[
            (asked["entity_id"] == row.entity_id)
            & (asked["selection_slot"] == selection.PRIMARY)
        ]
        assert 2 <= len(client_questions) <= 4, (row.entity_id, len(client_questions))


def test_questions_are_client_specific_not_generic(synthetic: engine.Intelligence) -> None:
    """At least one question per client must quote a figure from that client."""
    asked = synthetic.banker_questions
    for entity_id, group in asked.groupby("entity_id"):
        primary = group[group["selection_slot"] == selection.PRIMARY]
        assert any("R" in question for question in primary["question"]), entity_id


def test_every_question_names_the_fields_it_was_built_from(
    synthetic: engine.Intelligence,
) -> None:
    from src.syn_wallet.wallet import contract

    published = set(contract.OPPORTUNITY_ENGINE_COLUMNS)
    for row in synthetic.banker_questions.itertuples():
        cited = {field.strip() for field in row.source_fields.split(",")}
        assert cited <= published, cited - published
        assert row.rationale
        assert row.question.endswith("?")


def test_questions_only_cover_the_primary_and_secondary_slots(
    synthetic: engine.Intelligence,
) -> None:
    slots = set(synthetic.banker_questions["selection_slot"])
    assert slots <= {selection.PRIMARY, selection.SECONDARY}


# ---------------------------------------------------------------------------
# No cross-pillar totals
# ---------------------------------------------------------------------------


def test_no_profile_column_totals_the_pillars(synthetic: engine.Intelligence) -> None:
    from src.syn_wallet.intelligence import profiles

    profiles.assert_no_cross_pillar_total(synthetic.client_intelligence)


def test_a_deliberate_cross_pillar_total_is_caught(synthetic: engine.Intelligence) -> None:
    from src.syn_wallet.intelligence import profiles

    sabotaged = synthetic.client_intelligence.copy()
    sabotaged["total_opportunity_zar"] = sabotaged[
        list(profiles.PILLAR_VALUE_COLUMNS)
    ].sum(axis=1, min_count=1)
    with pytest.raises(AssertionError, match="never be added"):
        profiles.assert_no_cross_pillar_total(sabotaged)


def test_the_portfolio_table_reports_rand_per_pillar_never_combined(
    synthetic: engine.Intelligence,
) -> None:
    rand_rows = synthetic.portfolio_intelligence[
        synthetic.portfolio_intelligence["metric"].str.endswith("_zar")
    ]
    # Every rand figure is attributed to exactly one pillar. A row with no
    # product would be a cross-pillar aggregate.
    assert rand_rows["product"].notna().all()


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------


def test_sensitivity_ranges_match_the_sweep(synthetic: engine.Intelligence) -> None:
    sweep_result = sweep.run(_features(), list(REDUCED_GRID))
    view = synthetic.sensitivity.set_index(["entity_id", "product"])
    grouped = sweep_result.detail.groupby(["entity_id", "product"])
    for key, group in grouped:
        row = view.loc[key]
        expected_low = pd.to_numeric(group["estimate_zar"], errors="coerce").min()
        expected_high = pd.to_numeric(group["estimate_zar"], errors="coerce").max()
        assert row["estimate_low"] == pytest.approx(expected_low, nan_ok=True)
        assert row["estimate_high"] == pytest.approx(expected_high, nan_ok=True)


def test_the_base_estimate_matches_the_contract(synthetic: engine.Intelligence) -> None:
    detail = synthetic.opportunity_detail.set_index(["entity_id", "product"])
    view = synthetic.sensitivity.set_index(["entity_id", "product"])
    for key, row in view.iterrows():
        if pd.isna(row["estimate_base"]):
            continue
        assert row["estimate_base"] == pytest.approx(
            detail.at[key, "addressable_zar"], nan_ok=True
        )


def test_an_identity_anchored_pillar_is_reported_as_stable(
    synthetic: engine.Intelligence,
) -> None:
    cash = synthetic.sensitivity[synthetic.sensitivity["product"] == assumptions.CASH]
    assert (cash["sensitivity_flag"] == config.STABLE).all()
    assert (cash["estimate_range_pct"].fillna(0) == 0).all()


def test_a_pillar_with_no_rand_estimate_is_not_called_stable(
    synthetic: engine.Intelligence,
) -> None:
    ib = synthetic.sensitivity[synthetic.sensitivity["product"] == assumptions.IB]
    assert (ib["sensitivity_flag"] == config.NOT_APPLICABLE).all()


def test_a_run_without_the_sweep_says_not_tested_rather_than_stable() -> None:
    features = synthetic_features(count=8)
    model = wallet_engine.run(features)
    result = engine.run(model.opportunity_engine, model.client_profiles, None)
    assert (result.sensitivity["sensitivity_flag"] == config.NOT_APPLICABLE).all()
    assert result.sensitivity["estimate_base"].isna().all()
    assert result.report["scenarios_tested"] == 0


def test_sensitive_estimates_get_the_benchmark_sensitive_wording(
    synthetic: engine.Intelligence,
) -> None:
    sensitive = synthetic.sensitivity[
        synthetic.sensitivity["sensitivity_flag"] == config.SENSITIVE
    ]
    if sensitive.empty:
        pytest.skip("no sensitive estimate in this synthetic portfolio")
    explanations = synthetic.opportunity_explanations.set_index(["entity_id", "product"])
    for key in sensitive.set_index(["entity_id", "product"]).index:
        if key[1] not in (assumptions.FX, assumptions.TRADE):
            continue
        assert "benchmark-sensitive" in explanations.at[key, "limitation"]


def test_sensitivity_classification_boundaries() -> None:
    assert sensitivity_view._classify_range(0.0) == config.STABLE
    assert sensitivity_view._classify_range(config.SENSITIVITY_STABLE_RANGE) == config.STABLE
    assert (
        sensitivity_view._classify_range(config.SENSITIVITY_MODERATE_RANGE)
        == config.MODERATE
    )
    assert sensitivity_view._classify_range(0.9) == config.SENSITIVE
    assert sensitivity_view._classify_range(None) == config.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Contract fidelity
# ---------------------------------------------------------------------------


def test_the_layer_refuses_a_contract_from_another_methodology() -> None:
    features = synthetic_features(count=8)
    model = wallet_engine.run(features)
    tampered = model.opportunity_engine.copy()
    tampered["methodology_version"] = "wallet-0.0.1"
    with pytest.raises(ValueError, match="methodology"):
        engine.run(tampered, model.client_profiles, None)


def test_the_layer_refuses_a_contract_with_missing_rows() -> None:
    features = synthetic_features(count=8)
    model = wallet_engine.run(features)
    truncated = model.opportunity_engine.iloc[:-1]
    with pytest.raises(ValueError, match="rows"):
        engine.run(truncated, model.client_profiles, None)


def test_every_number_in_the_profile_comes_from_the_contract(
    synthetic: engine.Intelligence,
) -> None:
    """Spot-check every pillar figure against the row it was read from."""
    contract_table = wallet_engine.run(_features()).opportunity_engine.set_index(
        ["entity_id", "product"]
    )
    profiles = synthetic.client_intelligence.set_index("entity_id")
    for entity_id, row in profiles.iterrows():
        for product, prefix, column in (
            (assumptions.CASH, "addressable_cash_flow_zar", "addressable_zar"),
            (assumptions.CASH, "cash_observed_zar", "observed_zar"),
            (assumptions.CASH, "cash_share", "share"),
            (assumptions.FX, "fx_addressable_zar", "addressable_zar"),
            (assumptions.FX, "fx_share", "share"),
            (assumptions.TRADE, "trade_addressable_zar", "addressable_zar"),
            (assumptions.TRADE, "trade_share", "share"),
            (assumptions.LENDING, "lending_opportunity_zar", "opportunity_zar"),
            (assumptions.IB, "ib_signal_score", "signal_score"),
        ):
            expected = contract_table.at[(entity_id, product), column]
            assert row[prefix] == pytest.approx(expected, nan_ok=True), (
                entity_id,
                prefix,
            )


def test_lending_carries_no_share_anywhere(synthetic: engine.Intelligence) -> None:
    profiles = synthetic.client_intelligence
    assert not any(column.startswith("lending_share") for column in profiles.columns)
    detail = synthetic.opportunity_detail
    assert detail.loc[detail["product"] == assumptions.LENDING, "share"].isna().all()


def test_investment_banking_carries_no_rand_anywhere(
    synthetic: engine.Intelligence,
) -> None:
    profiles = synthetic.client_intelligence
    assert not any(
        column.startswith("ib_") and column.endswith("_zar") for column in profiles.columns
    )
    detail = synthetic.opportunity_detail
    ib = detail[detail["product"] == assumptions.IB]
    assert ib["addressable_zar"].isna().all()
    assert ib["opportunity_zar"].isna().all()
    assert ib["share"].isna().all()


# ---------------------------------------------------------------------------
# The real portfolio, end to end
# ---------------------------------------------------------------------------


@requires_full_data
def test_all_twenty_clients_are_covered(intelligence) -> None:
    rows, clients = intelligence.execute(
        "SELECT COUNT(*), COUNT(DISTINCT entity_id) FROM client_opportunity_intelligence"
    ).fetchone()
    assert rows == clients == EXPECTED_CLIENTS


@requires_full_data
def test_every_client_has_explanations_for_all_five_pillars(intelligence) -> None:
    bad = intelligence.execute(
        "SELECT COUNT(*) FROM (SELECT entity_id, COUNT(*) n FROM opportunity_explanations "
        "GROUP BY 1) WHERE n <> 5"
    ).fetchone()[0]
    assert bad == 0


@requires_full_data
def test_profile_figures_match_the_contract_on_the_real_portfolio(intelligence) -> None:
    mismatches = intelligence.execute(
        """
        SELECT COUNT(*) FROM client_opportunity_intelligence i
        JOIN opportunity_engine c
          ON c.entity_id = i.entity_id AND c.product = 'cash_management'
        WHERE ABS(i.addressable_cash_flow_zar - c.addressable_zar) > 1.0
           OR ABS(i.cash_observed_zar - c.observed_zar) > 1.0
           OR ABS(i.cash_share - c.share) > 1e-12
        """
    ).fetchone()[0]
    assert mismatches == 0


@requires_full_data
def test_no_low_confidence_priority_on_the_real_portfolio(intelligence) -> None:
    offenders = intelligence.execute(
        "SELECT COUNT(*) FROM opportunity_selection_detail "
        "WHERE opportunity_status = 'PRIORITY' AND confidence_band <> 'HIGH'"
    ).fetchone()[0]
    assert offenders == 0


@requires_full_data
def test_sensitivity_ranges_match_the_sweep_on_the_real_portfolio(intelligence) -> None:
    mismatches = intelligence.execute(
        """
        WITH span AS (
            SELECT entity_id, product,
                   MIN(estimate_zar) AS low, MAX(estimate_zar) AS high
            FROM model_sensitivity GROUP BY 1, 2
        )
        SELECT COUNT(*) FROM opportunity_sensitivity_summary s
        JOIN span USING (entity_id, product)
        WHERE ABS(s.estimate_low - span.low) > 1.0 OR ABS(s.estimate_high - span.high) > 1.0
        """
    ).fetchone()[0]
    assert mismatches == 0


@requires_full_data
def test_no_forbidden_phrase_on_the_real_portfolio(intelligence) -> None:
    columns = (
        ("opportunity_explanations", "narrative"),
        ("banker_questions", "question"),
        ("banker_questions", "rationale"),
        ("client_opportunity_intelligence", "opportunity_summary"),
        ("portfolio_opportunity_intelligence", "note"),
    )
    for table, column in columns:
        for phrase in config.FORBIDDEN_PHRASES:
            hits = intelligence.execute(
                f"SELECT COUNT(*) FROM {table} WHERE lower({column}) LIKE '%{phrase}%'"
            ).fetchone()[0]
            assert hits == 0, f"{phrase!r} found in {table}.{column}"


@requires_full_data
def test_the_json_outputs_are_valid_and_complete(intelligence_run) -> None:
    import json
    from pathlib import Path

    for name, path in intelligence_run["outputs_json"].items():
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        assert isinstance(records, list) and records, name
        assert all(isinstance(record, dict) for record in records)


@requires_full_data
def test_every_client_has_a_primary_opportunity_or_a_stated_reason(intelligence) -> None:
    bad = intelligence.execute(
        "SELECT COUNT(*) FROM client_opportunity_intelligence "
        "WHERE (has_primary_opportunity AND primary_product IS NULL) "
        "   OR (NOT has_primary_opportunity AND no_opportunity_reason = '')"
    ).fetchone()[0]
    assert bad == 0


@requires_full_data
def test_the_portfolio_table_covers_every_required_section(intelligence) -> None:
    sections = {
        row[0]
        for row in intelligence.execute(
            "SELECT DISTINCT section FROM portfolio_opportunity_intelligence"
        ).fetchall()
    }
    assert {
        "portfolio_position",
        "product_metrics",
        "top_by_product",
        "top_by_client",
        "penetration_distribution",
        "confidence_distribution",
        "largest_gaps",
        "highest_cash_penetration",
        "low_confidence_high_value",
        "multiple_opportunities",
        "sector_concentration",
        "primary_concentration",
    } <= sections
