"""Walkthrough of the wallet and opportunity engine, and its visualisation.

Runs as a plain script or, because it is split into ``# %%`` cells, as an
interactive notebook in VS Code or Jupyter::

    .venv/bin/python -m analysis.wallet_model_walkthrough

It reads ``data/processed/*.parquet`` if the wallet engine has already been
built (``python -m src.syn_wallet.build_wallet --overwrite``), and builds it
into a temporary directory otherwise, so it never depends on state a fresh
clone would not have.

Full methodology, every formula, every declared assumption and three worked
examples live in ``MODEL_REPORT.md`` (generated from these same tables, not
hand-written) -- this notebook is a tour of the *outputs*, not a restatement
of the method.
"""

# %%
from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pandas as pd

try:
    get_ipython()  # type: ignore[name-defined]  # noqa: F821 - defined by IPython/Jupyter kernels
    _IN_NOTEBOOK = True
except NameError:
    _IN_NOTEBOOK = False

import matplotlib

if not _IN_NOTEBOOK:
    matplotlib.use("Agg")  # headless script mode - no display to render to
import matplotlib.pyplot as plt

from src.syn_wallet import config

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)
pd.set_option("display.float_format", lambda value: f"{value:,.4f}")

# Same categorical order used throughout the dashboard (dataviz skill reference palette).
PILLAR_COLOR = {
    "cash_management": "#2a78d6",
    "fx_global_markets": "#1baf7a",
    "trade_finance": "#eb6834",
    "lending": "#eda100",
    "investment_banking": "#e87ba4",
}
PILLAR_ORDER = list(PILLAR_COLOR)


def load() -> duckdb.DuckDBPyConnection:
    """Return a connection with the wallet engine outputs registered as views."""
    from src.syn_wallet import build_wallet

    names = build_wallet.WALLET_OUTPUTS
    paths = {name: config.PROCESSED_DIR / f"{name}.parquet" for name in names}
    if not all(path.is_file() for path in paths.values()):
        print("Wallet outputs not found; building into a temporary directory...")
        output_dir = Path(tempfile.mkdtemp(prefix="syn_wallet_wallet_"))
        build_wallet.run(output_dir=output_dir, overwrite=True)
        paths = {name: output_dir / f"{name}.parquet" for name in names}
    connection = duckdb.connect(":memory:")
    for name, path in paths.items():
        connection.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    return connection


def show(title: str, frame: pd.DataFrame) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    print(frame.to_string(index=False))


con = load()

# %% [markdown]
# ## 1. Five pillars, two kinds of estimate
#
# Every client x product row separates **observed** activity (visible in Syn
# Bank's own data) from an **estimate** (addressable wallet, financing
# opportunity, or a ranked signal, depending on the pillar) and a **confidence**
# score. The five pillars are never summed into one number -- they have
# different estimate bases and different units. Full formulas and every
# declared coefficient are in `MODEL_REPORT.md`.

# %%
show(
    "Portfolio summary by pillar",
    con.execute(
        """
        SELECT product, product_label, estimate_basis,
               ROUND(total_observed_zar / 1e9, 2) AS observed_bn,
               ROUND(total_estimate_zar / 1e9, 2) AS estimate_bn,
               ROUND(portfolio_share * 100, 3) AS portfolio_share_pct,
               ROUND(mean_confidence, 2) AS mean_confidence,
               clients_flagged
        FROM portfolio_summary
        """
    ).df(),
)

# %% [markdown]
# ## 2. The ranked opportunity table
#
# `opportunities.parquet` is the banker-facing view: every client x product
# estimate, ranked best-first by `opportunity_score` (0.45 x gap percentile +
# 0.30 x confidence + 0.25 x headroom, normalised **within** each pillar so a
# trillion-rand revenue base cannot dominate on scale alone).

