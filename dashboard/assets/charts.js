/* Syn Bank Coverage Desk — charts.
 *
 * Hand-rolled SVG. No library, no CDN, no build step: the dashboard has to run
 * on a judge's laptop with the network switched off.
 *
 * Three rules this file exists to keep, all of them load-bearing:
 *
 *   1. It never formats a rand figure. Currency arrives as the `display` string
 *      the API already rendered and is drawn verbatim; the paired numeric only
 *      ever sets a position. An axis generator would happily round R278.56bn to
 *      R279bn beside a table saying otherwise. Percentages and scores are
 *      presentation of a published ratio, so those are formatted here.
 *   2. Charts are measured, not scaled. `mount()` reads the container width and
 *      redraws on resize. A viewBox stretched to fit its column scales the type
 *      with it, and the same 11px tick then renders at 9px in one card and 13px
 *      in another.
 *   3. There is no categorical palette. Blue is magnitude, amber is decoration,
 *      the status four are states. Anything that would need five series colours
 *      facets into small multiples instead — identity becomes *which panel you
 *      are looking at*, which leaves the one colour channel meaning magnitude.
 */

const NS = "http://www.w3.org/2000/svg";

function s(name, attrs, ...children) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/* Percentages and unitless scores only — never currency. See rule 1. */
const pctTick = (value) => {
  const shown = value * 100;
  if (shown === 0) return "0%";
  if (shown < 1) return `${shown.toFixed(2)}%`;
  if (shown < 10) return `${shown.toFixed(1)}%`;
  return `${Math.round(shown)}%`;
};
const num2 = (value) => (value === null || value === undefined ? "—" : value.toFixed(2));

/* SVG text does not wrap and does not ellipsize, so a long client name in a
 * narrow gutter runs off the left edge of the card rather than being clipped by
 * it. Cut it here instead; the full name stays in the <title> and in the table
 * twin, which is the point of having a twin. */
const fit = (label, pixels, perChar = 5.7) => {
  const room = Math.max(3, Math.floor(pixels / perChar));
  return label.length <= room ? label : `${label.slice(0, room - 1).trimEnd()}…`;
};

/* ---------------------------------------------------------------- mount */

/* The container is built detached and appended later, so its width is 0 at
 * construction time. A ResizeObserver is the only thing that reliably knows
 * when it finally has one — and it doubles as the resize handler. It stops
 * itself once the node leaves the document, because a route change throws the
 * whole page away and an observer holding a detached node is a slow leak. */
export function mount(draw, className = "chart") {
  const holder = document.createElement("div");
  holder.className = className;
  let painted = -1;

  const paint = () => {
    if (!holder.isConnected) return false;
    const width = Math.round(holder.clientWidth);
    if (width < 60 || width === painted) return true;
    painted = width;
    holder.replaceChildren(draw(width));
    return true;
  };

  if (window.ResizeObserver) {
    const observer = new ResizeObserver(() => {
      if (holder.isConnected) paint();
      else observer.disconnect();
    });
    observer.observe(holder);
  } else {
    window.addEventListener("resize", paint);
    requestAnimationFrame(paint);
  }
  return holder;
}

/* ------------------------------------------------------- ranked bar chart */

/* Share of wallet, one bar per client, sorted. The dashed rule is the book —
 * the single most-asked question about any one of these bars is "compared to
 * what?", and without the rule the reader has to hold twenty numbers in mind
 * to answer it.
 *
 * rows       [{ label, value, display, onclick }]
 * reference  { value, label }  optional
 * domainCap  hard ceiling for the axis. A share axis is given 1: without it the
 *            6% headroom the domain adds for the value labels would print a
 *            "106%" tick under a chart of fractions, which is simply not a
 *            quantity that exists.
 */
