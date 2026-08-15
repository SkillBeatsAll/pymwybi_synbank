"""The dashboard's service and HTTP layer.

The tests that matter here are the ones that stop the dashboard disagreeing with
the model. Two kinds:

* **Fidelity.** Every figure a page shows must equal the column it came from.
  The service is not allowed to compute anything, so any divergence is a bug in
  the projection.
* **The display rule.** No payload may contain a total across the five pillars.
  That is checked on the real payloads, and the guard itself is checked by
  feeding it a deliberate total.
"""

from __future__ import annotations

import pytest

from src.syn_wallet.api import service
from src.syn_wallet.wallet import assumptions

from .conftest import requires_full_data

pytestmark = requires_full_data

EXPECTED_CLIENTS = 20


@pytest.fixture(scope="module")
def store() -> service.Tables:
    return service.load()


@pytest.fixture(scope="module")
def client(store):
    from fastapi.testclient import TestClient

    from src.syn_wallet.api.app import create_app

    return TestClient(create_app())


# ---------------------------------------------------------------------------
# The display rule
# ---------------------------------------------------------------------------


def test_no_page_payload_totals_the_pillars(store) -> None:
    service.portfolio_payload(store)  # asserts internally
    for entity_id in store["client_opportunity_intelligence"]["entity_id"]:
        service.client_payload(entity_id, store)


def test_the_no_total_guard_actually_fires() -> None:
    payload = {
        "pillars": [
            {"key": "cash", "opportunity": {"value": 100.0}},
            {"key": "fx", "opportunity": {"value": 50.0}},
        ],
        "headline": {"total": 150.0},
    }
    with pytest.raises(AssertionError, match="never be added"):
        service._assert_no_cross_pillar_total(payload)


def test_the_guard_ignores_a_single_pillar() -> None:
    payload = {"pillars": [{"key": "cash", "opportunity": {"value": 100.0}}], "x": 100.0}
    service._assert_no_cross_pillar_total(payload)


# ---------------------------------------------------------------------------
# Fidelity to the model
# ---------------------------------------------------------------------------


def test_portfolio_figures_match_the_portfolio_summary(store) -> None:
    payload = service.portfolio_payload(store)
    summary = store["portfolio_summary"].set_index("product")
    for pillar in payload["pillars"]:
        row = summary.loc[pillar["product"]]
        assert pillar["observed"]["value"] == pytest.approx(
            service.clean(row["total_observed_zar"]), nan_ok=True
        )
        assert pillar["addressable"]["value"] == pytest.approx(
            service.clean(row["total_estimate_zar"]), nan_ok=True
        )
        assert pillar["opportunity"]["value"] == pytest.approx(
            service.clean(row["total_gap_zar"]), nan_ok=True
        )


def test_client_pillar_figures_match_the_selection_detail(store) -> None:
    detail = store["opportunity_selection_detail"].set_index(["entity_id", "product"])
    for entity_id in ("E02", "E09", "E16", "E17", "E20"):
        payload = service.client_payload(entity_id, store)
        for pillar in payload["pillars"]:
            row = detail.loc[(entity_id, pillar["product"])]
            for key, column in (
                ("observed", "observed_zar"),
                ("addressable", "addressable_zar"),
                ("opportunity", "opportunity_zar"),
                ("share", "share"),
            ):
                assert pillar[key]["value"] == pytest.approx(
                    service.clean(row[column]), nan_ok=True
                ), (entity_id, pillar["product"], key)
            assert pillar["confidence_band"] == row["confidence_band"]


def test_heatmap_covers_every_client_and_pillar(store) -> None:
    payload = service.heatmap_payload(store)
    assert len(payload["clients"]) == EXPECTED_CLIENTS
    assert len(payload["products"]) == len(assumptions.PRODUCTS)
    assert len(payload["cells"]) == EXPECTED_CLIENTS * len(assumptions.PRODUCTS)
    keys = {(cell["entity_id"], cell["product"]) for cell in payload["cells"]}
    assert len(keys) == len(payload["cells"])


def test_sensitivity_ranges_match_the_sweep(store) -> None:
    payload = service.sensitivity_payload(store)
    spread = store["opportunity_sensitivity_summary"].set_index(["entity_id", "product"])
    for row in payload["widest"]:
        source = spread.loc[(row["entity_id"], row["product"])]
        assert row["low"]["value"] == pytest.approx(service.clean(source["estimate_low"]))
        assert row["high"]["value"] == pytest.approx(service.clean(source["estimate_high"]))


# ---------------------------------------------------------------------------
# Pillar semantics
# ---------------------------------------------------------------------------


def test_lending_and_ib_never_carry_a_share(store) -> None:
    for entity_id in store["client_opportunity_intelligence"]["entity_id"]:
        payload = service.client_payload(entity_id, store)
        for pillar in payload["pillars"]:
            if pillar["product"] in (assumptions.LENDING, assumptions.IB):
                assert pillar["share"]["value"] is None, (entity_id, pillar["product"])
                assert pillar["role"] in ("supporting", "signal")


def test_investment_banking_never_carries_a_rand_figure(store) -> None:
    for entity_id in store["client_opportunity_intelligence"]["entity_id"]:
        payload = service.client_payload(entity_id, store)
        ib = next(p for p in payload["pillars"] if p["product"] == assumptions.IB)
        assert ib["observed"]["value"] is None
        assert ib["addressable"]["value"] is None
        assert ib["opportunity"]["value"] is None


def test_the_pillar_roles_match_the_model_classification(store) -> None:
    classes = store["product_classification"].set_index("product")["product_class"]
    expected = {"CORE": "core", "SUPPORTING": "supporting", "SIGNAL_ONLY": "signal"}
    for product, meta in service.PILLAR_META.items():
        assert meta["role"] == expected[classes[product]], product


