/* Syn Bank Coverage Desk — front end.
 *
 * This file renders. It does not calculate. Every figure it shows arrives from
 * the API already computed and already formatted, because the moment a
 * currency figure is derived in the browser the dashboard and the model can
 * disagree — and the model is the one under audit.
 *
 * The two visual devices that carry the argument:
 *   rangeMark()  draws a figure that moves as a band, and a figure that does
 *                not as a lone dot. Cash is a dot. FX and trade are bands.
 *   pillarRule   solid / half / dotted for CORE / SUPPORTING / SIGNAL_ONLY.
 */

const state = {
  route: { page: "portfolio", arg: null },
  cache: new Map(),
  health: null,
  heatFilters: { sector: "", product: "", band: "", status: "" },
  copilotOpen: false,
  copilotBusy: false,
  copilotClient: null,
};

/* ------------------------------------------------------------------ api */

async function api(path) {
  if (state.cache.has(path)) return state.cache.get(path);
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || `Request failed: ${path}`);
  }
  const data = await response.json();
  state.cache.set(path, data);
  return data;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(detail.detail || "Request failed");
  }
  return response.json();
}

/* -------------------------------------------------------------- helpers */

const el = (tag, attrs, ...children) => {
  const node = document.createElement(tag);
  // `attrs = {}` as a default would only cover `undefined`; call sites pass an
  // explicit `null` when an element has no attributes, and Object.entries(null)
  // throws.
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
};

const esc = (text) =>
  String(text ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const dash = (value) => (value === null || value === undefined ? "—" : value);
const pct1 = (value) => (value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`);
const pct0 = (value) => (value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`);
const score2 = (value) => (value === null || value === undefined ? "—" : value.toFixed(2));

const roleClass = (role) => (role === "core" ? "core" : role === "supporting" ? "supporting" : "signal");
const roleLabel = (role) =>
  role === "core" ? "Share of wallet" : role === "supporting" ? "Supporting" : "Signal only";

/* Tables are where hand-nested calls go wrong: a table is six levels deep and
 * one missing bracket silently takes the whole module out. Building them
 * through one helper keeps every call site two levels deep instead of eight. */
function dataTable(headers, rows) {
  const head = el("tr", null, headers.map((header) =>
    typeof header === "string"
      ? el("th", null, header)
      : el("th", { class: header.r ? "r" : null }, header.label)));
  return el("table", { class: "data" },
    el("thead", null, head),
    el("tbody", null, rows));
}

/* A card with a titled header and an arbitrary body. */
function card(title, subtitle, body, aside = null, rule = null) {
  const head = el("div", { class: "card-head" },
    el("div", null,
      el("h2", null, title),
      subtitle ? el("div", { class: "sub" }, subtitle) : null),
    aside);
  return el("div", { class: "card" },
    rule ? el("div", { class: `pillar-rule ${rule}` }) : null,
    head,
    body);
}

const tableBody = (rows) => el("div", { class: "card-body tight" }, rows);

const chip = (band) => el("span", { class: `chip chip-${band || "neutral"}` }, band || "—");

/* HIGH severity is a stop, not a success — it borrows the LOW-confidence chip
 * rather than the HIGH one, which would read as "all good" at a glance. */
const severityChip = (severity) =>
  severity === "HIGH" ? "chip-LOW" : severity === "MEDIUM" ? "chip-MEDIUM" : "chip-neutral";
const verdictChip = (verdict) =>
  verdict === "ROBUST" ? "chip-HIGH"
    : String(verdict).includes("SENSITIVE") ? "chip-LOW" : "chip-neutral";
const statusTag = (status) =>
  el("span", { class: `status status-${status}` }, (status || "").replace(/_/g, " ").toLowerCase());

const SENS_GLYPH = { STABLE: "=", MODERATE: "≈", SENSITIVE: "~", NOT_APPLICABLE: "·" };
const SENS_WORD = {
  STABLE: "stable",
  MODERATE: "moderate",
  SENSITIVE: "sensitive",
  NOT_APPLICABLE: "no range",
};
const sensTag = (flag) =>
  el("span", { class: `sens sens-${flag || "NOT_APPLICABLE"}`, title: `Benchmark sensitivity: ${SENS_WORD[flag] || "unknown"}` },
    el("span", { class: "glyph" }, SENS_GLYPH[flag] || "·"), SENS_WORD[flag] || "—");

/* ------------------------------------------------------- the range mark */
/* The signature device. A band from low to high, a dot at the base. A figure
 * with no range gets the dot alone — the visual claim that it does not move. */

function rangeMark(low, base, high, width = 190, labels = {}) {
  const height = 26;
  const pad = 6;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "rangemark");
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");

  const inner = width - pad * 2;
  const y = 9;
  const add = (name, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    svg.append(node);
    return node;
  };

  const hasRange = low !== null && high !== null && base !== null && high > low;
  if (!hasRange) {
    add("line", { class: "axis", x1: pad, y1: y, x2: pad + inner, y2: y });
    add("circle", { class: "base fixed", cx: pad + inner / 2, cy: y, r: 4 });
    const label = add("text", { class: "tick-label", x: pad + inner / 2, y: y + 15, "text-anchor": "middle" });
    label.textContent = base === null ? "no rand figure" : "does not move";
    svg.setAttribute("aria-label", "Fixed figure: identical across every tested assumption");
    return svg;
  }

  const span = high - low;
  const at = (value) => pad + ((value - low) / span) * inner;
  const wide = span / (base || 1) > 0.6;

  add("rect", { class: `band${wide ? " wide" : ""}`, x: pad, y: y - 5, width: inner, height: 10, rx: 2 });
  add("line", { class: "cap", x1: pad, y1: y - 6, x2: pad, y2: y + 6 });
  add("line", { class: "cap", x1: pad + inner, y1: y - 6, x2: pad + inner, y2: y + 6 });
  add("circle", { class: "base", cx: at(base), cy: y, r: 4.5 });

  /* Tick labels are the API's own strings. This file used to format them with
   * a local helper that rounded to whole billions, so a band the model
   * published as R278.56bn was drawn labelled R279bn — the same number stated
   * two ways on one screen. The browser no longer renders currency anywhere. */
  const lo = add("text", { class: "tick-label", x: pad, y: y + 15, "text-anchor": "start" });
  lo.textContent = dash(labels.low);
  const hi = add("text", { class: "tick-label", x: pad + inner, y: y + 15, "text-anchor": "end" });
  hi.textContent = dash(labels.high);
  svg.setAttribute("aria-label", `Ranges ${dash(labels.low)} to ${dash(labels.high)} around ${dash(labels.base)}`);
  return svg;
}

/* -------------------------------------------------------------- tooltip */

const tooltip = el("div", { class: "tooltip" });
document.body.append(tooltip);