export function rankedBars(rows, options = {}) {
  const { reference = null, rowHeight = 22, domainCap = null } = options;

  return mount((width) => {
    const labelW = clamp(Math.round(width * 0.3), 92, 168);
    const valueW = 56;
    const padTop = reference ? 20 : 8;
    const axisH = 24;
    const plotW = Math.max(40, width - labelW - valueW);
    const height = padTop + rows.length * rowHeight + axisH;

    const values = rows.map((row) => Math.abs(row.value || 0));
    const headroom =
      Math.max(...values, reference ? Math.abs(reference.value || 0) : 0) * 1.06 || 1;
    const domain = domainCap === null ? headroom : Math.min(headroom, domainCap);
    const x = (value) => labelW + (Math.abs(value || 0) / domain) * plotW;
    const baseline = padTop + rows.length * rowHeight;

    const svg = s("svg", {
      width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
      "aria-label": options.label || "Ranked bars",
    });

    /* Ticks first, so every mark sits over them. Three is enough to read a
     * length off; more turns a twenty-row chart into a grid. */
    for (const fraction of [0, 0.5, 1]) {
      const at = labelW + fraction * plotW;
      svg.append(s("line", { class: "grid-line", x1: at, y1: padTop - 4, x2: at, y2: baseline }));
      svg.append(s("text", { class: "tick", x: at, y: baseline + 15, "text-anchor": fraction === 0 ? "start" : fraction === 1 ? "end" : "middle" },
        pctTick(domain * fraction)));
    }

    rows.forEach((row, index) => {
      const y = padTop + index * rowHeight;
      const barY = y + (rowHeight - 11) / 2;
      const group = s("g", {
        class: `row-hit${row.onclick ? " click" : ""}`,
        role: row.onclick ? "button" : null,
        tabindex: row.onclick ? "0" : null,
        onclick: row.onclick || null,
        onkeydown: row.onclick ? (event) => { if (event.key === "Enter") row.onclick(event); } : null,
      });
      group.append(s("title", null, `${row.label}: ${row.display}`));
      group.append(s("rect", { class: "row-bg", x: 0, y, width, height: rowHeight }));
      group.append(s("text", { class: "cat", x: labelW - 9, y: y + rowHeight / 2 + 4, "text-anchor": "end" },
        fit(row.label, labelW - 12)));
      group.append(s("rect", {
        class: "bar", x: labelW, y: barY,
        width: Math.max(1.5, x(row.value) - labelW), height: 11, rx: 1.5,
      }));
      group.append(s("text", { class: "val", x: width, y: y + rowHeight / 2 + 4, "text-anchor": "end" }, row.display));
      svg.append(group);
    });

    /* The reference is a value, so it is drawn in ink, not in amber: amber is
     * decoration in this system and never encodes a number. */
    if (reference && reference.value !== null && reference.value !== undefined) {
      const at = x(reference.value);
      svg.append(s("line", { class: "ref-line", x1: at, y1: padTop - 6, x2: at, y2: baseline }));
      const anchor = at > labelW + plotW * 0.7 ? "end" : "start";
      svg.append(s("text", {
        class: "ref-label", x: at + (anchor === "end" ? -5 : 5), y: padTop - 10, "text-anchor": anchor,
      }, reference.label));
    }

    return svg;
  });
}

/* ------------------------------------------------------ scatter, faceted */

/* One panel per pillar, x = commercial score, y = confidence. Both axes are
 * unitless and identical in every panel, so the panels are directly comparable
 * — which is the whole reason to facet rather than to colour five series.
 *
 * points [{ x, y, label }]
 * rules  [{ y, label }]  horizontal reference lines (published thresholds only)
 */
