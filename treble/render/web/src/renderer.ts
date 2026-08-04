// Shared cell-grid renderer (spec §6.1; invariant I6).
//
// Consumes the SAME canonical layout tree the conformance suite compares
// and the TUI renders. Nothing here resolves data or interprets a screen
// definition: if this file needed to know what DES means, the abstraction
// would have failed.
//
// Used by both the Tauri desktop shell and the browser client, which is
// why it lives under treble/render/web rather than inside apps/desktop.

export type Attr =
  | "label" | "editable" | "link" | "positive" | "negative"
  | "warning" | "stale" | "model_derived" | "blink" | "emphasis";

export interface LayoutCell {
  at: [number, number];
  text: string;
  attrs: Attr[];
  link: string | null;
  provenance: string | null;
  input: string | null;
}

export interface LayoutPane {
  region: [number, number, number, number]; // row, col, height, width
  type: string;
  binding: string;
  data: Array<Array<string | number | null>>;
}

export interface LayoutTree {
  mnemonic: string;
  tab: string;
  grid: [number, number];
  stale: boolean;
  cells: LayoutCell[];
  panes: LayoutPane[];
  footnotes: string[];
}

// §6.3 colour semantics. Tokens, never colours, come from the definition;
// this is the desktop theme's mapping of them.
const STYLES: Record<Attr, string> = {
  label: "color:#d78700",
  editable: "color:#ffffff;background:#1c1c1c",
  link: "color:#00afd7;cursor:pointer;text-decoration:underline",
  positive: "color:#00af5f",
  negative: "color:#d70000",
  warning: "color:#d7d700",
  stale: "color:#767676",
  // §5.4: model-derived values carry a dotted underline and expand via SPTR.
  model_derived: "text-decoration:underline dotted",
  blink: "animation:treble-blink 1s step-end infinite",
  emphasis: "font-weight:700",
};

/** Style string for a cell. `stale` is applied last so a value known not to
 *  be current always looks stale, whatever else it is (§6.3 makes this
 *  mandatory). */
export function styleFor(attrs: Attr[]): string {
  const ordered = [...attrs].sort((a, b) => Number(a === "stale") - Number(b === "stale"));
  return ordered.map((a) => STYLES[a] ?? "").filter(Boolean).join(";");
}

/** A table pane as real HTML.
 *
 *  MDL, FLDS and SPTR are tables, and a pane type with no drawing renders
 *  as an empty box: the data arrives, conformance passes (it asserts
 *  region and binding, never pixels) and the user sees nothing. */