function showTip(event, html) {
  tooltip.innerHTML = html;
  tooltip.classList.add("on");
  const rect = tooltip.getBoundingClientRect();
  let x = event.clientX + 14;
  let y = event.clientY + 14;
  if (x + rect.width > window.innerWidth - 12) x = event.clientX - rect.width - 14;
  if (y + rect.height > window.innerHeight - 12) y = event.clientY - rect.height - 14;
  tooltip.style.left = `${Math.max(8, x)}px`;
  tooltip.style.top = `${Math.max(8, y)}px`;
}
const hideTip = () => tooltip.classList.remove("on");

/* ------------------------------------------------------------ page: 1 */

function pillarCard(pillar) {
  const isSignal = pillar.role === "signal";
  const isCore = pillar.role === "core";
  const hasRange = pillar.range && pillar.range.low.value !== null && pillar.range.high.value !== null;

  const body = el("div", { class: "card-body" });

  body.append(
    el("div", { class: "stat-row" },
      el("div", { class: "stat" },
        el("div", { class: "k" }, isSignal ? "Signal" : pillar.denominator),
        el("div", { class: "v num num-xl" }, isSignal ? "—" : dash(pillar.addressable.display)),
        el("div", { class: "h" }, pillar.basis)),
    ));

  if (!isSignal) {
    const facts = el("div", { class: "stat-row", style: "margin-top:16px" });
    facts.append(
      el("div", { class: "stat" },
        el("div", { class: "k" }, "Observed"),
        el("div", { class: "v num num-md" }, dash(pillar.observed.display)),
        el("div", { class: "h" }, "Handled by Syn Bank")),
      el("div", { class: "stat" },
        el("div", { class: "k" }, isCore ? "Share" : "No share"),
        el("div", { class: "v num num-md" }, isCore ? dash(pillar.share.display) : "n/a"),
        el("div", { class: "h" }, isCore ? "Of addressable" : "No loan book to divide")),
      el("div", { class: "stat" },
        el("div", { class: "k" }, "Opportunity"),
        el("div", { class: "v num num-md" }, dash(pillar.opportunity.display)),
        el("div", { class: "h" }, "Not observed in our data")),
    );
    body.append(facts);

    if (isCore && pillar.share.value !== null) {
      body.append(el("div", { class: "meter", title: `Share ${pillar.share.display}` },
        el("i", { style: `width:${Math.max(0.6, pillar.share.value * 100)}%` })));
    }

    body.append(el("div", { style: "margin-top:14px" },
      el("div", { class: "eyebrow" }, hasRange ? "Range across 36 tested assumptions" : "Across 36 tested assumptions"),
      rangeMark(
        hasRange ? pillar.range.low.value : null,
        pillar.opportunity.value,
        hasRange ? pillar.range.high.value : null,
        240,
        hasRange
          ? { low: pillar.range.low.display, base: pillar.opportunity.display, high: pillar.range.high.display }
          : {},
      )));
  } else {
    body.append(el("div", { class: "callout", style: "margin-top:4px" },
      "No rand amount is estimated. Nothing in the data indicates a planned issue, disposal or acquisition, so this pillar publishes a ranked signal and a category only."));
  }

  body.append(el("div", { style: "margin-top:16px" },
    el("div", { class: "eyebrow", style: "margin-bottom:7px" }, "Confidence across 20 clients"),
    el("div", { class: "bar-row" },
      el("div", { class: "lbl" }, "Mean"),
      el("div", { class: "track" }, el("i", { style: `width:${(pillar.confidence.mean || 0) * 100}%` })),
      el("div", { class: "val" }, score2(pillar.confidence.mean))),
    el("div", { style: "display:flex;gap:8px;flex-wrap:wrap" },
      el("span", { class: "chip chip-HIGH" }, `${pct0(pillar.confidence.high_pct)} high`),
      el("span", { class: "chip chip-MEDIUM" }, `${pct0(pillar.confidence.medium_pct)} medium`),
      el("span", { class: "chip chip-LOW" }, `${pct0(pillar.confidence.low_pct)} low`))));

  if (pillar.top_clients.length) {
    const list = el("div", { style: "margin-top:16px" },
      el("div", { class: "eyebrow", style: "margin-bottom:6px" }, "Top clients"));
    for (const client of pillar.top_clients) {
      list.append(el("div", {
        class: "bar-row click",
        style: "cursor:pointer",
        onclick: () => go(`client/${client.entity_id}`),
      },
        el("div", { class: "lbl", style: "flex:1" }, client.entity_name),
        el("div", { class: "val", style: "width:auto" },
          isSignal ? score2(client.signal) : dash(client.opportunity.display)),
        chip(client.confidence_band)));
    }
    list.querySelectorAll(".bar-row").forEach((row) => {
      row.style.padding = "3px 0";
      row.addEventListener("mouseenter", () => (row.style.background = "var(--accent-wash)"));
      row.addEventListener("mouseleave", () => (row.style.background = ""));
    });
    body.append(list);
  }

  return el("div", { class: "card" },
    el("div", { class: `pillar-rule ${roleClass(pillar.role)}` }),
    el("div", { class: "card-head" },
      el("div", null,
        el("h2", null, pillar.label),
        el("div", { class: "sub" }, pillar.sublabel)),
      el("span", { class: `role-tag ${roleClass(pillar.role)}` }, roleLabel(pillar.role))),
    body);
}

