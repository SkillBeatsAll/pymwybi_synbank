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

# Neon navy/cyan/violet palette sampled from the hackathon reference image.
PILLAR_COLOR = {CASH: "#18d8ff", FX: "#7a6cff", TRADE: "#cf40ff", LENDING: "#00f0c8", IB: "#ff4fd8"}
SEQUENTIAL_BLUE = ["#07122f", "#0b2f7e", "#155fff", "#18d8ff", "#7a6cff", "#cf40ff"]
SURFACE = "#081536"
INK = "#f7fbff"
MUTED_INK = "#8fb7e8"
GRIDLINE = "rgba(24,216,255,0.18)"

# Role each pillar plays: core = carries a share of wallet, supporting = a rand
# opportunity with no share, signal = no rand amount at all, ranked signal only.
PILLAR_ROLE = {CASH: "core", FX: "core", TRADE: "core", LENDING: "supporting", IB: "signal"}
ROLE_COLOR = {"core": "#18d8ff", "supporting": "#00f0c8", "signal": "#ff4fd8"}

CONFIDENCE_COLOR = {"HIGH": "#00f0c8", "MEDIUM": "#7a6cff", "LOW": "#ff4fd8"}
STATUS_COLOR = {
    "PRIORITY": "#ff4fd8", "INVESTIGATE": "#7a6cff",
    "MONITOR": "#18d8ff", "NO_HEADROOM_DEMONSTRATED": "#8fb7e8",
}
SENSITIVITY_WORD = {"STABLE": "stable", "MODERATE": "moderate", "SENSITIVE": "sensitive", "NOT_APPLICABLE": "no range"}

# Verbatim from the model-trust page authored on feat/data-anal (src/syn_wallet's dashboard) —
# these are authored verdicts, not derived from a table, so they're reproduced as static text.
TRUST_STATEMENTS = {
    CASH: {
        "verdict": "ROBUST", "headline": "Identity-anchored. Nothing moves it.",
        "detail": "Addressable Cash Flow is revenue plus cost of sales — two accounting identities, not "
                   "coefficients. No scenario in the 36-run sweep changes it by a rand. This is the only "
                   "pillar whose rand figure can be quoted as a single number.",
    },
    FX: {
        "verdict": "ASSUMPTION_SENSITIVE", "headline": "Peer-benchmark. Quote the range, never the point.",
        "detail": "No disclosure states any client's true cross-border activity, so the denominator IS the "
                   "coefficient. Across the sweep the portfolio FX opportunity spans 7.4x from lowest to "
                   "highest, and the within-pillar ordering falls to rho 0.51 under a median benchmark.",
    },
    TRADE: {
        "verdict": "ASSUMPTION_SENSITIVE", "headline": "Peer-benchmark. Quote the range, never the point.",
        "detail": "Same construction as FX and the same caveat. The portfolio trade opportunity spans 4.0x "
                   "across the sweep. Ordering is steadier than FX at rho 0.85, so the ranking is more "
                   "usable than the total.",
    },
    LENDING: {
        "verdict": "ROBUST", "headline": "A financing opportunity, not a share of wallet.",
        "detail": "Built from disclosed debt structure: debt falling due, undrawn committed facilities, the "
                   "working-capital cycle and capex. Syn Bank's data holds no loan book, so there is no "
                   "observed activity to divide and no share exists. Ordering holds at rho 0.997 and the "
                   "total moves under 5% even when the capex judgement coefficient moves by a third.",
    },
    IB: {
        "verdict": "SIGNAL_ONLY", "headline": "A ranked signal. No rand figure exists.",
        "detail": "Five percentile-ranked balance-sheet facts produce a mandate-likelihood signal. Nothing "
                   "in the data indicates a planned transaction, so no amount is estimated. Its ordering is "
                   "identical in all 36 runs because every threshold behind it is a declared judgement "
                   "rather than a measured coefficient.",
    },
}

# Verbatim methodology paragraphs, same source as TRUST_STATEMENTS.
METHODOLOGY_PARAGRAPHS = {
    "How benchmarks are built": (
        "Where no accounting identity fixes a coefficient it is measured from the client's peers at the "
        "75th percentile — with that client removed from the population. Including it is circular in both "
        "directions: a heavily penetrated client raises the benchmark it is then judged against; a dormant "
        "one drags it down and makes its own share look healthy."
    ),
    "Sector vs. portfolio benchmark": (
        "A sector benchmark is used only where at least three peers remain after that exclusion, otherwise "
        "the portfolio population is used and the reason is recorded per client. A sector frontier built "
        "from one or two companies would be a restatement of those companies."
    ),
    "The 36-run sweep": (
        "Every rand estimate is rebuilt under 36 configurations varying the benchmark percentile "
        "(median / P75 / P80), leave-one-out versus self-inclusive peers, sector versus portfolio scope, "
        "and the capex debt-funded share (0.20 / 0.30 / 0.40)."
    ),
    "Why there's no portfolio total": (
        "The five pillars are never added. Two overlap on the SWIFT channel by an amount the supplied data "
        "cannot resolve, and the five rand figures are measured on incomparable bases."
    ),
}

# entity_id, field label, and why it matters, per pillar — client_features.parquet columns.
SIGNALS_BY_PRODUCT = {
    CASH: [
        ("revenue_total_zar", "Revenue", "Collections leg of addressable cash flow"),
        ("cost_of_sales_zar", "Cost of sales", "Supplier-payment leg"),
        ("employees", "Employees, count", "Payroll mandate signal"),
    ],
    FX: [
        ("revenue_foreign_zar", "Foreign revenue", "Export settlement exposure"),
        ("cost_of_sales_zar", "Cost of sales", "Import settlement exposure"),
        ("fx_forward_notional_zar", "FX forward notional", "Disclosed hedging book"),
    ],
    TRADE: [
        ("cost_of_sales_zar", "Cost of sales", "Import documentary driver"),
        ("revenue_foreign_zar", "Foreign revenue", "Export documentary driver"),
        ("inventory_zar", "Inventory", "Working stock behind trade demand"),
        ("revenue_total_zar", "Revenue", "Guarantee driver"),
    ],
    LENDING: [
        ("debt_current_zar", "Debt falling due", "Refinancing inside 12 months"),
        ("undrawn_facilities_zar", "Undrawn facilities", "Committed headroom in use elsewhere"),
        ("working_capital_zar", "Working capital", "Cycle funded by short-term debt"),
        ("capex_zar", "Capex", "Investment programme"),
        ("gross_debt_zar", "Gross debt", "Total disclosed debt"),
    ],
    IB: [
        ("gross_debt_zar", "Gross debt", "Leverage input"),
        ("debt_current_zar", "Debt falling due", "Near-term maturity input"),
        ("capex_zar", "Capex", "Capex intensity input"),
        ("named_lender_count", "Named lenders, count", "Syndicate breadth input"),
    ],
}


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