# %%
show(
    "Top 10 opportunities, portfolio-wide",
    con.execute(
        """
        SELECT rank_overall, entity_name, sector, product_label,
               ROUND(estimate_zar / 1e9, 2) AS estimate_bn,
               ROUND(observed_zar / 1e9, 2) AS observed_bn,
               confidence_band, ROUND(opportunity_score, 3) AS opportunity_score
        FROM opportunities
        ORDER BY rank_overall
        LIMIT 10
        """
    ).df(),
)

# %% [markdown]
# ## 3. One client, in full
#
# Every number below is read back from the engine's own output -- nothing here
# is typed by hand. `explanation` is the engine's own generated narrative for
# this client and product, built from the real component values.

# %%
example = con.execute(
    """
    SELECT product_label, estimate_basis, ROUND(estimate_zar / 1e6, 1) AS estimate_zar_m,
           ROUND(observed_zar / 1e6, 1) AS observed_zar_m, share, confidence_band, explanation
    FROM opportunities
    WHERE entity_id = 'E09'
    ORDER BY product
    """
).df()
show("Shoprite Holdings (E09) -- every pillar", example)

# %% [markdown]
# ## 4. Visualisation
#
# Two views the dashboard (`dashboard/app.py`) also renders live: portfolio
# scale by pillar, and an opportunity heatmap across every client. Both read
# the same `opportunities` table shown above -- nothing is recomputed here.

# %%
portfolio = con.execute(
    "SELECT product, total_estimate_zar FROM portfolio_summary WHERE product != 'investment_banking'"
).df().set_index("product")

fig, ax = plt.subplots(figsize=(8, 4.5))
values = [portfolio.loc[p, "total_estimate_zar"] / 1e9 for p in PILLAR_ORDER if p in portfolio.index]
labels = [p.replace("_", " ").title() for p in PILLAR_ORDER if p in portfolio.index]
colors = [PILLAR_COLOR[p] for p in PILLAR_ORDER if p in portfolio.index]
bars = ax.bar(labels, values, color=colors, width=0.6)
ax.bar_label(bars, fmt="R%.0fbn", padding=3)
ax.set_ylabel("Portfolio total estimate (ZAR, billions)")
ax.set_title("Addressable / opportunity value by pillar\n(Investment Banking excluded -- signal only, no rand amount)")
ax.spines[["top", "right"]].set_visible(False)
plt.tight_layout()
if _IN_NOTEBOOK:
    plt.show()

# %%
heatmap_data = con.execute(
    "SELECT entity_name, product, opportunity_score FROM opportunities"
).df().pivot(index="entity_name", columns="product", values="opportunity_score")
heatmap_data = heatmap_data[PILLAR_ORDER]
heatmap_data = heatmap_data.loc[heatmap_data.mean(axis=1).sort_values(ascending=False).index]

fig, ax = plt.subplots(figsize=(7, 9))
im = ax.imshow(heatmap_data.values, cmap="Blues", aspect="auto", vmin=0, vmax=1)
ax.set_xticks(range(len(heatmap_data.columns)))
ax.set_xticklabels([c.replace("_", " ").title() for c in heatmap_data.columns], rotation=30, ha="right")
ax.set_yticks(range(len(heatmap_data.index)))
ax.set_yticklabels(heatmap_data.index, fontsize=8)
ax.set_title("Opportunity score by client x pillar")
fig.colorbar(im, ax=ax, label="Opportunity score", shrink=0.6)
plt.tight_layout()
if _IN_NOTEBOOK:
    plt.show()

# %% [markdown]
# ## 5. Where this goes next
#
# - The live, interactive version of both charts above -- plus a client
#   drill-down and a grounded AI briefing-note generator -- is
#   `dashboard/app.py` (`streamlit run dashboard/app.py`).
# - Competitor-lender evidence extraction and the briefing-note prompt are in
#   `prompts/`, with the generating code in `src/syn_wallet/`.
# - Every diagnostic and modelling caveat referenced above (`diagnostic_flags`,
#   `explanation`) is published in full in `model_diagnostics.parquet` and
#   `MODEL_REPORT.md`.