async function renderPortfolio(root) {
  const data = await api("/api/portfolio");
  const core = data.pillars.filter((p) => p.role === "core");
  const rest = data.pillars.filter((p) => p.role !== "core");

  root.append(el("div", { class: "callout", style: "margin-bottom:18px" },
    el("strong", null, "Where to focus next. "),
    `The three Share of Wallet pillars measure what fraction of a client's addressable activity runs through Syn Bank. Lending and Investment Banking are opportunity signals — they carry no share. `,
    el("strong", null, "The five are never added together"),
    ": two overlap on the SWIFT channel by an amount the data cannot resolve, and all five are measured on different bases."));

  root.append(el("div", { class: "section-head" },
    el("h2", null, "Core Share of Wallet"),
    el("div", { class: "note" }, "Cash is anchored on an accounting identity. FX and trade are peer benchmarks — read them as a range.")));
  root.append(el("div", { class: "grid grid-3" }, core.map(pillarCard)));

  root.append(el("div", { class: "section-head" },
    el("h2", null, "Supporting opportunities"),
    el("div", { class: "note" }, "A financing need and a mandate signal. Neither is a share of wallet.")));
  root.append(el("div", { class: "grid grid-2" }, rest.map(pillarCard)));

  /* Focus list — the direct answer to the page's question. */
  const focusRows = data.focus.map((row) =>
    el("tr", { class: "click", onclick: () => go(`client/${row.entity_id}`) },
      el("td", null, el("strong", null, row.entity_name)),
      el("td", { class: "dim tiny" }, row.sector.replace(/_/g, " ")),
      el("td", null, row.product_label),
      el("td", { class: "r num" }, dash(row.opportunity.display)),
      el("td", null, chip(row.confidence_band)),
      el("td", null, sensTag(row.sensitivity)),
      el("td", null, statusTag(row.status))));

  const focus = card(
    "Focus list",
    "Ranked by evidence-weighted opportunity, best first",
    tableBody(dataTable(
      ["Client", "Sector", "Focus product", { label: "Opportunity", r: true },
        "Confidence", "Sensitivity", "Action"],
      focusRows)),
    el("button", { class: "btn btn-sm", onclick: () => go("heatmap") }, "Open heatmap"));

  root.append(el("div", { class: "section-head" }, el("h2", null, "Portfolio insight")));
  root.append(focus);

  /* Concentration + risk of misquoting. */
  const warn = data.concentration.find((row) => row.metric === "concentration_warning");
  const grid = el("div", { class: "grid grid-2", style: "margin-top:14px" });

  const concentrationBody = el("div", { class: "card-body" },
    data.concentration
      .filter((row) => row.metric === "clients_with_this_primary")
      .map((row) =>
        el("div", { class: "bar-row" },
          el("div", { class: "lbl" }, row.product_label),
          el("div", { class: "track" },
            el("i", { style: `width:${(row.value_numeric / data.clients) * 100}%` })),
          el("div", { class: "val" }, `${row.value_numeric}`))),
    warn ? el("div", { class: "callout warn", style: "margin-top:10px" }, warn.note) : null);

  grid.append(card("Where the focus lands", "Primary product across the 20 clients",
    concentrationBody));

  const riskyRows = data.low_confidence_high_value.slice(0, 6).map((row) =>
    el("tr", { class: "click", onclick: () => go(`client/${row.entity_id}`) },
      el("td", null, row.entity_name),
      el("td", { class: "dim tiny" }, row.product.replace(/_/g, " ")),
      el("td", { class: "r num" }, row.value_text)));

  grid.append(card("Large but weakly evidenced",
    "Biggest rand figures on LOW confidence — capped at monitor",
    tableBody(dataTable(["Client", "Pillar", { label: "Opportunity", r: true }], riskyRows))));
  root.append(grid);

  /* Multiple simultaneous opportunities — breadth, counted not summed. */
  if (data.multiple.length) {
    const rows = data.multiple.map((row) =>
      el("tr", { class: "click", onclick: () => go(`client/${row.entity_id}`) },
        el("td", null, row.entity_name),
        el("td", { class: "dim tiny" }, (row.sector || "").replace(/_/g, " ")),
        el("td", { class: "r num" }, row.value_numeric),
        el("td", { class: "dim tiny" }, (row.value_text || "").replace(/_/g, " "))));
    const node = card("Clients with more than one live opportunity",
      "Breadth of coverage, counted — the pillars are not additive",
      tableBody(dataTable(
        ["Client", "Sector", { label: "Live pillars", r: true }, "Which"], rows)));
    node.style.marginTop = "14px";
    root.append(node);
  }
}

/* ------------------------------------------------------------ page: 2 */

function scoreStep(score) {
  if (score === null || score === undefined) return 0;
  if (score >= 0.75) return 5;
  if (score >= 0.6) return 4;
  if (score >= 0.45) return 3;
  if (score >= 0.3) return 2;
  return 1;
}

async function renderHeatmap(root) {
  const data = await api("/api/heatmap");
  const f = state.heatFilters;

  const bar = el("div", { class: "card", style: "margin-bottom:14px" },
    el("div", { class: "card-body", style: "padding:12px 16px" },
      el("div", { class: "filters" },
        el("label", null, "Sector"),
        el("select", { onchange: (e) => { f.sector = e.target.value; rerender(); } },
          el("option", { value: "" }, "All sectors"),
          data.sectors.map((s) => el("option", { value: s, selected: f.sector === s }, s.replace(/_/g, " ")))),
        el("label", null, "Pillar"),
        el("select", { onchange: (e) => { f.product = e.target.value; rerender(); } },
          el("option", { value: "" }, "All pillars"),
          data.products.map((p) => el("option", { value: p.product, selected: f.product === p.product }, p.label))),
        el("label", null, "Confidence"),
        el("select", { onchange: (e) => { f.band = e.target.value; rerender(); } },
          el("option", { value: "" }, "Any"),
          data.bands.map((b) => el("option", { value: b, selected: f.band === b }, b))),
        el("label", null, "Status"),
        el("select", { onchange: (e) => { f.status = e.target.value; rerender(); } },
          el("option", { value: "" }, "Any"),
          data.statuses.map((s) => el("option", { value: s, selected: f.status === s }, s.replace(/_/g, " ")))),
        el("button", { class: "btn btn-sm", onclick: () => { state.heatFilters = { sector: "", product: "", band: "", status: "" }; rerender(); } }, "Reset"))));
  root.append(bar);

  const products = data.products.filter((p) => !f.product || p.product === f.product);
  const byKey = new Map(data.cells.map((cell) => [`${cell.entity_id}|${cell.product}`, cell]));

  const matches = (cell) =>
    (!f.band || cell.confidence_band === f.band) &&
    (!f.status || cell.opportunity_status === f.status);

  const clients = data.clients.filter((client) => {
    if (f.sector && client.sector !== f.sector) return false;
    if (!f.band && !f.status) return true;
    return products.some((p) => {
      const cell = byKey.get(`${client.entity_id}|${p.product}`);
      return cell && matches(cell);
    });
  });

  const head = el("tr", null, el("th", { class: "row" }, ""),
    products.map((p) => el("th", { class: "col", title: p.denominator },
      p.label,
      el("div", { style: "margin-top:5px" }, el("span", { class: `role-tag ${roleClass(p.role)}` }, "")))));

  const body = el("tbody");
  for (const client of clients) {
    const row = el("tr", null,
      el("th", { class: "row" },
        el("button", {
          class: "crumb", style: "background:none;border:0;padding:0;cursor:pointer;text-align:left",
          onclick: () => go(`client/${client.entity_id}`),
        }, client.entity_name),
        el("span", { class: "sec" }, client.sector.replace(/_/g, " "))));
    for (const p of products) {
      const cell = byKey.get(`${client.entity_id}|${p.product}`);
      if (!cell || !matches(cell)) {
        row.append(el("td", null, el("div", { class: "cell empty" },
          el("div", { class: "sc" }, "—"),
          el("div", { class: "meta tiny" }, cell ? "filtered" : "no data"))));
        continue;
      }
      const band = cell.confidence_band || "LOW";
      const node = el("div", {
        class: `cell c-${band} s-${scoreStep(cell.commercial_opportunity_score)}`,
        tabindex: "0",
        role: "button",
        "aria-label": `${client.entity_name}, ${p.label}: score ${score2(cell.commercial_opportunity_score)}, ${band} confidence`,
        onclick: () => go(`client/${client.entity_id}`),
        onkeydown: (e) => { if (e.key === "Enter") go(`client/${client.entity_id}`); },
        onmousemove: (e) => showTip(e, cellTip(cell)),
        onmouseleave: hideTip,
      },
        el("div", { class: "sc" }, score2(cell.commercial_opportunity_score)),
        el("div", { class: "meta" },
          el("span", { class: "pips", title: `${band} confidence` },
            el("i", { class: "on" }),
            el("i", { class: band === "HIGH" || band === "MEDIUM" ? "on" : "" }),
            el("i", { class: band === "HIGH" ? "on" : "" })),
          el("span", { class: "warn" },
            (cell.sensitivity_flag === "SENSITIVE" ? "~" : "") +
            (cell.high_severity_diagnostic ? "!" : ""))));
      row.append(el("td", null, node));
    }
    body.append(row);
  }

  root.append(el("div", { class: "card" },
    el("div", { class: "card-head" },
      el("div", null,
        el("h2", null, "Opportunity heatmap"),
        el("div", { class: "sub" }, `${clients.length} clients × ${products.length} pillars — fill is the commercial opportunity score, fill style is confidence`)),
      el("div", { class: "legend" },
        el("span", null, "Score ",
          el("span", { class: "swatches" },
            el("i", { style: "background:var(--seq-200)" }),
            el("i", { style: "background:var(--seq-300)" }),
            el("i", { style: "background:var(--seq-400)" }),
            el("i", { style: "background:var(--seq-500)" }),
            el("i", { style: "background:var(--seq-600)" }))),
        el("span", null, "● ● ● high  ·  ● ● medium  ·  ● low"),
        el("span", null, "~ benchmark-sensitive  ·  ! review flag"))),
    el("div", { class: "card-body" },
      clients.length
        ? el("div", { class: "heat-wrap" }, el("table", { class: "heat" }, el("thead", null, head), body))
        : el("div", { class: "empty-state" }, "No client matches these filters. Reset to see the full book."))));

  root.append(el("div", { class: "callout", style: "margin-top:14px" },
    el("strong", null, "Reading the grid. "),
    "A solid dark cell is a well-evidenced opportunity. A pale outlined cell is the same score on LOW confidence — the model can size it but cannot stand behind it, so it never looks like the solid one. Fill carries magnitude; fill style carries evidence. The two are deliberately different channels."));
}