PROCESSED_DIR = REPO_ROOT / "data" / "processed"


@st.cache_data
def load_table(name: str) -> pd.DataFrame | None:
    """Load one stage 2-4 output table, or None if it hasn't been built yet.

    Every table this dashboard reads beyond ``opportunities.parquet`` comes from the
    commercial-intelligence layer (stage 4, ``src/syn_wallet/build_intelligence.py``) and the
    sensitivity sweep (stage 3, ``--sensitivity``) — both optional builds. Sections that need
    them degrade to an info message instead of crashing when they're absent.
    """
    path = PROCESSED_DIR / f"{name}.parquet"
    if not path.exists():
        return None
    return duckdb.sql(f"SELECT * FROM read_parquet('{path}')").df()


def clean_label(value: str | None) -> str:
    return "" if value is None else str(value).replace("_", " ")


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
        margin=kwargs.pop("margin", dict(l=10, r=10, t=40, b=10)),
        transition=dict(duration=450, easing="cubic-in-out"),
        hoverlabel=dict(bgcolor="#0c1c46", font=dict(color=INK, size=13), bordercolor="#18d8ff"),
        **kwargs,
    )
    fig.update_xaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE, tickfont_color=MUTED_INK, automargin=True)
    fig.update_yaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE, tickfont_color=MUTED_INK, automargin=True)
    return fig


def confidence_bar(value: float, color: str) -> str:
    pct = max(0.0, min(1.0, float(value))) * 100
    return (
        '<div class="conf-track">'
        f'<div class="conf-fill" style="width:{pct:.0f}%; background:{color};"></div>'
        "</div>"
        f'<div class="conf-caption">Confidence {pct:.0f}%</div>'
    )


def chip_html(text: str, color: str) -> str:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    return f'<span class="pill-chip" style="background:{color}22; border-color:{color}66; color:{color} !important;">{text}</span>'


def status_chip(status: str | None) -> str:
    return chip_html(clean_label(status).lower(), STATUS_COLOR.get(status, MUTED_INK))


def confidence_chip(band: str | None) -> str:
    return chip_html(band or "—", CONFIDENCE_COLOR.get(band, MUTED_INK))


def role_tag(role: str) -> str:
    color = ROLE_COLOR.get(role, MUTED_INK)
    label = {"core": "Share of wallet", "supporting": "Supporting", "signal": "Signal only"}.get(role, role)
    return chip_html(label, color)


def pillar_rule_html(role: str) -> str:
    """Solid / half / dotted top rule, mirroring the CORE / SUPPORTING / SIGNAL_ONLY pillar grammar."""
    color = ROLE_COLOR.get(role, MUTED_INK)
    style = "solid" if role == "core" else "dashed" if role == "supporting" else "dotted"
    return f'<div class="pillar-rule" style="border-top: 3px {style} {color};"></div>'


def range_mark_html(low: float | None, base: float | None, high: float | None, color: str) -> str:
    """A figure that moves as a low-high band with a dot at the base, or a fixed dot if it doesn't.

    Mirrors the two-visual-device design from the model's own dashboard: a range that moves is
    drawn as a band, a figure that does not move gets a lone dot labelled accordingly.
    """
    if low is None or high is None or pd.isna(low) or pd.isna(high) or high <= low:
        base_label = format_zar(base) if base is not None and not pd.isna(base) else "no rand figure"
        return (
            '<div class="range-fixed">'
            f'<span class="range-dot-fixed" style="background:{color};"></span>{base_label} — does not move</div>'
        )
    span = high - low
    pos = max(0.0, min(100.0, (base - low) / span * 100)) if base is not None and not pd.isna(base) else 50.0
    return (
        f'<div class="range-track"><div class="range-band" style="background:{color}33;"></div>'
        f'<div class="range-dot" style="left:{pos:.1f}%; background:{color}; box-shadow:0 0 8px {color};"></div></div>'
        '<div class="range-labels">'
        f'<span>{format_zar(low)}</span><span class="range-base-label">{format_zar(base)}</span><span>{format_zar(high)}</span>'
        "</div>"
    )


