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

/** Render a pane. The desktop draws a real chart where the TUI draws a
 *  sparkline — §6.1 permits exactly this difference, which is why
 *  conformance asserts region/type/binding and never pixels. */
export function renderPane(pane: LayoutPane): string {
  const [, , height, width] = pane.region;
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