function cellTip(cell) {
  const rows = [
    ["Commercial score", score2(cell.commercial_opportunity_score)],
    ["Confidence", `${score2(cell.confidence)} (${cell.confidence_band})`],
    ["Headroom", cell.headroom_fraction === null ? "—" : pct0(cell.headroom_fraction)],
    ["Sensitivity", SENS_WORD[cell.sensitivity_flag] || "—"],
  ];
  if (cell.opportunity_zar !== null) rows.unshift(["Opportunity", dash(cell.opportunity_zar_display)]);
  if (cell.share !== null) rows.push(["Share", pct1(cell.share)]);
  return `<div class="t-k">${esc(cell.entity_name)} · ${esc(cell.product_label)}</div>` +
    rows.map(([k, v]) => `<div class="t-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("") +
    `<div style="margin-top:6px;opacity:.75">${esc(cell.status_action || "")}</div>`;
}

/* ------------------------------------------------------------ page: 3 */

async function renderClientList(root) {
  const clients = await api("/api/clients");

  const rows = clients.map((row) =>
    el("tr", { class: "click", onclick: () => go(`client/${row.entity_id}`) },
      el("td", null,
        el("strong", null, row.entity_name), " ",
        el("span", { class: "dim tiny" }, row.entity_id)),
      el("td", { class: "dim tiny" }, (row.sector || "").replace(/_/g, " ")),
      el("td", null, row.primary_opportunity || "—"),
      el("td", { class: "r num" },
        row.primary_opportunity_zar === null ? "signal" : dash(row.primary_opportunity_zar_display)),
      el("td", null, chip(row.confidence_band)),
      el("td", null, sensTag(row.sensitivity)),
      el("td", { class: "tiny" }, row.next_action)));

  const head = el("tr", null,
    el("th", null, "Client"), el("th", null, "Sector"), el("th", null, "Focus product"),
    el("th", { class: "r" }, "Opportunity"), el("th", null, "Confidence"),
    el("th", null, "Sensitivity"), el("th", null, "Next action"));

  root.append(el("div", { class: "card" },
    el("div", { class: "card-head" },
      el("div", null,
        el("h2", null, "Client book"),
        el("div", { class: "sub" }, "Ordered by evidence-weighted opportunity"))),
    el("div", { class: "card-body tight" },
      el("table", { class: "data" },
        el("thead", null, head),
        el("tbody", null, rows)))));
}

async function renderClient(root, entityId) {
  let data;
  try {
    data = await api(`/api/clients/${entityId}`);
  } catch (error) {
    root.append(el("div", { class: "card" }, el("div", { class: "empty-state" },
      el("p", null, error.message),
      el("button", { class: "btn", onclick: () => go("clients") }, "Back to the client book"))));
    return;
  }
  state.copilotClient = data.entity_id;

  const core = data.pillars.filter((p) => p.role === "core");
  const primary = data.pillars.find((p) => p.selection_slot === "primary");

  /* The sticky topbar carries the client, so scrolling a long page never loses
   * track of whose numbers these are. That also keeps one h1 per page. */
  document.getElementById("page-title").textContent = data.entity_name;
  document.getElementById("page-lede").textContent =
    `${(data.sector || "").replace(/_/g, " ")} · fiscal year ${data.fy_label} · ends ${data.fiscal_year_end}`;

  root.append(el("div", { class: "crumb", style: "margin-bottom:10px" },
    el("button", { onclick: () => go("clients") }, "Client book"), "›", data.entity_name));

  const headline = el("div", { style: "display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap;align-items:center" },
    el("div", { style: "display:flex;gap:9px;align-items:center;flex-wrap:wrap" },
      el("span", { class: "eyebrow" }, "Focus"),
      data.primary.label ? el("strong", null, data.primary.label) : null,
      data.primary.status ? statusTag(data.primary.status) : null,
      chip(data.primary.confidence_band)),
    el("button", {
      class: "btn btn-primary btn-sm",
      onclick: () => openCopilot(`Why is ${data.entity_name} a priority?`),
    }, "Ask why"));

  root.append(el("div", { class: "card" },
    el("div", { class: `pillar-rule ${primary ? roleClass(primary.role) : "core"}` }),
    el("div", { class: "card-body" },
      headline,
      el("div", { class: "callout", style: "margin-top:13px" }, data.summary))));

  /* Relationship snapshot — observed only. */
  root.append(el("div", { class: "section-head" },
    el("h2", null, "Relationship snapshot"),
    el("div", { class: "note" }, "What Syn Bank actually handled this fiscal year. Measured, not estimated.")));
  root.append(el("div", { class: "card" }, el("div", { class: "card-body" },
    el("div", { class: "gauge-row" }, core.map((p) =>
      el("div", { class: "gauge" },
        el("div", { class: "k" }, p.label),
        el("div", { class: "v num num-lg" }, dash(p.observed.display)),
        el("div", { class: "tiny dim" }, `of ${dash(p.addressable.display)} addressable`),
        el("div", { class: "meter thin" }, el("i", { style: `width:${Math.max(0.6, (p.share.value || 0) * 100)}%` })),
        el("div", { class: "tiny dim", style: "margin-top:4px" }, `${dash(p.share.display)} share`)))))));

  /* Share of wallet — the three CORE pillars only. */
  root.append(el("div", { class: "section-head" },
    el("h2", null, "Share of wallet"),
    el("div", { class: "note" }, "Only the three core pillars carry a share. Lending and investment banking are shown below as opportunities, not shares.")));
  root.append(el("div", { class: "grid grid-3" }, core.map((p) =>
    el("div", { class: "card" },
      el("div", { class: "pillar-rule core" }),
      el("div", { class: "card-body" },
        el("div", { class: "eyebrow" }, p.label),
        el("div", { class: "num num-xl", style: "margin:6px 0 2px" }, dash(p.share.display)),
        el("div", { class: "tiny dim" }, p.denominator),
        el("div", { class: "meter" }, el("i", { style: `width:${Math.max(0.6, (p.share.value || 0) * 100)}%` })),
        el("div", { style: "display:flex;gap:8px;margin-top:10px;flex-wrap:wrap" }, chip(p.confidence_band), sensTag(p.sensitivity_flag)),
        p.range && p.range.estimate_low.value !== null
          ? el("div", { style: "margin-top:10px" },
              el("div", { class: "eyebrow" }, "Addressable range"),
              rangeMark(p.range.estimate_low.value, p.range.estimate_base.value, p.range.estimate_high.value, 230,
                { low: p.range.estimate_low.display, base: p.range.estimate_base.display, high: p.range.estimate_high.display }))
          : el("div", { style: "margin-top:10px" },
              el("div", { class: "eyebrow" }, "Addressable range"),
              rangeMark(null, p.addressable.value, null, 230)))))));

  /* Opportunity table across all five. */
  root.append(el("div", { class: "section-head" }, el("h2", null, "Opportunities")));
  const oppRows = data.pillars.map((p) =>
    el("tr", null,
      el("td", null,
        el("span", { class: `role-tag ${roleClass(p.role)}`, style: "margin-right:7px" }, ""),
        p.product_label,
        p.selection_slot
          ? el("span", { class: "chip chip-accent", style: "margin-left:7px" },
              p.selection_slot.replace(/_/g, " "))
          : null),
      el("td", { class: "r num" },
        p.role === "signal" ? "signal only" : dash(p.opportunity.display)),
      el("td", null, chip(p.confidence_band)),
      el("td", null, sensTag(p.sensitivity_flag)),
      el("td", { class: "r num" }, dash(p.commercial_rank)),
      el("td", { class: "tiny" }, p.status_action)));

  root.append(el("div", { class: "card" },
    tableBody(dataTable(
      ["Product", { label: "Opportunity", r: true }, "Confidence", "Sensitivity",
        { label: "Rank", r: true }, "Recommended action"],
      oppRows))));

  /* Financial signals behind the primary and secondary pillars only. */
  const withSignals = data.pillars.filter((p) => p.signals.length && (p.selection_slot || p.role === "core"));
  if (withSignals.length) {
    root.append(el("div", { class: "section-head" },
      el("h2", null, "Financial signals"),
      el("div", { class: "note" }, "The disclosed figures that drive each estimate — not the full 19-field store.")));
    const wrap = el("div", { class: "grid grid-2" });
    for (const p of withSignals) {
      wrap.append(el("div", { class: "card" },
        el("div", { class: "card-head" }, el("div", null,
          el("h3", null, p.product_label),
          el("div", { class: "sub tiny" }, p.basis))),
        el("div", { class: "card-body" },
          el("div", { class: "sig-grid" }, p.signals.map((s) =>
            el("div", { class: "sig" },
              el("div", { class: "k" }, s.label),
              el("div", { class: "v" }, dash(s.display)),
              el("div", { class: "w" }, s.why)))))));
    }
    root.append(wrap);
  }

  /* Why the primary opportunity exists. */
  if (primary && primary.explanation) {
    root.append(el("div", { class: "section-head" }, el("h2", null, "Why this is the focus")));
    root.append(el("div", { class: "card" }, el("div", { class: "card-body" },
      el("p", { style: "margin-top:0" }, primary.explanation.why),
      el("div", { class: "callout warn", style: "margin-top:10px" },
        el("strong", null, "Limitation. "), primary.explanation.limitation),
      el("div", { style: "margin-top:12px" },
        el("div", { class: "eyebrow" }, "Next action"),
        el("div", { style: "margin-top:4px" }, primary.explanation.next_action)))));
  }

  /* Banker questions. */
  if (data.questions.length) {
    root.append(el("div", { class: "section-head" },
      el("h2", null, "Questions for the client"),
      el("div", { class: "note" }, "Generated from this client's own figures, not from a template.")));
    root.append(el("div", { class: "card" }, el("div", { class: "card-body" },
      data.questions.map((q, index) =>
        el("div", { style: index ? "margin-top:14px;padding-top:14px;border-top:1px solid var(--rule)" : "" },
          el("div", { style: "display:flex;gap:9px;align-items:baseline" },
            el("span", { class: "num dim", style: "font-size:12px" }, `${index + 1}`),
            el("div", null,
              el("div", { style: "font-weight:560" }, q.question),
              el("div", { class: "tiny dim", style: "margin-top:4px" }, q.rationale))))))));
  }

  if (data.diagnostics.length) {
    root.append(el("div", { class: "section-head" }, el("h2", null, "Model diagnostics")));
    const diagRows = data.diagnostics.map((d) =>
      el("tr", null,
        el("td", null, el("span", { class: `chip ${severityChip(d.severity)}` }, d.severity)),
        el("td", { class: "tiny dim" }, (d.product || "").replace(/_/g, " ")),
        el("td", { class: "tiny" },
          el("strong", null, d.diagnostic.replace(/_/g, " ")), " — ", d.detail)));
    root.append(el("div", { class: "card" },
      tableBody(dataTable(["Severity", "Pillar", "Finding"], diagRows))));
  }
}

/* ------------------------------------------------------------ page: 4 */

async function renderSensitivity(root) {
  const data = await api("/api/sensitivity");

  root.append(el("div", { class: "callout", style: "margin-bottom:18px" },
    el("strong", null, "What the model is sure of, and what it is not. "),
    "Every rand estimate was rebuilt under 36 model configurations. Cash management does not move at all. FX and trade move by several times, because no disclosure states either activity's true size — so the benchmark choice is the denominator."));

  const flagChip = (flag) =>
    flag === "STABLE" ? "chip-HIGH"
      : flag === "SENSITIVE" ? "chip-LOW"
      : flag === "MODERATE" ? "chip-MEDIUM" : "chip-neutral";

  const trustCard = (p) => {
    const head = el("div", { class: "card-head" },
      el("div", null,
        el("h2", null, p.label),
        el("div", { class: "sub" }, p.headline)),
      el("span", { class: `chip ${verdictChip(p.verdict)}` }, p.verdict.replace(/_/g, " ")));

    const flags = el("div", { style: "display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px" },
      Object.entries(p.flags).map(([flag, count]) =>
        el("span", { class: `chip ${flagChip(flag)}` },
          `${count} ${SENS_WORD[flag] || flag.toLowerCase()}`)));

    const bands = el("div", { style: "display:flex;gap:7px;flex-wrap:wrap" },
      el("span", { class: "chip chip-HIGH" }, `${pct0(p.pct_high)} high`),
      el("span", { class: "chip chip-MEDIUM" }, `${pct0(p.pct_medium)} medium`),
      el("span", { class: "chip chip-LOW" }, `${pct0(p.pct_low)} low`));

    const body = el("div", { class: "card-body" },
      el("p", { style: "margin:0 0 12px;font-size:13px;line-height:1.6" }, p.detail),
      el("div", { class: "eyebrow", style: "margin-bottom:6px" }, "Client estimates by sensitivity"),
      flags,
      el("div", { class: "bar-row" },
        el("div", { class: "lbl" }, "Mean confidence"),
        el("div", { class: "track" },
          el("i", { style: `width:${(p.mean_confidence || 0) * 100}%` })),
        el("div", { class: "val" }, score2(p.mean_confidence))),
      bands);

    return el("div", { class: "card" },
      el("div", { class: `pillar-rule ${roleClass(p.role)}` }),
      head,
      body);
  };

  root.append(el("div", { class: "grid grid-2" }, data.by_product.map(trustCard)));

  root.append(el("div", { class: "section-head" },
    el("h2", null, "The widest ranges in the book"),
    el("div", { class: "note" }, "Where a single quoted figure would be least defensible.")));
  const widestRows = data.widest.map((row) =>
    el("tr", { class: "click", onclick: () => go(`client/${row.entity_id}`) },
      el("td", null, row.entity_name),
      el("td", { class: "dim tiny" }, row.product.replace(/_/g, " ")),
      el("td", null,
        el("div", { style: "display:flex;align-items:center;gap:10px" },
          el("span", { class: "num tiny" }, `${row.low.display} – ${row.high.display}`),
          rangeMark(row.low.value, row.base.value, row.high.value, 140,
            { low: row.low.display, base: row.base.display, high: row.high.display }))),
      el("td", { class: "r num" }, pct0(row.range_pct)),
      el("td", null, sensTag(row.rank_stability))));

  root.append(el("div", { class: "card" },
    tableBody(dataTable(
      ["Client", "Pillar", "Range across 36 runs", { label: "Spread", r: true }, "Ranking"],
      widestRows))));

  if (data.robustness.length) {
    root.append(el("div", { class: "section-head" }, el("h2", null, "Verdict by pillar, from the 36-run sweep")));
    const robustRows = data.robustness
      .filter((row) => row.product !== "ALL")
      .map((row) =>
        el("tr", null,
          el("td", null, row.product_label),
          el("td", null,
            el("span", { class: `chip ${verdictChip(row.verdict)}` },
              row.verdict.replace(/_/g, " "))),
          el("td", { class: "r num" },
            row.min_spearman_rank_in_product === null
              ? "—" : row.min_spearman_rank_in_product.toFixed(3)),
          el("td", { class: "r num" },
            row.max_abs_total_gap_drift === null
              ? "no rand figure" : pct0(row.max_abs_total_gap_drift)),
          el("td", { class: "tiny" }, row.note)));

    root.append(el("div", { class: "card" },
      tableBody(dataTable(
        ["Pillar", "Verdict", { label: "Worst rank correlation", r: true },
          { label: "Worst drift", r: true }, "Reading"],
        robustRows))));
  }

  root.append(el("div", { class: "section-head" }, el("h2", null, "How the benchmarks are built")));
  root.append(el("div", { class: "grid grid-2" },
    Object.entries(data.methodology).map(([key, text]) =>
      el("div", { class: "card" }, el("div", { class: "card-body" },
        el("div", { class: "eyebrow", style: "margin-bottom:6px" }, key.replace(/_/g, " ")),
        el("p", { style: "margin:0;font-size:13px;line-height:1.6" }, text))))));

  root.append(el("div", { class: "section-head" },
    el("h2", null, "Open model diagnostics"),
    el("div", { class: "note" }, `${data.diagnostic_counts.HIGH || 0} high · ${data.diagnostic_counts.MEDIUM || 0} medium · ${data.diagnostic_counts.INFO || 0} info`)));
  const trustDiagRows = data.diagnostics.slice(0, 14).map((d) =>
    el("tr", null,
      el("td", null, el("span", { class: `chip ${severityChip(d.severity)}` }, d.severity)),
      el("td", { class: "tiny dim" },
        [d.entity_name, (d.product || "").replace(/_/g, " ")].filter(Boolean).join(" · ")
          || "portfolio"),
      el("td", { class: "tiny" },
        el("strong", null, d.diagnostic.replace(/_/g, " ")), " — ", d.detail)));

  root.append(el("div", { class: "card" },
    tableBody(dataTable(["Severity", "Scope", "Finding"], trustDiagRows))));
}

/* ------------------------------------------------------------ page: 5 */

async function renderProduct(root, key) {
  const products = await api("/api/products");
  const active = key || products[0].key;
  const data = await api(`/api/products/${active}`);

  root.append(el("div", { class: "seg", style: "margin-bottom:16px" },
    products.map((p) =>
      el("button", { class: p.key === active ? "on" : "", onclick: () => go(`product/${p.key}`) }, p.label))));

  const isSignal = data.role === "signal";
  root.append(el("div", { class: "card" },
    el("div", { class: `pillar-rule ${roleClass(data.role)}` }),
    el("div", { class: "card-head" },
      el("div", null, el("h2", null, data.label), el("div", { class: "sub" }, data.basis)),
      el("span", { class: `role-tag ${roleClass(data.role)}` }, roleLabel(data.role))),
    el("div", { class: "card-body" },
      el("div", { class: "stat-row" },
        !isSignal ? el("div", { class: "stat" },
          el("div", { class: "k" }, data.denominator),
          el("div", { class: "v num num-xl" }, dash(data.addressable.display))) : null,
        !isSignal ? el("div", { class: "stat" },
          el("div", { class: "k" }, "Observed"),
          el("div", { class: "v num num-lg" }, dash(data.observed.display))) : null,
        data.role === "core" ? el("div", { class: "stat" },
          el("div", { class: "k" }, "Portfolio share"),
          el("div", { class: "v num num-lg" }, dash(data.share.display))) : null,
        !isSignal ? el("div", { class: "stat" },
          el("div", { class: "k" }, "Opportunity"),
          el("div", { class: "v num num-lg" }, dash(data.opportunity.display))) : null,
        isSignal ? el("div", { class: "stat" },
          el("div", { class: "k" }, "Rand figure"),
          el("div", { class: "v num num-lg" }, "none"),
          el("div", { class: "h" }, "Signal only, by design")) : null))));

  /* Descriptive observed breakdowns. */
  const d = data.descriptive || {};
  const panels = [];
  const barPanel = (title, rows, note) => {
    if (!rows || !rows.length) return null;
    const max = Math.max(...rows.map((r) => Math.abs(r.value || 0)), 1);
    return el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", null,
        el("h3", null, title),
        note ? el("div", { class: "sub tiny" }, note) : null)),
      el("div", { class: "card-body" }, rows.map((r) =>
        el("div", { class: "bar-row" },
          el("div", { class: "lbl" }, r.label),
          el("div", { class: "track" }, el("i", { style: `width:${Math.max(1, (Math.abs(r.value || 0) / max) * 100)}%` })),
          el("div", { class: "val" }, r.display || r.value)))));
  };

  if (d.legs) panels.push(barPanel("In-scope legs", d.legs, "Observed, fiscal year"));
  if (d.excluded) panels.push(el("div", { class: "card" },
    el("div", { class: "card-head" }, el("div", null,
      el("h3", null, "Deliberately outside the denominator"),
      el("div", { class: "sub tiny" }, "Observed activity the model does not count, and why"))),
    el("div", { class: "card-body" }, d.excluded.map((r) =>
      el("div", { style: "margin-bottom:10px" },
        el("div", { style: "display:flex;justify-content:space-between;gap:12px" },
          el("span", { style: "font-weight:560;font-size:13px" }, r.label),
          el("span", { class: "num tiny" }, r.display)),
        el("div", { class: "tiny dim" }, r.why))))));
  if (d.currency_pairs) panels.push(barPanel("Currency pairs", d.currency_pairs, "Observed cross-border volume"));
  if (d.direction) panels.push(barPanel("Direction", d.direction, "Observed"));
  if (d.countries && d.countries.length) panels.push(barPanel("Top counterparty countries", d.countries, "Observed"));
  if (d.instruments) panels.push(barPanel("Instrument types", d.instruments, "Observed issuance"));
  if (d.components) panels.push(barPanel("Financing components", d.components, "Disclosed balance-sheet drivers"));
  if (d.categories) panels.push(barPanel("Mandate categories", d.categories.map((c) => ({ label: c.label.replace(/_/g, " "), value: c.value, display: `${c.value} clients` })), "Clients per category"));

  if (panels.length) {
    root.append(el("div", { class: "section-head" },
      el("h2", null, "Observed detail"),
      el("div", { class: "note" }, "Measured activity and disclosed figures. Nothing here is an estimate.")));
    const kept = panels.filter(Boolean);
    // One panel in a two-column grid leaves a dead column; let it run full width.
    root.append(el("div", { class: kept.length > 1 ? "grid grid-2" : "grid" }, kept));
  }

  root.append(el("div", { class: "section-head" },
    el("h2", null, "Clients"),
    el("div", { class: "note" }, "Ranked by evidence-weighted opportunity within this pillar.")));
  const headers = ["Client", "Sector"];
  if (!isSignal) headers.push({ label: "Observed", r: true }, { label: "Addressable", r: true });
  if (data.role === "core") headers.push({ label: "Share", r: true });
  if (!isSignal) headers.push({ label: "Opportunity", r: true }, "Range");
  headers.push("Confidence", "Status");

  const clientRows = data.clients.map((row) => {
    const cells = [
      el("td", null, el("strong", null, row.entity_name)),
      el("td", { class: "dim tiny" }, (row.sector || "").replace(/_/g, " ")),
    ];
    if (!isSignal) {
      cells.push(el("td", { class: "r num" }, dash(row.observed.display)));
      cells.push(el("td", { class: "r num" }, dash(row.addressable.display)));
    }
    if (data.role === "core") cells.push(el("td", { class: "r num" }, dash(row.share.display)));
    if (!isSignal) {
      cells.push(el("td", { class: "r num" }, dash(row.opportunity.display)));
      cells.push(el("td", null,
        rangeMark(row.low.value, row.addressable.value, row.high.value, 120,
          { low: row.low.display, base: row.addressable.display, high: row.high.display })));
    }
    cells.push(el("td", null, chip(row.confidence_band)));
    cells.push(el("td", null, statusTag(row.status)));
    return el("tr", { class: "click", onclick: () => go(`client/${row.entity_id}`) }, cells);
  });

  root.append(el("div", { class: "card" }, tableBody(dataTable(headers, clientRows))));

  if (isSignal && d.signals) {
    root.append(el("div", { class: "section-head" }, el("h2", null, "Signal ranking")));
    root.append(el("div", { class: "card" }, el("div", { class: "card-body" },
      d.signals.map((s) =>
        el("div", { class: "bar-row click", style: "cursor:pointer", onclick: () => go(`client/${s.entity_id}`) },
          el("div", { class: "lbl" }, s.entity_name),
          el("div", { class: "track" }, el("i", { style: `width:${(s.signal || 0) * 100}%` })),
          el("div", { class: "val" }, score2(s.signal)),
          el("span", { class: "chip chip-neutral", style: "margin-left:8px" }, (s.category || "").replace(/_/g, " ")))))));
  }
}

/* ------------------------------------------------------------- copilot */

const copilotPanel = () => document.getElementById("copilot");
const copilotBody = () => document.getElementById("copilot-body");

function openCopilot(question) {
  state.copilotOpen = true;
  copilotPanel().classList.add("open");
  document.getElementById("scrim").classList.add("on");
  if (question) {
    document.getElementById("copilot-input").value = question;
    askCopilot(question);
  } else {
    document.getElementById("copilot-input").focus();
  }
}

function closeCopilot() {
  state.copilotOpen = false;
  copilotPanel().classList.remove("open");
  document.getElementById("scrim").classList.remove("on");
}

/* A deliberately small markdown subset: headings, bold, code, lists, rules.
 * The copilot's answers are generated from templates that only use these. */
function renderMarkdown(text) {
  const lines = String(text).split("\n");
  const out = [];
  let list = null;
  const flush = () => { if (list) { out.push(`</${list}>`); list = null; } };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) { flush(); continue; }
    if (/^---+$/.test(line.trim())) { flush(); out.push("<hr>"); continue; }
    const heading = line.match(/^(#{2,4})\s+(.*)$/);
    if (heading) {
      flush();
      out.push(`<h${heading[1].length === 2 ? 2 : 3}>${inline(heading[2])}</h${heading[1].length === 2 ? 2 : 3}>`);
      continue;
    }
    const ordered = line.match(/^\s*(\d+)\.\s+(.*)$/);
    if (ordered) {
      if (list !== "ol") { flush(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(ordered[2])}</li>`);
      continue;
    }
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      if (list !== "ul") { flush(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    flush();
    out.push(`<p>${inline(line)}</p>`);
  }
  flush();
  return out.join("");
}