def inject_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --surface: #081536;
            --surface-raised: rgba(10, 24, 62, 0.88);
            --surface-deep: #03091f;
            --ink: #f7fbff;
            --ink-secondary: #bdd9ff;
            --ink-muted: #8fb7e8;
            --border: rgba(24,216,255,0.22);
            --shadow: 0 10px 32px rgba(0, 6, 24, 0.42), 0 0 20px rgba(24, 216, 255, 0.08);
            --shadow-hover: 0 16px 42px rgba(0, 6, 24, 0.55), 0 0 30px rgba(207, 64, 255, 0.18);
            --blue: #155fff;
            --aqua: #18d8ff;
            --teal: #00f0c8;
            --violet: #7a6cff;
            --magenta: #cf40ff;
            --pink: #ff4fd8;
        }

        .stApp {
            background:
                radial-gradient(circle at 82% 10%, rgba(24,216,255,0.22) 0, rgba(24,216,255,0.08) 22%, transparent 42%),
                radial-gradient(circle at 60% 78%, rgba(207,64,255,0.26) 0, rgba(122,108,255,0.12) 24%, transparent 48%),
                linear-gradient(145deg, #02061a 0%, #061536 48%, #03091f 100%);
            color: var(--ink) !important;
        }
        .stApp::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            background-image:
                linear-gradient(rgba(24,216,255,0.08) 1px, transparent 1px),
                linear-gradient(90deg, rgba(24,216,255,0.08) 1px, transparent 1px);
            background-size: 72px 72px;
            mask-image: linear-gradient(180deg, rgba(0,0,0,.5), transparent 72%);
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
            background: linear-gradient(90deg, var(--ink) 0%, var(--aqua) 38%, var(--violet) 68%, var(--pink) 100%);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent !important;
            text-shadow: 0 0 28px rgba(24,216,255,0.25);
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
            background: linear-gradient(90deg, var(--aqua), var(--violet), var(--magenta), var(--pink));
            background-size: 300% 100%;
            box-shadow: 0 0 18px rgba(24,216,255,0.7);
            animation: gradientShift 5s ease infinite, pulseGlow 2.4s ease-in-out infinite;
        }

        h2, h3 { color: var(--ink) !important; letter-spacing: -0.01em; }

        @keyframes fadeSlideDown { from {opacity:0; transform: translateY(-10px);} to {opacity:1; transform: translateY(0);} }
        @keyframes fadeSlideUp { from {opacity:0; transform: translateY(16px);} to {opacity:1; transform: translateY(0);} }
        @keyframes gradientShift { 0% {background-position:0% 50%;} 50% {background-position:100% 50%;} 100% {background-position:0% 50%;} }
        @keyframes pulseGlow { 0%,100% {opacity:1;} 50% {opacity:.55;} }

        [data-testid="stMetric"] {
            background: linear-gradient(180deg, rgba(10,24,62,0.94), rgba(5,13,38,0.94));
            border-radius: 14px;
            padding: 14px 18px 10px 18px;
            box-shadow: var(--shadow);
            border: 1px solid var(--border);
            border-top: 3px solid var(--aqua);
            transition: transform .25s ease, box-shadow .25s ease;
            animation: fadeSlideUp .55s ease both;
        }
        [data-testid="stMetric"]:hover {
            transform: translateY(-4px);
            box-shadow: var(--shadow-hover);
        }
        [data-testid="stMetricValue"] { font-weight: 700 !important; }
        [data-testid="stMetricLabel"] {
            color: var(--ink-secondary) !important;
            font-size: 0.76rem;
            font-weight: 600;
            line-height: 1.25;
            min-height: 2.1rem;
            white-space: normal !important;
        }
        [data-testid="stMetricLabel"] * {
            overflow: visible !important;
            text-overflow: clip !important;
            white-space: normal !important;
        }

        div[data-testid="column"]:nth-of-type(1) [data-testid="stMetric"] { animation-delay: .04s; border-top-color: var(--aqua); }
        div[data-testid="column"]:nth-of-type(2) [data-testid="stMetric"] { animation-delay: .09s; border-top-color: var(--violet); }
        div[data-testid="column"]:nth-of-type(3) [data-testid="stMetric"] { animation-delay: .14s; border-top-color: var(--magenta); }
        div[data-testid="column"]:nth-of-type(4) [data-testid="stMetric"] { animation-delay: .19s; border-top-color: var(--teal); }
        div[data-testid="column"]:nth-of-type(5) [data-testid="stMetric"] { animation-delay: .24s; border-top-color: var(--pink); }

        [data-baseweb="tab-list"] { gap: 6px; }
        [data-baseweb="tab"] {
            border-radius: 10px 10px 0 0 !important;
            font-weight: 600;
            color: var(--ink-secondary) !important;
            transition: background .2s ease, color .2s ease;
        }
        [data-baseweb="tab"] p { color: inherit !important; }
        [data-baseweb="tab"]:hover { background: rgba(24,216,255,0.12); }
        [data-baseweb="tab"][aria-selected="true"] { color: var(--aqua) !important; }
        [data-baseweb="tab-highlight"] {
            background: linear-gradient(90deg, var(--aqua), var(--magenta)) !important;
            transition: left .35s cubic-bezier(.4,0,.2,1), width .35s cubic-bezier(.4,0,.2,1) !important;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--surface-raised);
            border-color: var(--border) !important;
            border-radius: 16px !important;
            box-shadow: var(--shadow);
            transition: box-shadow .3s ease, transform .3s ease;
            animation: fadeSlideUp .5s ease both;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:hover { box-shadow: var(--shadow-hover); }

        [data-testid="stDataFrame"] {
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: var(--shadow);
        }

        .stButton>button {
            border-radius: 10px;
            font-weight: 600;
            border: 1px solid rgba(24,216,255,0.42);
            background: linear-gradient(90deg, rgba(24,216,255,0.22), rgba(207,64,255,0.22));
            color: var(--ink);
            transition: transform .15s ease, box-shadow .15s ease;
        }
        .stButton>button:not(:disabled):hover { transform: translateY(-2px); box-shadow: var(--shadow-hover); }

        [data-testid="stAlert"] {
            border-radius: 12px;
            box-shadow: var(--shadow);
            animation: fadeSlideDown .5s ease both;
        }

        .conf-track {
            background: rgba(143,183,232,0.16);
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
            background: rgba(207,64,255,0.18);
            border: 1px solid rgba(255,79,216,0.35);
            color: #ffd6fb !important;
        }

        .pill-chip {
            display: inline-block;
            padding: 2px 10px;
            margin: 2px 4px 2px 0;
            border-radius: 999px;
            font-size: 0.72rem;
            font-weight: 700;
            border: 1px solid;
        }

        .pillar-rule {
            width: 56px;
            margin-bottom: 10px;
        }

        .range-track {
            position: relative;
            height: 8px;
            border-radius: 6px;
            background: rgba(143,183,232,0.14);
            margin-top: 10px;
        }
        .range-band { position: absolute; inset: 0; border-radius: 6px; }
        .range-dot {
            position: absolute;
            top: 50%;
            width: 11px;
            height: 11px;
            border-radius: 50%;
            transform: translate(-50%, -50%);
        }
        .range-labels {
            display: flex;
            justify-content: space-between;
            font-size: 0.68rem;
            color: var(--ink-muted) !important;
            margin-top: 5px;
        }
        .range-base-label { font-weight: 700; color: var(--ink) !important; }
        .range-fixed {
            font-size: 0.78rem;
            color: var(--ink-secondary) !important;
            margin-top: 10px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .range-dot-fixed { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }

        .callout {
            background: rgba(24,216,255,0.08);
            border: 1px solid rgba(24,216,255,0.25);
            border-radius: 10px;
            padding: 12px 14px;
            font-size: 0.85rem;
            color: var(--ink-secondary) !important;
        }
        .callout.warn {
            background: rgba(255,79,216,0.08);
            border-color: rgba(255,79,216,0.3);
        }

        @media (max-width: 1200px) {
            [data-testid="stMetricValue"] { font-size: 1.35rem !important; }
            [data-testid="stMetric"] { padding: 12px 14px 8px 14px; }
        }
        @media (max-width: 700px) {
            h1 { font-size: 1.7rem !important; }
            [data-testid="stMetric"] { padding: 10px 12px; }
            [data-testid="stMetricValue"] { font-size: 1.15rem !important; }
            [data-testid="stMetricLabel"] { font-size: 0.7rem !important; }
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

# Stage 3 (--sensitivity) / stage 4 (build_intelligence) outputs — optional, richer detail.
# Each is None if that build hasn't been run; every section below checks before using one.
selection_detail = load_table("opportunity_selection_detail")
client_intelligence = load_table("client_opportunity_intelligence")
client_cards = load_table("client_opportunity_cards")
opportunity_explanations = load_table("opportunity_explanations")
banker_questions = load_table("banker_questions")
sensitivity_summary = load_table("opportunity_sensitivity_summary")
sensitivity_by_product = load_table("model_sensitivity_by_product")
sensitivity_robustness = load_table("model_sensitivity_robustness")
product_confidence = load_table("product_confidence")
model_diagnostics = load_table("model_diagnostics")
corridor_breakdown = load_table("client_corridor_breakdown")
client_features = load_table("client_features")
portfolio_intelligence = load_table("portfolio_opportunity_intelligence")
INTELLIGENCE_BUILT = client_intelligence is not None and selection_detail is not None

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

tab_summary, tab_drilldown, tab_heatmap, tab_trust, tab_products, tab_briefing = st.tabs(
    ["Portfolio Summary", "Client Drill-Down", "Opportunity Heatmap",
     "Model Trust", "Products", "AI Briefing Notes"]
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
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, theme=None)

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

    if not INTELLIGENCE_BUILT:
        st.info(
            "Run `python -m src.syn_wallet.build_intelligence --overwrite` to unlock the focus list, "
            "concentration, and portfolio-intelligence sections below."
        )
    else:
        st.write("")
        with st.container(border=True):
            st.subheader("Focus list")
            st.caption("Ranked by evidence-weighted opportunity, best first — click through on Client Drill-Down.")
            focus = client_cards.sort_values("primary_opportunity_score", ascending=False)[
                ["entity_name", "sector", "primary_opportunity_product", "primary_opportunity_zar",
                 "confidence_band", "sensitivity", "status"]
            ].copy()
            focus["primary_opportunity_product"] = focus["primary_opportunity_product"].map(
                lambda p: PRODUCT_LABELS.get(p, clean_label(p))
            )
            focus["primary_opportunity_zar"] = focus["primary_opportunity_zar"].apply(format_zar)
            focus["sector"] = focus["sector"].map(clean_label)
            focus["status"] = focus["status"].map(lambda s: clean_label(s).lower())
            focus.columns = ["Client", "Sector", "Focus product", "Opportunity", "Confidence", "Sensitivity", "Action"]
            st.dataframe(focus, hide_index=True, width="stretch")

        st.write("")
        grid_left, grid_right = st.columns(2, gap="medium")
        with grid_left:
            with st.container(border=True):
                st.subheader("Where the focus lands")
                st.caption("Primary product across the 20 clients")
                concentration = client_cards["primary_opportunity_product"].value_counts()
                warn_row = portfolio_intelligence[
                    (portfolio_intelligence["section"] == "primary_concentration")
                    & (portfolio_intelligence["metric"] == "concentration_warning")
                ] if portfolio_intelligence is not None else pd.DataFrame()
                for product, count in concentration.items():
                    label = PRODUCT_LABELS.get(product, clean_label(product))
                    fig = go.Figure(go.Bar(
                        x=[count], y=[label], orientation="h",
                        marker_color=PILLAR_COLOR.get(product, MUTED_INK), marker_line_width=0,
                        text=[str(count)], textposition="outside",
                        hovertemplate=f"{label}: {count} clients<extra></extra>",
                    ))
                    chart_layout(fig, height=46, margin=dict(l=10, r=30, t=2, b=2),
                                 xaxis=dict(visible=False, range=[0, 20]), yaxis=dict(visible=False))
                    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, theme=None)
                    st.caption(label)
                if len(warn_row):
                    st.markdown(f'<div class="callout warn">{warn_row.iloc[0]["note"]}</div>', unsafe_allow_html=True)
        with grid_right:
            with st.container(border=True):
                st.subheader("Large but weakly evidenced")
                st.caption("Biggest rand figures on LOW confidence — capped at monitor")
                risky = portfolio_intelligence[
                    portfolio_intelligence["section"] == "low_confidence_high_value"
                ].head(6) if portfolio_intelligence is not None else pd.DataFrame()
                if len(risky):
                    risky_view = risky[["entity_name", "product_label", "value_text"]].copy()
                    risky_view.columns = ["Client", "Pillar", "Opportunity"]
                    st.dataframe(risky_view, hide_index=True, width="stretch")
                else:
                    st.caption("No LOW-confidence, high-value rows found.")

        multi = portfolio_intelligence[
            portfolio_intelligence["section"] == "multiple_opportunities"
        ] if portfolio_intelligence is not None else pd.DataFrame()
        if len(multi):
            st.write("")
            with st.container(border=True):
                st.subheader("Clients with more than one live opportunity")
                st.caption("Breadth of coverage, counted — the pillars are not additive")
                multi_view = multi[["entity_name", "sector", "value_numeric", "value_text"]].copy()
                multi_view["sector"] = multi_view["sector"].map(clean_label)
                multi_view["value_numeric"] = multi_view["value_numeric"].astype(int)
                multi_view["value_text"] = multi_view["value_text"].map(clean_label)
                multi_view.columns = ["Client", "Sector", "Live pillars", "Which"]
                st.dataframe(multi_view, hide_index=True, width="stretch")

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
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, theme=None)

        if IB in client_rows.index:
            ib_row = client_rows.loc[IB]
            explanation = str(ib_row.get("explanation") or "").strip()
            st.caption(f"Investment Banking — signal {ib_row['opportunity_score'] * 100:.0f}/100. " + explanation)

    if not INTELLIGENCE_BUILT:
        st.info(
            "Run `python -m src.syn_wallet.build_wallet --overwrite --sensitivity` and "
            "`python -m src.syn_wallet.build_intelligence --overwrite` to unlock the relationship "
            "snapshot, banker questions, and range-of-outcomes sections below."
        )
    else:
        profile_rows = client_intelligence[client_intelligence["entity_id"] == entity_id]
        if not len(profile_rows):
            st.caption("No commercial-intelligence profile for this client.")
        else:
            profile = profile_rows.iloc[0]

            st.write("")
            with st.container(border=True):
                st.markdown(pillar_rule_html(PILLAR_ROLE.get(profile.get("primary_product"), "core")), unsafe_allow_html=True)
                head_l, head_r = st.columns([4, 1])
                with head_l:
                    st.subheader(f"Focus: {profile.get('primary_label', '—')}")
                with head_r:
                    st.markdown(status_chip(profile.get("primary_status")) + confidence_chip(profile.get("primary_confidence_band")),
                                unsafe_allow_html=True)
                st.markdown(f'<div class="callout">{profile.get("opportunity_summary", "")}</div>', unsafe_allow_html=True)

            st.write("")
            with st.container(border=True):
                st.subheader("Relationship snapshot")
                st.caption("What Syn Bank actually handled this fiscal year — measured, not estimated.")
                # cash's addressable column is irregularly named vs. fx/trade's own {prefix}_addressable_zar.
                addressable_field = {CASH: "addressable_cash_flow_zar", FX: "fx_addressable_zar", TRADE: "trade_addressable_zar"}
                snap_cols = st.columns(3, gap="medium")
                for col, product in zip(snap_cols, (CASH, FX, TRADE)):
                    prefix = {CASH: "cash", FX: "fx", TRADE: "trade"}[product]
                    observed = profile.get(f"{prefix}_observed_zar")
                    addressable = profile.get(addressable_field[product])
                    share = profile.get(f"{prefix}_share")
                    with col:
                        st.markdown(f"**{PRODUCT_LABELS[product]}**")
                        st.markdown(f'<div class="v num" style="font-size:1.3rem;font-weight:700;">{format_zar(observed)}</div>', unsafe_allow_html=True)
                        st.caption(f"of {format_zar(addressable)} addressable")
                        pct = max(0.0, min(1.0, share)) * 100 if pd.notna(share) else 0.0
                        st.markdown(
                            f'<div class="conf-track"><div class="conf-fill" style="width:{pct:.1f}%; background:{PILLAR_COLOR[product]};"></div></div>',
                            unsafe_allow_html=True,
                        )
                        st.caption(f"{format_pct(share)} share")

            st.write("")
            with st.container(border=True):
                st.subheader("Share of wallet")
                st.caption("Only the three core pillars carry a share. Lending and IB are opportunities, not shares.")
                share_cols = st.columns(3, gap="medium")
                for col, product in zip(share_cols, (CASH, FX, TRADE)):
                    prefix = {CASH: "cash", FX: "fx", TRADE: "trade"}[product]
                    with col:
                        st.markdown(pillar_rule_html("core"), unsafe_allow_html=True)
                        st.markdown(f"**{format_pct(profile.get(f'{prefix}_share'))}**")
                        st.caption(BASIS_CAPTION[product])
                        band = profile.get(f"{prefix}_confidence_band")
                        st.markdown(confidence_chip(band), unsafe_allow_html=True)
                        st.caption("Addressable range (36 tested assumptions)")
                        low, base, high = (
                            profile.get(f"{prefix}_estimate_low") if product != CASH else None,
                            profile.get(addressable_field[product]),
                            profile.get(f"{prefix}_estimate_high") if product != CASH else None,
                        )
                        st.markdown(range_mark_html(low, base, high, PILLAR_COLOR[product]), unsafe_allow_html=True)

            st.write("")
            with st.container(border=True):
                st.subheader("Opportunities")
                client_selection = selection_detail[selection_detail["entity_id"] == entity_id].copy()
                client_selection = client_selection.sort_values("selection_rank_for_client")
                slot_view = client_selection[
                    ["product_label", "opportunity_zar", "confidence_band", "sensitivity_flag",
                     "selection_rank_for_client", "status_action", "selection_slot"]
                ].copy()
                slot_view["opportunity_zar"] = [
                    "Signal only" if p == IB else format_zar(v)
                    for p, v in zip(client_selection["product"], slot_view["opportunity_zar"])
                ]
                slot_view["sensitivity_flag"] = slot_view["sensitivity_flag"].map(lambda v: SENSITIVITY_WORD.get(v, clean_label(v)))
                slot_view["selection_slot"] = slot_view["selection_slot"].map(clean_label)
                slot_view.columns = ["Product", "Opportunity", "Confidence", "Sensitivity", "Rank", "Recommended action", "Slot"]
                st.dataframe(slot_view, hide_index=True, width="stretch")

            fin_products = [p for p in PRODUCT_ORDER if PILLAR_ROLE[p] in ("core", "supporting") or p == profile.get("primary_product")]
            if client_features is not None:
                client_feature_row = client_features[client_features["entity_id"] == entity_id]
                if len(client_feature_row):
                    feature_row = client_feature_row.iloc[0]
                    st.write("")
                    with st.container(border=True):
                        st.subheader("Financial signals")
                        st.caption("The disclosed figures that drive each estimate — not the full field store.")
                        sig_cols = st.columns(2, gap="medium")
                        for i, product in enumerate(fin_products):
                            with sig_cols[i % 2]:
                                st.markdown(f"**{PRODUCT_LABELS[product]}**")
                                for field, field_label, why in SIGNALS_BY_PRODUCT[product]:
                                    value = feature_row.get(field)
                                    display = f"{value:,.0f}" if field in ("employees", "named_lender_count") and pd.notna(value) else format_zar(value)
                                    st.markdown(
                                        f'<div class="bar-row" style="display:flex;justify-content:space-between;padding:2px 0;">'
                                        f'<span>{field_label}</span><span style="font-weight:600;">{display}</span></div>'
                                        f'<div class="conf-caption" style="margin-bottom:6px;">{why}</div>',
                                        unsafe_allow_html=True,
                                    )

            if opportunity_explanations is not None and pd.notna(profile.get("primary_product")):
                explanation_row = opportunity_explanations[
                    (opportunity_explanations["entity_id"] == entity_id)
                    & (opportunity_explanations["product"] == profile.get("primary_product"))
                ]
                if len(explanation_row):
                    explanation_row = explanation_row.iloc[0]
                    st.write("")
                    with st.container(border=True):
                        st.subheader("Why this is the focus")
                        st.write(explanation_row.get("why", ""))
                        limitation = explanation_row.get("limitation")
                        if limitation:
                            st.markdown(f'<div class="callout warn">Limitation. {limitation}</div>', unsafe_allow_html=True)
                        next_action = explanation_row.get("next_action")
                        if next_action:
                            st.caption(f"Next action: {next_action}")

            if banker_questions is not None:
                client_questions = banker_questions[banker_questions["entity_id"] == entity_id].sort_values("question_index")
                if len(client_questions):
                    st.write("")
                    with st.container(border=True):
                        st.subheader("Questions for the client")
                        st.caption("Generated from this client's own figures, not from a template.")
                        for i, (_, q) in enumerate(client_questions.iterrows(), start=1):
                            st.markdown(f"**{i}. {q['question']}**")
                            st.caption(q["rationale"])

            if model_diagnostics is not None:
                client_diagnostics = model_diagnostics[model_diagnostics["entity_id"] == entity_id]
                if len(client_diagnostics):
                    st.write("")
                    with st.container(border=True):
                        st.subheader("Model diagnostics")
                        st.caption("One row per finding — the detail text wraps in full below each one.")
                        severity_color = {"HIGH": "#ff4fd8", "MEDIUM": "#7a6cff", "INFO": "#18d8ff"}
                        for i, (_, row) in enumerate(client_diagnostics.iterrows()):
                            pillar_label = PRODUCT_LABELS.get(row["product"], clean_label(row["product"]))
                            finding = clean_label(row["diagnostic"])
                            st.markdown(
                                chip_html(row["severity"], severity_color.get(row["severity"], MUTED_INK))
                                + f" &nbsp; **{pillar_label} — {finding}**",
                                unsafe_allow_html=True,
                            )
                            st.caption(row["detail"])
                            if i < len(client_diagnostics) - 1:
                                st.markdown("<hr style='border-color: rgba(24,216,255,0.14); margin: 10px 0;'>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Tab 3 — Opportunity heatmap
# ---------------------------------------------------------------------------
with tab_heatmap:
    with st.container(border=True):
        score_col = "commercial_opportunity_score"

        if INTELLIGENCE_BUILT:
            heat_source = selection_detail
            st.caption(
                f"{heat_source['entity_id'].nunique()} clients × {len(PRODUCT_ORDER)} pillars — fill is the "
                "commercial opportunity score, filterable by sector, pillar, confidence and status."
            )
            filter_cols = st.columns(4, gap="small")
            sectors = ["All"] + sorted(heat_source["sector"].dropna().unique().tolist())
            products_f = ["All"] + list(PRODUCT_ORDER)
            bands_f = ["All", "HIGH", "MEDIUM", "LOW"]
            statuses_f = ["All", "PRIORITY", "INVESTIGATE", "MONITOR", "NO_HEADROOM_DEMONSTRATED"]
            with filter_cols[0]:
                sector_choice = st.selectbox("Sector", sectors, format_func=clean_label)
            with filter_cols[1]:
                product_choice = st.selectbox("Pillar", products_f, format_func=lambda p: PRODUCT_LABELS.get(p, p))
            with filter_cols[2]:
                band_choice = st.selectbox("Confidence", bands_f)
            with filter_cols[3]:
                status_choice = st.selectbox("Status", statuses_f, format_func=clean_label)

            filtered = heat_source.copy()
            if sector_choice != "All":
                filtered = filtered[filtered["sector"] == sector_choice]
            if product_choice != "All":
                filtered = filtered[filtered["product"] == product_choice]
            if band_choice != "All":
                filtered = filtered[filtered["confidence_band"] == band_choice]
            if status_choice != "All":
                filtered = filtered[filtered["opportunity_status"] == status_choice]

            if not len(filtered):
                st.warning("No cells match this filter combination.")
            else:
                pivot = filtered.pivot_table(index="entity_name", columns="product", values=score_col, aggfunc="first")
                present_products = [p for p in PRODUCT_ORDER if p in pivot.columns]
                pivot = pivot[present_products]
                order = pivot.mean(axis=1).sort_values(ascending=False).index
                pivot = pivot.loc[order]

                hover_text = []
                for client in pivot.index:
                    row_text = []
                    for product in present_products:
                        cell = filtered[(filtered["entity_name"] == client) & (filtered["product"] == product)]
                        if len(cell):
                            c = cell.iloc[0]
                            opp = "signal only" if product == IB else format_zar(c.get("opportunity_zar"))
                            row_text.append(
                                f"{client} · {PRODUCT_LABELS[product]}<br>"
                                f"Opportunity: {opp}<br>"
                                f"Commercial score: {c.get(score_col, float('nan')):.2f}<br>"
                                f"Confidence: {c.get('confidence', float('nan')):.2f} ({c.get('confidence_band', '—')})<br>"
                                f"Headroom: {format_pct(c.get('headroom_fraction'))}<br>"
                                f"Sensitivity: {SENSITIVITY_WORD.get(c.get('sensitivity_flag'), '—')}<br>"
                                f"{c.get('status_action', '')}"
                            )
                        else:
                            row_text.append("")
                    hover_text.append(row_text)

                fig = go.Figure(data=go.Heatmap(
                    z=pivot.values,
                    x=[PRODUCT_LABELS[p] for p in present_products],
                    y=pivot.index,
                    colorscale=[[i / (len(SEQUENTIAL_BLUE) - 1), c] for i, c in enumerate(SEQUENTIAL_BLUE)],
                    xgap=3, ygap=3, zmin=0, zmax=1,
                    text=hover_text, hoverinfo="text",
                    hoverlabel=dict(bgcolor="#0c1c46", font=dict(color=INK, size=13), bordercolor="#18d8ff"),
                    colorbar=dict(title="Score", thickness=14),
                ))
                chart_layout(fig, height=max(420, 32 * len(pivot)), yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, theme=None)

                st.markdown(
                    '<div class="callout">Reading the grid. A solid dark cell is a well-evidenced '
                    "opportunity. A pale cell is the same score on LOW confidence — the model can size it "
                    "but cannot stand behind it. Fill carries magnitude; hover carries evidence.</div>",
                    unsafe_allow_html=True,
                )
        else:
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
                hoverlabel=dict(bgcolor="#0c1c46", font=dict(color=INK, size=13), bordercolor="#18d8ff"),
                colorbar=dict(title="Score", thickness=14),
            ))
            chart_layout(fig, height=680, yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, theme=None)