export function scatterPanel(points, options = {}) {
  const { rules = [], onEnter = null, onLeave = null, xLabel = "", yLabel = "" } = options;

  return mount((width) => {
    const padL = 30;
    const padR = 10;
    const padT = 10;
    const padB = 26;
    const height = 176;
    const plotW = Math.max(40, width - padL - padR);
    const plotH = height - padT - padB;
    const x = (value) => padL + clamp(value || 0, 0, 1) * plotW;
    const y = (value) => padT + (1 - clamp(value || 0, 0, 1)) * plotH;

    const svg = s("svg", {
      width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
      "aria-label": options.label || `${points.length} clients, ${xLabel} against ${yLabel}`,
    });

    for (const fraction of [0, 0.5, 1]) {
      svg.append(s("line", { class: "grid-line", x1: padL, y1: y(fraction), x2: padL + plotW, y2: y(fraction) }));
      svg.append(s("text", { class: "tick", x: padL - 6, y: y(fraction) + 3.5, "text-anchor": "end" },
        fraction.toFixed(1)));
      svg.append(s("text", { class: "tick", x: x(fraction), y: height - 8, "text-anchor": fraction === 0 ? "start" : fraction === 1 ? "end" : "middle" },
        fraction.toFixed(1)));
    }

    for (const rule of rules) {
      svg.append(s("line", { class: "ref-line", x1: padL, y1: y(rule.y), x2: padL + plotW, y2: y(rule.y) }));
      if (rule.label) {
        /* Anchored left, inside the plot: the right-hand side is where the
         * high-score dots live in every panel, and a label there sits on top
         * of the marks it is meant to explain. */
        svg.append(s("text", { class: "ref-label", x: padL + 4, y: y(rule.y) - 4 }, rule.label));
      }
    }

    for (const point of points) {
      const dot = s("circle", {
        class: "dot", cx: x(point.x), cy: y(point.y), r: 3.4,
        onmousemove: onEnter ? (event) => onEnter(event, point) : null,
        onmouseleave: onLeave || null,
      });
      dot.append(s("title", null, `${point.label}: score ${num2(point.x)}, confidence ${num2(point.y)}`));
      svg.append(dot);
    }

    return svg;
  }, "chart chart-panel");
}

/* ------------------------------------------------- one client, one pillar */

/* This client's share against the book, one row per pillar. Each row carries
 * its own scale on purpose: the three pillars are measured on incomparable
 * denominators, so a shared axis would invite exactly the comparison the model
 * refuses to make. Both numbers are printed, so the scaling cannot mislead.
 *
 * rows [{ label, value, display, refValue, refDisplay, refName }]
 */
export function benchmarkRows(rows) {
  return mount((width) => {
    const rowHeight = 54;
    const height = rows.length * rowHeight;
    const plotW = Math.max(40, width);
    const svg = s("svg", {
      width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
      "aria-label": `Share against the book for ${rows.map((row) => row.label).join(", ")}`,
    });

    rows.forEach((row, index) => {
      const top = index * rowHeight;
      const domain = Math.max(Math.abs(row.value || 0), Math.abs(row.refValue || 0)) * 1.3 || 1;
      const at = (value) => (Math.abs(value || 0) / domain) * plotW;

      svg.append(s("text", { class: "cat-lead", x: 0, y: top + 11 }, row.label));
      svg.append(s("text", { class: "val-lead", x: plotW, y: top + 12, "text-anchor": "end" }, row.display));
      svg.append(s("rect", { class: "track", x: 0, y: top + 20, width: plotW, height: 9, rx: 2 }));
      svg.append(s("rect", { class: "bar", x: 0, y: top + 20, width: Math.max(1.5, at(row.value)), height: 9, rx: 2 }));

      if (row.refValue !== null && row.refValue !== undefined) {
        const mark = at(row.refValue);
        /* The tick crosses whichever of the pale track and the dark bar it lands
         * on, so it carries its own backing stroke — ink on ink is invisible
         * exactly when the client is ahead of the book, which is the one case
         * the reader most wants to see. */
        svg.append(s("line", { class: "ref-halo", x1: mark, y1: top + 15, x2: mark, y2: top + 34 }));
        svg.append(s("line", { class: "ref-line", x1: mark, y1: top + 15, x2: mark, y2: top + 34 }));
        const anchor = mark > plotW * 0.72 ? "end" : mark < 44 ? "start" : "middle";
        svg.append(s("text", {
          class: "ref-label", x: clamp(mark, 0, plotW), y: top + 44, "text-anchor": anchor,
        }, `${row.refName} ${row.refDisplay}`));
      }
    });

    return svg;
  });
}