function inline(text) {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|\s)\*([^*]+)\*/g, "$1<em>$2</em>");
}

async function askCopilot(question) {
  if (state.copilotBusy || !question.trim()) return;
  state.copilotBusy = true;
  const body = copilotBody();
  const placeholder = document.getElementById("copilot-placeholder");
  if (placeholder) placeholder.remove();

  const block = el("div", { class: "msg" },
    el("div", { class: "q" }, question),
    el("div", { class: "a" }, el("span", { class: "thinking" }, el("i"), el("i"), el("i"), " working through the model outputs")));
  body.append(block);
  body.scrollTop = body.scrollHeight;

  try {
    const result = await post("/api/copilot/ask", { question });
    const answer = block.querySelector(".a");
    answer.innerHTML = "";
    if (result.notice) {
      const kind = result.mode === "llm" ? "live"
        : result.mode === "fallback_validation_failed" ? "reject" : "demo";
      answer.append(el("div", { class: `notice notice-${kind}`, html: inline(result.notice) }));
    }
    answer.append(el("div", { html: renderMarkdown(result.answer) }));
    const meta = [result.intent.replace(/_/g, " ")];
    if (result.entity_ids.length) meta.push(result.entity_ids.join(", "));
    if (result.latency_seconds) meta.push(`${result.latency_seconds.toFixed(1)}s`);
    answer.append(el("div", { class: "tiny dim", style: "margin-top:10px" }, meta.join(" · ")));
    if (result.entity_ids.length === 1) {
      answer.append(el("button", {
        class: "btn btn-sm", style: "margin-top:10px",
        onclick: () => { go(`client/${result.entity_ids[0]}`); closeCopilot(); },
      }, "Open client page"));
    }
  } catch (error) {
    block.querySelector(".a").innerHTML =
      `<div class="notice notice-reject">${esc(error.message)}</div>`;
  } finally {
    state.copilotBusy = false;
    body.scrollTop = body.scrollHeight;
    await refreshSuggestions();
  }
}