# ---------------------------------------------------------------------------
# Tab 4 — Model trust
# ---------------------------------------------------------------------------
with tab_trust:
    if not INTELLIGENCE_BUILT:
        st.info(
            "Run `python -m src.syn_wallet.build_wallet --overwrite --sensitivity` to unlock this tab — "
            "it reads the 36-scenario sensitivity sweep."
        )
    else:
        st.markdown(
            '<div class="callout">What the model is sure of, and what it is not. Every rand estimate was '
            "rebuilt under 36 model configurations. Cash management does not move at all. FX and trade move "
            "by several times, because no disclosure states either activity's true size — so the benchmark "
            "choice is the denominator.</div>",
            unsafe_allow_html=True,
        )

        st.write("")
        trust_cols = st.columns(2, gap="medium")
        for i, product in enumerate(PRODUCT_ORDER):
            statement = TRUST_STATEMENTS[product]
            role = PILLAR_ROLE[product]
            with trust_cols[i % 2]:
                with st.container(border=True):
                    st.markdown(pillar_rule_html(role), unsafe_allow_html=True)
                    st.markdown(f"**{PRODUCT_LABELS[product]}** — {statement['headline']}")
                    st.markdown(chip_html(clean_label(statement["verdict"]),
                                           "#00f0c8" if statement["verdict"] == "ROBUST" else "#ff4fd8" if "SENSITIVE" in statement["verdict"] else MUTED_INK),
                                unsafe_allow_html=True)
                    st.caption(statement["detail"])

                    if sensitivity_summary is not None:
                        product_sens = sensitivity_summary[sensitivity_summary["product"] == product]
                        if len(product_sens):
                            flag_counts = product_sens["sensitivity_flag"].value_counts()
                            st.caption(
                                " · ".join(f"{flag_counts.get(f, 0)} {SENSITIVITY_WORD.get(f, f).lower()}"
                                           for f in ("STABLE", "MODERATE", "SENSITIVE") if flag_counts.get(f, 0))
                                or "No sensitivity variation recorded."
                            )
                    if product_confidence is not None:
                        pc = product_confidence[product_confidence["product"] == product]
                        if len(pc):
                            pc = pc.iloc[0]
                            st.markdown(confidence_bar(pc["mean_confidence"], PILLAR_COLOR[product]), unsafe_allow_html=True)
                            st.markdown(
                                confidence_chip("HIGH") + f" {pc['pct_high'] * 100:.0f}% &nbsp; "
                                + confidence_chip("MEDIUM") + f" {pc['pct_medium'] * 100:.0f}% &nbsp; "
                                + confidence_chip("LOW") + f" {pc['pct_low'] * 100:.0f}%",
                                unsafe_allow_html=True,
                            )

        if sensitivity_summary is not None:
            st.write("")
            with st.container(border=True):
                st.subheader("The widest ranges in the book")
                st.caption("Where a single quoted figure would be least defensible.")
                widest = sensitivity_summary.sort_values("estimate_range_pct", ascending=False).head(10).copy()
                widest["entity_name"] = widest["entity_id"].map(
                    entities.set_index("entity_id")["entity_name"]
                )
                widest["Pillar"] = widest["product"].map(lambda p: PRODUCT_LABELS.get(p, clean_label(p)))
                widest["Range"] = widest.apply(lambda r: f"{format_zar(r['estimate_low'])} – {format_zar(r['estimate_high'])}", axis=1)
                widest["Spread"] = (widest["estimate_range_pct"] * 100).round(0).astype(int).astype(str) + "%"
                widest["Ranking"] = widest["rank_stability"].map(clean_label)
                widest_view = widest[["entity_name", "Pillar", "Range", "Spread", "Ranking"]]
                widest_view.columns = ["Client", "Pillar", "Range", "Spread", "Ranking"]
                st.dataframe(widest_view, hide_index=True, width="stretch")

        if sensitivity_robustness is not None:
            st.write("")
            with st.container(border=True):
                st.subheader("Verdict by pillar, from the 36-run sweep")
                robustness_view = sensitivity_robustness.copy()
                robustness_view["product_label"] = robustness_view["product"].map(lambda p: PRODUCT_LABELS.get(p, clean_label(p)))
                robustness_view["verdict"] = robustness_view["verdict"].map(clean_label)
                robustness_view["max_abs_total_gap_drift"] = robustness_view["max_abs_total_gap_drift"].apply(
                    lambda v: format_pct(v) if pd.notna(v) else "no rand figure"
                )
                robustness_view = robustness_view[
                    ["product_label", "verdict", "min_spearman_rank_in_product", "max_abs_total_gap_drift", "note"]
                ]
                robustness_view.columns = ["Pillar", "Verdict", "Worst rank correlation", "Worst drift", "Reading"]
                st.dataframe(robustness_view, hide_index=True, width="stretch",
                             column_config={"Worst rank correlation": st.column_config.NumberColumn(format="%.3f")})

        st.write("")
        with st.container(border=True):
            st.subheader("How the benchmarks are built")
            method_cols = st.columns(2, gap="medium")
            for i, (heading, paragraph) in enumerate(METHODOLOGY_PARAGRAPHS.items()):
                with method_cols[i % 2]:
                    st.markdown(f"**{heading}**")
                    st.caption(paragraph)

        if model_diagnostics is not None:
            st.write("")
            with st.container(border=True):
                st.subheader("Open model diagnostics")
                severity_order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
                portfolio_diag = model_diagnostics.copy()
                portfolio_diag["_order"] = portfolio_diag["severity"].map(severity_order).fillna(3)
                portfolio_diag = portfolio_diag.sort_values("_order").head(25)
                portfolio_diag["scope"] = [
                    f"{name} · {clean_label(product)}" if pd.notna(name) else "portfolio"
                    for name, product in zip(portfolio_diag["entity_name"], portfolio_diag["product"])
                ]
                diag_view = portfolio_diag[["severity", "scope", "detail"]]
                diag_view.columns = ["Severity", "Scope", "Finding"]
                st.dataframe(diag_view, hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Tab 5 — Products
# ---------------------------------------------------------------------------
with tab_products:
    if not INTELLIGENCE_BUILT:
        st.info("Run `python -m src.syn_wallet.build_intelligence --overwrite` to unlock this tab.")
    else:
        product_choice_2 = st.radio(
            "Pillar", PRODUCT_ORDER, format_func=lambda p: PRODUCT_LABELS[p], horizontal=True,
        )
        role = PILLAR_ROLE[product_choice_2]

        with st.container(border=True):
            st.markdown(pillar_rule_html(role), unsafe_allow_html=True)
            st.markdown(f"### {PRODUCT_LABELS[product_choice_2]}")
            st.markdown(role_tag(role), unsafe_allow_html=True)
            totals = pillar_totals[pillar_totals["product"] == product_choice_2].iloc[0]
            stat_cols = st.columns(4, gap="medium")
            if product_choice_2 == IB:
                with stat_cols[0]:
                    st.metric("Rand figure", "None — signal only, by design")
            else:
                with stat_cols[0]:
                    label = "Addressable" if product_choice_2 in (CASH, FX, TRADE) else "Opportunity estimate"
                    st.metric(label, format_zar(totals["estimate_zar"]))
                    st.caption(BASIS_CAPTION[product_choice_2])
                with stat_cols[1]:
                    st.metric("Observed", format_zar(totals["observed_zar"]))
                if product_choice_2 in (CASH, FX, TRADE):
                    with stat_cols[2]:
                        share = totals["observed_zar"] / totals["estimate_zar"] if totals["estimate_zar"] else None
                        st.metric("Portfolio share", format_pct(share))
                with stat_cols[3]:
                    gap = totals["estimate_zar"] - totals["observed_zar"] if pd.notna(totals["observed_zar"]) else totals["estimate_zar"]
                    st.metric("Opportunity", format_zar(gap))

        st.write("")
        with st.container(border=True):
            st.subheader("Observed detail")
            st.caption("Measured activity and disclosed figures. Nothing here is an estimate.")

            def _bar_panel(title: str, items: list[tuple[str, float, str | None]], as_count: bool = False) -> None:
                st.markdown(f"**{title}**")
                labels = [i[0] for i in items]
                values = [i[1] if i[1] is not None else 0 for i in items]
                text_fmt = (lambda v: f"{v:.0f} client{'s' if v != 1 else ''}") if as_count else format_zar
                fig = go.Figure(go.Bar(
                    x=values, y=labels, orientation="h",
                    marker_color=PILLAR_COLOR[product_choice_2], marker_line_width=0,
                    text=[text_fmt(v) for v in values], textposition="outside",
                ))
                chart_layout(fig, height=44 * len(items) + 40, xaxis=dict(visible=False), margin=dict(l=10, r=60, t=10, b=10))
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, theme=None)
                for label, _, why in items:
                    if why:
                        st.caption(f"{label}: {why}")

            if client_features is None:
                st.caption("Feature table not available.")
            else:
                sums = client_features.sum(numeric_only=True)
                panels: list[tuple[str, list[tuple[str, float, str | None]]]] = []
                if product_choice_2 == CASH:
                    panels.append(("In-scope legs", [
                        ("Collections", sums.get("txn_collections_domestic_volume_zar_fy"), None),
                        ("Supplier payments", sums.get("txn_supplier_payments_domestic_volume_zar_fy"), None),
                    ]))
                    panels.append(("Deliberately outside the denominator", [
                        ("Intercompany sweeps", sums.get("txn_intercompany_sweeps_volume_zar_fy"), "No external anchor"),
                        ("Payroll", sums.get("txn_payroll_volume_zar_fy"), "No employee-cost field to size against"),
                        ("Tax", sums.get("txn_tax_volume_zar_fy"), "No tax-charge field to size against"),
                        ("SWIFT channel", sums.get("txn_swift_channel_volume_zar_fy"), "Overlaps FX by an unresolvable amount"),
                    ]))
                elif product_choice_2 == FX:
                    panels.append(("Currency pairs", [
                        ("USD", sums.get("xb_pair_usd_volume_zar_fy"), None),
                        ("EUR", sums.get("xb_pair_eur_volume_zar_fy"), None),
                        ("GBP", sums.get("xb_pair_gbp_volume_zar_fy"), None),
                        ("AED", sums.get("xb_pair_aed_volume_zar_fy"), None),
                        ("CNY", sums.get("xb_pair_cny_volume_zar_fy"), None),
                    ]))
                    panels.append(("Direction", [
                        ("Inbound", sums.get("xb_inbound_volume_zar_fy"), None),
                        ("Outbound", sums.get("xb_outbound_volume_zar_fy"), None),
                    ]))
                elif product_choice_2 == TRADE:
                    panels.append(("Instrument types", [
                        ("Letters of credit", sums.get("tf_letters_of_credit_value_zar_fy"), None),
                        ("Guarantees", sums.get("tf_guarantees_value_zar_fy"), None),
                        ("Export collections", sums.get("tf_export_collections_value_zar_fy"), None),
                    ]))
                    panels.append(("Direction", [
                        ("Import", sums.get("tf_import_value_zar_fy"), None),
                        ("Export", sums.get("tf_export_value_zar_fy"), None),
                    ]))
                elif product_choice_2 == LENDING:
                    panels.append(("Financing components", [
                        ("Refinancing (debt falling due)", sums.get("debt_current_zar"), "Repayable within 12 months"),
                        ("Undrawn facilities", sums.get("undrawn_facilities_zar"), "Committed headroom in use elsewhere"),
                        ("Working capital", sums.get("working_capital_zar"), "Cycle funded by short-term debt"),
                        ("Capex", sums.get("capex_zar"), "Investment programme"),
                    ]))

                if product_choice_2 in (FX, TRADE) and corridor_breakdown is not None:
                    pillar_name = "cross_border" if product_choice_2 == FX else "trade_finance"
                    countries = corridor_breakdown[
                        (corridor_breakdown["pillar"] == pillar_name)
                        & (corridor_breakdown["scope"] == "fiscal_year")
                        & (corridor_breakdown["dimension"] == "counterparty_country")
                    ].groupby("dimension_value", as_index=False)["volume_zar"].sum().sort_values("volume_zar", ascending=False).head(8)
                    if len(countries):
                        panels.append(("Top counterparty countries", [
                            (row["dimension_value"], row["volume_zar"], None) for _, row in countries.iterrows()
                        ]))

                if product_choice_2 == IB:
                    if client_intelligence is not None:
                        category_counts = client_intelligence["ib_opportunity_type"].value_counts()
                        panels.append(("Mandate categories", [
                            (clean_label(cat), count, None) for cat, count in category_counts.items()
                        ]))

                if panels:
                    panel_cols = st.columns(2, gap="medium") if len(panels) > 1 else [st.container()]
                    for i, (title, items) in enumerate(panels):
                        items = [item for item in items if item[1] is not None]
                        if not items:
                            continue
                        with panel_cols[i % len(panel_cols)]:
                            _bar_panel(title, items, as_count=(title == "Mandate categories"))
                else:
                    st.caption("No observed-detail breakdown available for this pillar.")

        if selection_detail is not None:
            st.write("")
            with st.container(border=True):
                st.subheader("Clients")
                product_clients = selection_detail[selection_detail["product"] == product_choice_2].sort_values(
                    "selection_score", ascending=False
                )
                cols_wanted = ["entity_name", "sector"]
                if product_choice_2 != IB:
                    cols_wanted += ["observed_zar", "addressable_zar"]
                if product_choice_2 in (CASH, FX, TRADE):
                    cols_wanted += ["share"]
                if product_choice_2 != IB:
                    cols_wanted += ["opportunity_zar"]
                cols_wanted += ["confidence_band", "opportunity_status"]
                product_view = product_clients[cols_wanted].copy()
                for col in ("observed_zar", "addressable_zar", "opportunity_zar"):
                    if col in product_view.columns:
                        product_view[col] = product_view[col].apply(format_zar)
                if "share" in product_view.columns:
                    product_view["share"] = product_view["share"].apply(format_pct)
                product_view["sector"] = product_view["sector"].map(clean_label)
                product_view["opportunity_status"] = product_view["opportunity_status"].map(lambda s: clean_label(s).lower())
                rename_map = {
                    "entity_name": "Client", "sector": "Sector", "observed_zar": "Observed",
                    "addressable_zar": "Addressable", "share": "Share", "opportunity_zar": "Opportunity",
                    "confidence_band": "Confidence", "opportunity_status": "Status",
                }
                product_view.columns = [rename_map[c] for c in product_view.columns]
                st.dataframe(product_view, hide_index=True, width="stretch")

        if product_choice_2 == IB and client_intelligence is not None:
            st.write("")
            with st.container(border=True):
                st.subheader("Signal ranking")
                top_signal = client_intelligence.sort_values("ib_signal_score", ascending=False).head(10)
                for _, row in top_signal.iterrows():
                    score = row["ib_signal_score"] if pd.notna(row["ib_signal_score"]) else 0.0
                    bar_col, label_col = st.columns([3, 1])
                    with bar_col:
                        st.markdown(f"{row['entity_name']}", help=clean_label(row.get("ib_opportunity_type")))
                        st.markdown(
                            f'<div class="conf-track"><div class="conf-fill" style="width:{score * 100:.0f}%; background:{PILLAR_COLOR[IB]};"></div></div>',
                            unsafe_allow_html=True,
                        )
                    with label_col:
                        st.markdown(f"**{score:.2f}**")
                        st.caption(clean_label(row.get("ib_opportunity_type")))

# ---------------------------------------------------------------------------
# Tab 6 — AI briefing notes (Layer 5, grounded generation)
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