function renderTable(pane: LayoutPane, height: number, width: number): string {
  const rows = pane.data.map((row) => row.map((cell) => (cell === null ? "" : String(cell))));
  const body = rows
    .map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`)
    .join("");
  return (
    `<div class="pane pane-table" style="grid-row:span ${height};grid-column:span ${width}">`
    + `<table>${body}</table></div>`
  );
}

/** Render a pane. The desktop draws a real chart where the TUI draws a
 *  sparkline — §6.1 permits exactly this difference, which is why
 *  conformance asserts region/type/binding and never pixels. */
export function renderPane(pane: LayoutPane): string {
  const [, , height, width] = pane.region;
  if (pane.type === "table_scroll") return renderTable(pane, height, width);
  const points = pane.data
    .map((row) => row[row.length - 1])
    .filter((v): v is number => typeof v === "number");
  if (points.length < 2) {
    return `<div class="pane" style="grid-row:span ${height};grid-column:span ${width}">`
      + `<span class="pane-label">[${pane.type}:${pane.binding}]</span></div>`;
  }
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const path = points
    .map((v, i) => `${(i / (points.length - 1)) * 100},${100 - ((v - lo) / span) * 100}`)
    .join(" ");
  return (
    `<div class="pane" style="grid-row:span ${height};grid-column:span ${width}">`
    + `<span class="pane-label">[${pane.type}:${pane.binding}]</span>`
    + `<svg viewBox="0 0 100 100" preserveAspectRatio="none" class="pane-chart">`
    + `<polyline points="${path}" fill="none" stroke="#00afd7" stroke-width="1.5"/></svg></div>`
  );
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

// ---------------------------------------------------------------------
// CNVS — a whole workspace (spec §5.3)
// ---------------------------------------------------------------------

export interface CanvasPlacement {
  x: number; y: number; width: number; height: number; display: number;
}

export interface CanvasComponentTree {
  id: string;
  screen: string;
  channel: string | null;
  placement: CanvasPlacement | null;
  tree: LayoutTree;
}

/** Smallest placement with a visible interior. Mirrors MIN_PLACEMENT in
 *  treble/render/canvas.py; a component drawn with no room inside its frame
 *  is indistinguishable from one that failed to resolve. */
export const MIN_PLACEMENT = 3;

/** §6.3 tokens are for cells; a colour group is the *component's* channel and
 *  is drawn on its frame, because a link the user cannot see is a link they
 *  cannot verify. */
const CHANNEL_COLOURS: Record<string, string> = {
  red: "#d70000", blue: "#0087d7", green: "#00af5f",
  yellow: "#d7d700", orange: "#d78700", purple: "#af5fd7",
};

function onDisplay(components: CanvasComponentTree[], display: number): CanvasComponentTree[] {
  return components
    .filter((c) => c.placement !== null && c.placement.display === display)
    .sort((a, b) =>
      a.placement!.y - b.placement!.y
      || a.placement!.x - b.placement!.x
      || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

function padEnd(text: string, length: number, fill: string): string {
  let out = text;
  while (out.length < length) out += fill;
  return out;
}

/** The plain-text projection of a workspace, character-identical to
 *  `canvas_text_snapshot` in treble/render/canvas.py. This is what the
 *  conformance suite compares (I6). */
export function canvasTextSnapshot(
  components: CanvasComponentTree[], display = 0,
): string {
  const placed = onDisplay(components, display);
  const height = placed.reduce((m, c) => Math.max(m, c.placement!.y + c.placement!.height), 0);
  const width = placed.reduce((m, c) => Math.max(m, c.placement!.x + c.placement!.width), 0);
  const grid: string[][] = Array.from({ length: height }, () => Array<string>(width).fill(" "));

  const write = (row: number, col: number, text: string): void => {
    for (let i = 0; i < text.length; i += 1) {
      if (row >= 0 && row < height && col + i >= 0 && col + i < width) grid[row][col + i] = text[i];
    }
  };

  for (const component of placed) {
    const p = component.placement!;
    if (p.width < MIN_PLACEMENT || p.height < MIN_PLACEMENT) {
      throw new Error(
        `component ${JSON.stringify(component.id)} is placed ${p.width}x${p.height}, which is `
        + `too small to draw anything inside a frame (minimum ${MIN_PLACEMENT}x${MIN_PLACEMENT}). `
        + "A component with no visible interior is indistinguishable from one that failed to resolve",
      );
    }
    const innerWidth = p.width - 2;
    const innerHeight = p.height - 2;
    const lines = textSnapshot(component.tree).replace(/\n+$/, "").split("\n");
    const clipped = lines.length > innerHeight || lines.some((l) => l.length > innerWidth);

    let title = ` ${component.screen}`;
    if (component.channel !== null) title += ` ${component.channel}`;
    if (clipped) title += " CLIP";
    title += " ";
    write(p.y, p.x, `+${padEnd(title.slice(0, innerWidth), innerWidth, "-")}+`);
    write(p.y + p.height - 1, p.x, `+${"-".repeat(innerWidth)}+`);
    for (let i = 0; i < innerHeight; i += 1) {
      const body = i < lines.length ? lines[i] : "";
      write(p.y + 1 + i, p.x, `|${padEnd(body.slice(0, innerWidth), innerWidth, " ")}|`);
    }
  }

  const rendered = grid.map((line) => line.join("").replace(/\s+$/, ""));
  const trailer: string[] = [];
  for (const component of [...components].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))) {
    if (component.placement === null) {
      trailer.push(
        `unplaced ${component.screen} ${component.channel ?? "unlinked"} id=${component.id}`);
    }
  }
  const elsewhere = components.filter(
    (c) => c.placement !== null && c.placement.display !== display).length;
  if (elsewhere > 0) {
    trailer.push(`display ${display} shown; ${elsewhere} component(s) on other displays`);
  }
  return [...rendered, ...trailer].join("\n").replace(/\n+$/, "") + "\n";
}

/** The styled workspace the desktop window displays: every component's own
 *  `renderHtml` output, absolutely positioned in workspace cells. */
export function renderCanvasHtml(components: CanvasComponentTree[], display = 0): string {
  const placed = onDisplay(components, display);
  const height = placed.reduce((m, c) => Math.max(m, c.placement!.y + c.placement!.height), 0);
  const width = placed.reduce((m, c) => Math.max(m, c.placement!.x + c.placement!.width), 0);

  const frames = placed.map((component) => {
    const p = component.placement!;
    const colour = component.channel ? CHANNEL_COLOURS[component.channel] ?? "#767676" : "#3a3a3a";
    const label = component.channel
      ? `${component.screen} · ${component.channel}` : component.screen;
    return (
      `<div class="canvas-component" data-component="${escapeHtml(component.id)}"`
      + ` style="grid-row:${p.y + 1}/span ${p.height};grid-column:${p.x + 1}/span ${p.width};`
      + `border:1px solid ${colour}">`
      + `<div class="canvas-title" style="color:${colour}">${escapeHtml(label)}</div>`
      + renderHtml(component.tree)
      + "</div>"
    );
  }).join("");

  // Unplaced components are listed, never dropped: a workspace that rendered
  // four of its six components would look like a working layout.
  const unplaced = components.filter((c) => c.placement === null);
  const note = unplaced.length === 0 ? "" : (
    `<div class="canvas-unplaced">${unplaced.length} component(s) with no placement: `
    + escapeHtml(unplaced.map((c) => `${c.screen} (${c.id})`).join(", "))
    + "</div>"
  );
  return (
    `<div class="canvas" style="display:grid;grid-template-columns:repeat(${width},1ch);`
    + `grid-template-rows:repeat(${height},1.2em)">${frames}</div>${note}`
  );
}