/* Suggestions live in the footer, not in the greeting: a banker who has asked
 * one question still wants the next one offered, and burying them in a
 * placeholder meant they disappeared for the rest of the session. */
async function refreshSuggestions() {
  const holder = document.getElementById("copilot-suggest");
  if (!holder) return;
  const path = state.copilotClient
    ? `/api/copilot/examples?client=${state.copilotClient}`
    : "/api/copilot/examples";
  const data = await api(path);
  holder.innerHTML = "";
  holder.append(el("div", { class: "eyebrow", style: "margin-bottom:4px" }, "Try"));
  for (const question of data.questions.slice(0, 3)) {
    holder.append(el("button", {
      onclick: () => { document.getElementById("copilot-input").value = ""; askCopilot(question); },
    }, question));
  }
}

/* --------------------------------------------------------------- router */

const PAGES = [
  { id: "portfolio", label: "Portfolio", title: "Portfolio overview", lede: "Where should a banker focus next?" },
  { id: "heatmap", label: "Heatmap", title: "Opportunity heatmap", lede: "Every client against every pillar, weighted by evidence" },
  { id: "clients", label: "Clients", title: "Client book", lede: "Drill into one relationship" },
  { id: "trust", label: "Model trust", title: "Sensitivity & model trust", lede: "What the model is sure of, and what it is not" },
  { id: "product", label: "Products", title: "Product analysis", lede: "One pillar at a time" },
];

