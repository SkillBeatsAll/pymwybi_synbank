"""Syn Bank Wallet Twin — Streamlit dashboard.

Four tabs: portfolio summary, client drill-down, opportunity heatmap, and a grounded
AI briefing-note generator (Layer 5 — see CLAUDE.md section 6 and
``prompts/briefing_note_prompt.md`` for the groundedness design).

Reads the real wallet engine output, ``data/processed/opportunities.parquet`` (built by
the wallet model on the ``feat/data-anal`` branch — see
``src/syn_wallet/wallet/opportunity.py`` for the exact schema this dashboard consumes), the
moment that file exists in this working tree. Until then it falls back automatically to a
schema-matched DUMMY placeholder (``src/syn_wallet/generate_dummy_wallet_results.py``) so
the UI can be built and demoed without blocking on the model. No code change is needed when
the real file lands — only REAL_OPPORTUNITIES_PATH below needs to ever change, if the real
output ever moves.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.syn_wallet.generate_briefing_note import build_grounding_context, generate_briefing_note  # noqa: E402

REAL_OPPORTUNITIES_PATH = REPO_ROOT / "data" / "processed" / "opportunities.parquet"
DUMMY_OPPORTUNITIES_PATH = REPO_ROOT / "data" / "processed" / "opportunities_DUMMY.csv"
WALLET_RESULTS_PATH = REAL_OPPORTUNITIES_PATH if REAL_OPPORTUNITIES_PATH.exists() else DUMMY_OPPORTUNITIES_PATH
IS_DUMMY = WALLET_RESULTS_PATH == DUMMY_OPPORTUNITIES_PATH

ENTITY_LOOKUP_SOURCE = REPO_ROOT / "data" / "trade_finance.csv"
COMPETITOR_EVIDENCE_PATH = REPO_ROOT / "data" / "processed" / "competitor_evidence.csv"

CASH, FX, TRADE, LENDING, IB = "cash_management", "fx_global_markets", "trade_finance", "lending", "investment_banking"
PRODUCT_ORDER = (CASH, FX, TRADE, LENDING, IB)
PRODUCT_LABELS = {
    CASH: "Transactional / Cash Management",
    FX: "FX / Global Markets",
    TRADE: "Trade Finance",
    LENDING: "Lending",
    IB: "Investment Banking / Capital Markets",
}
# Short form of assumptions.ESTIMATE_BASIS_NOTES — why each pillar's numbers aren't comparable.
BASIS_CAPTION = {
    CASH: "Whole disclosed flow across all banks — share is the fraction Syn Bank handles.",
    FX: "Peer-benchmark: own driver x a well-penetrated peer's intensity, not a disclosed total.",
    TRADE: "Peer-benchmark: own driver x a well-penetrated peer's intensity, not a disclosed total.",
    LENDING: "A financing-need indicator from disclosed debt structure — not a claimed wallet.",
    IB: "A ranked mandate-likelihood signal only — no rand amount is estimated.",
}
RAND_PRODUCTS = (CASH, FX, TRADE, LENDING)  # IB has no estimate_zar at all

# dataviz skill reference palette — categorical slots 1-5 (blue, orange, aqua, yellow, magenta)
PILLAR_COLOR = {CASH: "#2a78d6", FX: "#1baf7a", TRADE: "#eb6834", LENDING: "#eda100", IB: "#e87ba4"}
SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#184f95", "#0d366b"]
SURFACE = "#fcfcfb"
INK = "#0b0b0f"
MUTED_INK = "#898781"
GRIDLINE = "#e1e0d9"


@st.cache_data
def load_wallet_results(path: Path) -> pd.DataFrame:
    relation = f"read_parquet('{path}')" if path.suffix == ".parquet" else f"read_csv_auto('{path}')"
    return duckdb.sql(f"SELECT * FROM {relation}").df()


@st.cache_data
def load_entity_lookup(path: Path) -> pd.DataFrame:
    return duckdb.sql(
        f"SELECT DISTINCT entity_id, entity_name, sector "
        f"FROM read_csv_auto('{path}') ORDER BY entity_name"
    ).df()


@st.cache_data
def load_competitor_evidence(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_csv(path)


def format_zar(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    for threshold, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= threshold:
            return f"R{value / threshold:,.1f}{suffix}"
    return f"R{value:,.0f}"


def format_pct(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value * 100:.1f}%"


def chart_layout(fig: go.Figure, **kwargs) -> go.Figure:
    fig.update_layout(
        plot_bgcolor=SURFACE,
        paper_bgcolor=SURFACE,
        font_color=INK,
        font_family="system-ui, -apple-system, 'Segoe UI', sans-serif",
        margin=dict(l=10, r=10, t=40, b=10),
        transition=dict(duration=450, easing="cubic-in-out"),
        hoverlabel=dict(bgcolor=INK, font_color="#ffffff", bordercolor=INK, font_size=13),
        **kwargs,
    )
    fig.update_xaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE, tickfont_color=MUTED_INK)
    fig.update_yaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE, tickfont_color=MUTED_INK)
    return fig


def confidence_bar(value: float, color: str) -> str:
    pct = max(0.0, min(1.0, float(value))) * 100
    return (
        '<div class="conf-track">'
        f'<div class="conf-fill" style="width:{pct:.0f}%; background:{color};"></div>'
        "</div>"
        f'<div class="conf-caption">Confidence {pct:.0f}%</div>'
    )


def inject_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --surface: #fcfcfb;
            --ink: #0b0b0f;
            --ink-secondary: #52545c;
            --ink-muted: #898781;
            --border: rgba(11,11,15,0.08);
            --shadow: 0 4px 18px rgba(20,25,40,0.07);
            --shadow-hover: 0 12px 28px rgba(20,25,40,0.14);
            --blue: #2a78d6;
            --orange: #eb6834;
            --aqua: #1baf7a;
            --yellow: #eda100;
            --magenta: #e87ba4;
        }

        .stApp {
            background: linear-gradient(165deg, #f7f9fc 0%, #eef1f7 45%, #eaf1fb 100%);
            color: var(--ink) !important;
        }
        .stApp p, .stApp span, .stApp li, .stApp label,
        [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p,
        [data-testid="stMarkdownContainer"] p {
            color: var(--ink-secondary) !important;
        }
        [data-testid="stWidgetLabel"] p { color: var(--ink) !important; font-weight: 600; }

        .stApp h1 {
            font-weight: 800 !important;
            letter-spacing: -0.02em;
            background: linear-gradient(90deg, var(--ink) 0%, var(--blue) 55%, var(--aqua) 100%);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent !important;
            animation: fadeSlideDown .6s ease both;
        }
        .hero-subtitle {
            color: var(--ink-secondary) !important;
            font-size: 0.98rem;
            margin-top: -8px;
            animation: fadeSlideDown .7s ease both;
        }
        .hero-underline {
            height: 4px;
            width: 110px;
            border-radius: 4px;
            margin: 10px 0 22px 0;
            background: linear-gradient(90deg, var(--blue), var(--aqua), var(--orange), var(--yellow), var(--magenta));
            background-size: 300% 100%;
            animation: gradientShift 5s ease infinite;
        }

        h2, h3 { color: var(--ink) !important; letter-spacing: -0.01em; }

        @keyframes fadeSlideDown { from {opacity:0; transform: translateY(-10px);} to {opacity:1; transform: translateY(0);} }
        @keyframes fadeSlideUp { from {opacity:0; transform: translateY(16px);} to {opacity:1; transform: translateY(0);} }
        @keyframes gradientShift { 0% {background-position:0% 50%;} 50% {background-position:100% 50%;} 100% {background-position:0% 50%;} }
        @keyframes pulseGlow { 0%,100% {opacity:1;} 50% {opacity:.55;} }

        [data-testid="stMetric"] {
            background: var(--surface);
            border-radius: 14px;
            padding: 14px 18px 10px 18px;
            box-shadow: var(--shadow);
            border: 1px solid var(--border);
            border-top: 3px solid var(--blue);
            transition: transform .25s ease, box-shadow .25s ease;
            animation: fadeSlideUp .55s ease both;
        }
        [data-testid="stMetric"]:hover {
            transform: translateY(-4px);
            box-shadow: var(--shadow-hover);
        }
        [data-testid="stMetricValue"] { font-weight: 700 !important; }
        [data-testid="stMetricLabel"] { color: var(--ink-secondary) !important; font-weight: 600; }

        div[data-testid="column"]:nth-of-type(1) [data-testid="stMetric"] { animation-delay: .04s; border-top-color: var(--blue); }
        div[data-testid="column"]:nth-of-type(2) [data-testid="stMetric"] { animation-delay: .09s; border-top-color: var(--aqua); }
        div[data-testid="column"]:nth-of-type(3) [data-testid="stMetric"] { animation-delay: .14s; border-top-color: var(--orange); }
        div[data-testid="column"]:nth-of-type(4) [data-testid="stMetric"] { animation-delay: .19s; border-top-color: var(--yellow); }
        div[data-testid="column"]:nth-of-type(5) [data-testid="stMetric"] { animation-delay: .24s; border-top-color: var(--magenta); }

        [data-baseweb="tab-list"] { gap: 6px; }
        [data-baseweb="tab"] {
            border-radius: 10px 10px 0 0 !important;
            font-weight: 600;
            color: var(--ink-secondary) !important;
            transition: background .2s ease, color .2s ease;
        }
        [data-baseweb="tab"] p { color: inherit !important; }
        [data-baseweb="tab"]:hover { background: rgba(42,120,214,0.08); }
        [data-baseweb="tab"][aria-selected="true"] { color: var(--blue) !important; }
        [data-baseweb="tab-highlight"] {
            background-color: var(--blue) !important;
            transition: left .35s cubic-bezier(.4,0,.2,1), width .35s cubic-bezier(.4,0,.2,1) !important;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 16px !important;
            box-shadow: var(--shadow);
            transition: box-shadow .3s ease, transform .3s ease;
            animation: fadeSlideUp .5s ease both;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:hover { box-shadow: var(--shadow-hover); }

        [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; box-shadow: var(--shadow); }

        .stButton>button {
            border-radius: 10px;
            font-weight: 600;
            transition: transform .15s ease, box-shadow .15s ease;
        }
        .stButton>button:not(:disabled):hover { transform: translateY(-2px); box-shadow: var(--shadow-hover); }

        [data-testid="stAlert"] {
            border-radius: 12px;
            box-shadow: var(--shadow);
            animation: fadeSlideDown .5s ease both;
        }

        .conf-track {
            background: rgba(11,11,15,0.08);
            border-radius: 6px;
            height: 7px;
            overflow: hidden;
            margin-top: 6px;
            width: 100%;
        }
        .conf-fill {
            height: 100%;
            border-radius: 6px;
            width: 0%;
            animation: fillBar 1s cubic-bezier(.22,1,.36,1) forwards;
        }
        @keyframes fillBar { from { width: 0%; } }
        .conf-caption { font-size: 0.72rem; color: var(--ink-muted) !important; margin-top: 3px; }

        .flag-chip {
            display: inline-block;
            padding: 1px 8px;
            margin: 2px 4px 0 0;
            border-radius: 999px;
            font-size: 0.68rem;
            font-weight: 600;
            background: rgba(235,104,52,0.12);
            color: #b8501f !important;
        }

        @media (max-width: 700px) {
            h1 { font-size: 1.7rem !important; }
            [data-testid="stMetric"] { padding: 10px 12px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(page_title="Syn Bank Wallet Twin", page_icon="💠", layout="wide")
inject_style()

if not WALLET_RESULTS_PATH.exists():
    st.error(f"wallet_results file not found: {WALLET_RESULTS_PATH}")
    st.stop()

results = load_wallet_results(WALLET_RESULTS_PATH)
entities = load_entity_lookup(ENTITY_LOOKUP_SOURCE)
if "entity_name" not in results.columns or "sector" not in results.columns:
    results = results.merge(entities, on="entity_id", how="left")

st.title("Syn Bank — Share of Wallet Intelligence Engine")
st.markdown(
    '<div class="hero-subtitle">Portfolio intelligence across 20 JSE-listed corporate '
    "clients — Cash Management, FX, Trade Finance, Lending and Investment Banking. "
    "Estimate bases differ by design and are never summed across pillars.</div>"
    '<div class="hero-underline"></div>',
    unsafe_allow_html=True,
)

if IS_DUMMY:
    st.warning(
        f"**DUMMY DATA** — reading `{WALLET_RESULTS_PATH.relative_to(REPO_ROOT)}`. The real wallet "
        "engine output (`data/processed/opportunities.parquet`) isn't in this working tree yet; "
        "this dashboard will pick it up automatically the moment it is. Every number below is a "
        "seeded placeholder — never let these figures reach a slide, the PDF, or a briefing note.",
        icon="⚠️",
    )

tab_summary, tab_drilldown, tab_heatmap, tab_briefing = st.tabs(
    ["Portfolio Summary", "Client Drill-Down", "Opportunity Heatmap", "AI Briefing Notes"]
)

# ---------------------------------------------------------------------------
# Tab 1 — Portfolio summary
# ---------------------------------------------------------------------------
with tab_summary:
    st.caption(
        "Each pillar has its own estimate basis and is never summed with another — Cash "
        "Management is anchored on a disclosed-flow identity, FX and Trade Finance are peer-"
        "benchmarked, Lending is a financing-opportunity indicator, and Investment Banking is a "
        "ranked signal with no rand amount at all."
    )

    pillar_totals = (
        results.groupby(["product"], as_index=False)
        .agg(estimate_zar=("estimate_zar", "sum"), observed_zar=("observed_zar", "sum"),
             opportunity_score=("opportunity_score", "mean"), confidence=("confidence", "mean"))
    )

    cols = st.columns(len(PRODUCT_ORDER), gap="medium")
    for col, product in zip(cols, PRODUCT_ORDER):
        row = pillar_totals[pillar_totals["product"] == product].iloc[0]
        label = PRODUCT_LABELS[product]
        with col:
            if product == IB:
                st.metric("IB Signal Strength", f"{row['opportunity_score'] * 100:.0f}/100")
                st.caption(BASIS_CAPTION[product])
            elif product not in (CASH, FX, TRADE):  # LENDING: no observed loan book, no share
                st.metric(f"{label} (opportunity)", format_zar(row["estimate_zar"]))
                st.caption(BASIS_CAPTION[product])
            else:
                share = row["observed_zar"] / row["estimate_zar"] if row["estimate_zar"] else None
                st.metric(f"{label} (addressable)", format_zar(row["estimate_zar"]))
                st.caption(f"Observed: {format_zar(row['observed_zar'])} ({format_pct(share)} share)")

    st.write("")
    with st.container(border=True):
        st.subheader("Addressable / opportunity value by pillar (portfolio total)")
        st.caption("Investment Banking is excluded here — it produces a ranked signal, not a rand amount.")
        fig = go.Figure()
        for product in RAND_PRODUCTS:
            row = pillar_totals[pillar_totals["product"] == product].iloc[0]
            fig.add_bar(
                x=[PRODUCT_LABELS[product]],
                y=[row["estimate_zar"]],
                marker_color=PILLAR_COLOR[product],
                marker_line_width=0,
                name=PRODUCT_LABELS[product],
                showlegend=False,
                text=[format_zar(row["estimate_zar"])],
                textposition="outside",
                textfont=dict(color=INK, size=13),
                hovertemplate=f"{PRODUCT_LABELS[product]}<br>%{{y:,.0f}} ZAR<extra></extra>",
            )
        chart_layout(fig, height=380, yaxis_title="ZAR", bargap=0.35)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    st.write("")
    with st.container(border=True):
        st.subheader("Top 10 growth opportunities")
        top10 = results.sort_values("rank_overall").head(10)[
            ["rank_overall", "entity_name", "sector", "product_label", "estimate_zar", "observed_zar",
             "gap_zar", "confidence", "opportunity_score"]
        ].copy()
        for col in ("estimate_zar", "observed_zar", "gap_zar"):
            top10[col] = top10[col].apply(format_zar)
        top10["opportunity_score"] = top10["opportunity_score"].round(3)
        top10.columns = ["Rank", "Client", "Sector", "Pillar", "Estimate", "Observed",
                          "Gap", "Confidence", "Opportunity Score"]
        st.dataframe(
            top10,
            hide_index=True,
            width="stretch",
            column_config={
                "Confidence": st.column_config.ProgressColumn(
                    "Confidence", min_value=0.0, max_value=1.0, format="%.2f"
                ),
                "Opportunity Score": st.column_config.ProgressColumn(
                    "Opportunity Score", min_value=0.0, max_value=1.0, format="%.2f"
                ),
            },
        )

# ---------------------------------------------------------------------------
# Tab 2 — Client drill-down
# ---------------------------------------------------------------------------
with tab_drilldown:
    entity_name = st.selectbox("Client", entities["entity_name"].tolist())
    entity_id = entities.loc[entities["entity_name"] == entity_name, "entity_id"].iloc[0]
    sector = entities.loc[entities["entity_name"] == entity_name, "sector"].iloc[0]
    st.caption(f"{entity_id} · {sector}")

    client_rows = results[results["entity_id"] == entity_id].set_index("product")

    metric_cols = st.columns(len(PRODUCT_ORDER), gap="medium")
    for col, product in zip(metric_cols, PRODUCT_ORDER):
        if product not in client_rows.index:
            continue
        row = client_rows.loc[product]
        with col:
            if product == IB:
                st.metric(PRODUCT_LABELS[product], f"{row['opportunity_score'] * 100:.0f}/100 signal")
            else:
                st.metric(PRODUCT_LABELS[product], format_zar(row["estimate_zar"]))
            st.caption(f"Share: {format_pct(row.get('share'))}")
            st.markdown(confidence_bar(row["confidence"], PILLAR_COLOR[product]), unsafe_allow_html=True)
            flags = str(row.get("diagnostic_flags") or "").strip()
            if flags:
                chips = "".join(f'<span class="flag-chip">{f.strip()}</span>' for f in flags.split(","))
                st.markdown(chips, unsafe_allow_html=True)

    st.write("")
    with st.container(border=True):
        st.subheader("Estimate vs. observed, by pillar")
        st.caption("Investment Banking has no rand estimate by design — shown as a signal score instead.")
        fig = go.Figure()
        rand_pillars_present = [p for p in RAND_PRODUCTS if p in client_rows.index]
        for i, product in enumerate(rand_pillars_present):
            row = client_rows.loc[product]
            fig.add_bar(
                x=[PRODUCT_LABELS[product]], y=[row["estimate_zar"]],
                marker_color=PILLAR_COLOR[product], marker_line_width=0, showlegend=False,
                hovertemplate=f"{PRODUCT_LABELS[product]} estimate: {format_zar(row['estimate_zar'])}<extra></extra>",
            )
            if pd.notna(row.get("observed_zar")):
                fig.add_trace(go.Scatter(
                    x=[PRODUCT_LABELS[product]], y=[row["observed_zar"]], mode="markers",
                    marker=dict(color=INK, size=12, symbol="diamond"),
                    name="Observed (Syn Bank)", showlegend=(i == 0),
                    hovertemplate=f"Observed: {format_zar(row['observed_zar'])}<extra></extra>",
                ))
        chart_layout(fig, height=420, legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

        if IB in client_rows.index:
            ib_row = client_rows.loc[IB]
            explanation = str(ib_row.get("explanation") or "").strip()
            st.caption(f"Investment Banking — signal {ib_row['opportunity_score'] * 100:.0f}/100. " + explanation)

# ---------------------------------------------------------------------------
# Tab 3 — Opportunity heatmap
# ---------------------------------------------------------------------------
with tab_heatmap:
    with st.container(border=True):
        st.caption(
            "Opportunity score (0.45·gap percentile + 0.30·confidence + 0.25·headroom), ranked "
            "within each pillar — Investment Banking contributes its signal score in place of a gap."
        )

        pivot = results.pivot(index="entity_name", columns="product", values="opportunity_score")
        pivot = pivot[list(PRODUCT_ORDER)]
        order = pivot.mean(axis=1).sort_values(ascending=False).index
        pivot = pivot.loc[order]

        fig = go.Figure(data=go.Heatmap(
            z=pivot.values,
            x=[PRODUCT_LABELS[p] for p in pivot.columns],
            y=pivot.index,
            colorscale=[[i / (len(SEQUENTIAL_BLUE) - 1), c] for i, c in enumerate(SEQUENTIAL_BLUE)],
            xgap=3,
            ygap=3,
            hovertemplate="%{y} · %{x}<br>Opportunity score: %{z:.2f}<extra></extra>",
            colorbar=dict(title="Score", thickness=14),
        ))
        chart_layout(fig, height=680, yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

# ---------------------------------------------------------------------------
# Tab 4 — AI briefing notes (Layer 5, grounded generation)
# ---------------------------------------------------------------------------
with tab_briefing:
    with st.container(border=True):
        st.caption(
            "Grounded call-prep briefing note (5-6 sentences), generated only from the computed "
            "tables shown below — no free generation. The exact JSON context sent to the model "
            "is displayed alongside the note so every sentence can be checked against it."
        )
        briefing_entity_name = st.selectbox("Client", entities["entity_name"].tolist(), key="briefing_client")
        briefing_entity_id = entities.loc[entities["entity_name"] == briefing_entity_name, "entity_id"].iloc[0]
        briefing_sector = entities.loc[entities["entity_name"] == briefing_entity_name, "sector"].iloc[0]

        competitor_evidence = load_competitor_evidence(COMPETITOR_EVIDENCE_PATH)
        context = build_grounding_context(
            briefing_entity_id, results, briefing_entity_name, briefing_sector, competitor_evidence
        )

        api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
        generate_clicked = st.button("Generate briefing note", disabled=not api_key_present)
        if not api_key_present:
            st.info(
                "Set ANTHROPIC_API_KEY in the environment to enable live generation. The grounding "
                "context below is exactly what would be sent — nothing else."
            )

        if generate_clicked:
            import anthropic

            with st.spinner("Generating grounded briefing note..."):
                try:
                    note = generate_briefing_note(context, anthropic.Anthropic())
                    st.success(note)
                except Exception as exc:  # noqa: BLE001 - surface any API/config error directly to the demo user
                    st.error(f"Briefing note generation failed: {exc}")

        with st.expander("Grounding context sent to the model", expanded=not api_key_present):
            st.json(context)