def test_cash_is_labelled_addressable_cash_flow_and_never_a_wallet() -> None:
    meta = service.PILLAR_META[assumptions.CASH]
    assert meta["denominator"] == "Addressable Cash Flow"
    for banned in ("wallet", "revenue", "fee"):
        assert banned not in meta["denominator"].lower()


# ---------------------------------------------------------------------------
# JSON safety
# ---------------------------------------------------------------------------


def test_nan_becomes_null_not_the_token_nan(store) -> None:
    import json
    import math

    for payload in (
        service.portfolio_payload(store),
        service.heatmap_payload(store),
        service.client_payload("E16", store),
        service.sensitivity_payload(store),
        service.product_payload(assumptions.IB, store),
    ):
        text = json.dumps(payload)
        assert "NaN" not in text
        assert "Infinity" not in text

    assert service.clean(float("nan")) is None
    assert service.clean(None) is None
    assert service.clean(math.nan) is None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

ENDPOINTS = (
    "/api/health",
    "/api/portfolio",
    "/api/heatmap",
    "/api/clients",
    "/api/clients/E09",
    "/api/sensitivity",
    "/api/products",
    "/api/products/fx",
    "/api/products/cash_management",
    "/api/copilot/examples",
)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_answers(client, path) -> None:
    response = client.get(path)
    assert response.status_code == 200, path
    assert response.json() is not None


def test_a_client_id_is_case_insensitive(client) -> None:
    assert client.get("/api/clients/e09").status_code == 200


def test_an_unknown_client_is_a_404_with_a_useful_message(client) -> None:
    response = client.get("/api/clients/E99")
    assert response.status_code == 404
    assert "E99" in response.json()["detail"]


def test_an_unknown_product_is_a_404_that_lists_the_valid_ones(client) -> None:
    response = client.get("/api/products/nonsense")
    assert response.status_code == 404
    assert assumptions.CASH in response.json()["detail"]


def test_the_shell_is_served_for_a_client_side_route(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Coverage Desk" in response.text


def test_health_reports_the_model_versions(client) -> None:
    body = client.get("/api/health").json()
    assert body["clients"] == EXPECTED_CLIENTS
    assert body["methodology_version"] == assumptions.METHODOLOGY_VERSION
    assert "available" in body["ai"]


def test_health_never_leaks_a_key(client) -> None:
    text = client.get("/api/health").text
    assert "api_key" not in text
    assert "sk-" not in text
    assert "nvapi" not in text


def test_the_copilot_endpoint_answers_without_calling_the_model(client) -> None:
    response = client.post(
        "/api/copilot/ask",
        json={"question": "Which clients have the largest opportunities?", "allow_llm": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert body["used_llm"] is False
    assert body["mode"].startswith("fallback") or body["mode"] == "demo_stored_response"


def test_the_copilot_endpoint_rejects_an_empty_question(client) -> None:
    assert client.post("/api/copilot/ask", json={"question": ""}).status_code == 422


def test_client_examples_are_tailored_to_the_client(client) -> None:
    generic = client.get("/api/copilot/examples").json()["questions"]
    tailored = client.get("/api/copilot/examples?client=E09").json()["questions"]
    assert generic != tailored
    assert any("Shoprite" in question for question in tailored)


# ---------------------------------------------------------------------------
# Product pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("product", assumptions.PRODUCTS)
def test_every_product_page_builds(store, product) -> None:
    payload = service.product_payload(product, store)
    assert len(payload["clients"]) == EXPECTED_CLIENTS
    assert payload["label"]
    assert payload["basis"]


def test_product_descriptive_panels_are_populated(store) -> None:
    expected = {
        assumptions.CASH: "legs",
        assumptions.FX: "currency_pairs",
        assumptions.TRADE: "instruments",
        assumptions.LENDING: "components",
        assumptions.IB: "categories",
    }
    for product, key in expected.items():
        payload = service.product_payload(product, store)
        assert payload["descriptive"].get(key), (product, key)


# ---------------------------------------------------------------------------
# The browser must never render a currency figure itself
# ---------------------------------------------------------------------------


def test_the_front_end_contains_no_currency_formatter() -> None:
    """A second rand formatter is a second rounding rule.

    `dashboard/assets/app.js` had one that wrote R279bn where the server writes
    R278.56bn, and it was reached from a heatmap tooltip and the client table --
    not only from axis ticks, as its comment claimed. The rule is now checkable:
    no rand symbol may be constructed in the browser at all.
    """
    from src.syn_wallet import config as paths

    source = (paths.REPOSITORY_ROOT / "dashboard" / "assets" / "app.js").read_text(
        encoding="utf-8"
    )
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "`R${" in line or '"R" +' in line or "'R' +" in line
    ]
    assert not offenders, f"currency built in the browser: {offenders}"


def test_every_rand_column_the_front_end_reads_ships_with_its_display_string(store) -> None:
    """Raw rand without a rendered form is an invitation to format it locally."""
    for cell in service.heatmap_payload(store)["cells"]:
        for column in ("opportunity_zar", "observed_zar", "addressable_zar"):
            assert f"{column}_display" in cell, column
            if cell[column] is None:
                assert cell[f"{column}_display"] is None
            else:
                assert cell[f"{column}_display"].startswith(("R", "-R"))

    for row in service.client_index(store):
        assert "primary_opportunity_zar_display" in row
        if row["primary_opportunity_zar"] is not None:
            assert row["primary_opportunity_zar_display"].startswith(("R", "-R"))