function parseRoute() {
  const hash = (location.hash || "#portfolio").slice(1);
  const [page, arg] = hash.split("/");
  return { page: page || "portfolio", arg: arg || null };
}

function go(path) {
  location.hash = `#${path}`;
}

async function rerender() {
  const route = parseRoute();
  state.route = route;
  if (route.page !== "client") state.copilotClient = null;

  document.querySelectorAll(".rail-link").forEach((link) => {
    const target = link.dataset.page;
    const on = target === route.page || (target === "clients" && route.page === "client");
    link.classList.toggle("active", on);
  });

  const meta = PAGES.find((p) => p.id === route.page)
    || (route.page === "client" ? PAGES.find((p) => p.id === "clients") : PAGES[0]);
  document.getElementById("page-title").textContent = meta.title;
  document.getElementById("page-lede").textContent = meta.lede;

  const root = document.getElementById("page");
  root.innerHTML = "";
  const spinner = el("div", { class: "spinner" }, "Loading…");
  root.append(spinner);

  try {
    const fresh = el("div");
    switch (route.page) {
      case "heatmap": await renderHeatmap(fresh); break;
      case "clients": await renderClientList(fresh); break;
      case "client": await renderClient(fresh, route.arg); break;
      case "trust": await renderSensitivity(fresh); break;
      case "product": await renderProduct(fresh, route.arg); break;
      default: await renderPortfolio(fresh); break;
    }
    root.innerHTML = "";
    while (fresh.firstChild) root.append(fresh.firstChild);
  } catch (error) {
    root.innerHTML = "";
    root.append(el("div", { class: "card" }, el("div", { class: "empty-state" },
      el("p", null, el("strong", null, "Could not load this page.")),
      el("p", { class: "tiny" }, error.message),
      el("button", { class: "btn", onclick: () => rerender() }, "Try again"))));
  }
  await refreshSuggestions();
  window.scrollTo({ top: 0, behavior: "instant" });
}