/** The plain-text projection, character-identical to every other renderer.
 *  This is what the conformance suite compares. */
export function textSnapshot(tree: LayoutTree): string {
  const [rows, cols] = tree.grid;
  const grid: string[][] = Array.from({ length: rows }, () => Array<string>(cols).fill(" "));

  const place = (row: number, col: number, text: string): void => {
    for (let i = 0; i < text.length; i += 1) {
      if (row >= 0 && row < rows && col + i >= 0 && col + i < cols) grid[row][col + i] = text[i];
    }
  };

  for (const cell of [...tree.cells].sort((a, b) => a.at[0] - b.at[0] || a.at[1] - b.at[1])) {
    place(cell.at[0], cell.at[1], cell.text);
  }

  // Panes use the renderer-neutral shape: conformance is about layout, not
  // the medium's drawing (CLAUDE.md §4).
  for (const pane of tree.panes) {
    const [row, col, height, width] = pane.region;
    const horizontal = width >= 2 ? `+${"-".repeat(width - 2)}+` : "+";
    place(row, col, horizontal);
    const label = `[${pane.type}:${pane.binding}]`.slice(0, Math.max(width - 2, 0));
    for (let i = 1; i < height - 1; i += 1) {
      place(row + i, col, `|${(i === 1 ? label : "").padEnd(Math.max(width - 2, 0))}|`);
    }
    if (height > 1) place(row + height - 1, col, horizontal);
  }

  return grid.map((line) => line.join("").replace(/\s+$/, "")).join("\n").replace(/\n+$/, "") + "\n";
}

/** The styled HTML the desktop window displays. */
export function renderHtml(tree: LayoutTree): string {
  const [rows, cols] = tree.grid;
  const lines: string[][] = Array.from({ length: rows }, () => Array<string>(cols).fill(" "));
  const styled: string[] = [];

  for (const cell of [...tree.cells].sort((a, b) => a.at[0] - b.at[0] || a.at[1] - b.at[1])) {
    const [row, col] = cell.at;
    const style = styleFor(cell.attrs);
    const attrs = cell.link ? ` data-command="${escapeHtml(cell.link)}"` : "";
    const trace = cell.provenance ? ` data-provenance="${escapeHtml(cell.provenance)}"` : "";
    styled.push(
      `<span class="cell" style="grid-row:${row + 1};grid-column:${col + 1}/span ${cell.text.length};${style}"${attrs}${trace}>`
      + `${escapeHtml(cell.text)}</span>`,
    );
    for (let i = 0; i < cell.text.length; i += 1) {
      if (col + i < cols) lines[row][col + i] = cell.text[i];
    }
  }

  const panes = tree.panes.map(renderPane).join("");
  return `<div class="grid" style="grid-template-columns:repeat(${cols},1ch);grid-template-rows:repeat(${rows},1.2em)">${styled.join("")}${panes}</div>`;
}