/* ----------------------------------------------------------------- boot */

async function boot() {
  const rail = document.getElementById("rail-nav");
  PAGES.forEach((page, index) => {
    rail.append(el("a", {
      class: "rail-link", href: `#${page.id}`, "data-page": page.id,
    }, el("span", { class: "idx" }, String(index + 1).padStart(2, "0")), page.label));
  });

  try {
    state.health = await api("/api/health");
    const ai = state.health.ai;
    document.getElementById("rail-foot").innerHTML =
      `<div><span class="ai-dot ${ai.available ? "on" : "off"}"></span>` +
      `${ai.available ? `AI live · ${esc(ai.model)}` : "Demo mode · deterministic"}</div>` +
      `<div style="margin-top:5px">${esc(state.health.methodology_version)}</div>`;
  } catch (error) {
    document.getElementById("rail-foot").textContent = "Offline";
  }

  document.getElementById("copilot-toggle").addEventListener("click", () => openCopilot());
  document.getElementById("copilot-close").addEventListener("click", closeCopilot);
  document.getElementById("scrim").addEventListener("click", closeCopilot);
  document.getElementById("copilot-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.getElementById("copilot-input");
    const question = input.value;
    input.value = "";
    askCopilot(question);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.copilotOpen) closeCopilot();
    if (event.key === "/" && !/(INPUT|TEXTAREA|SELECT)/.test(document.activeElement.tagName)) {
      event.preventDefault();
      openCopilot();
    }
  });

  window.addEventListener("hashchange", rerender);
  await rerender();
}

boot();
