// The TypeScript renderer against the SAME conformance goldens the Python
// renderers use (invariant I6, CLAUDE.md §4).
//
// This is the test that matters for the desktop client: if it passes, the
// desktop and the TUI cannot disagree about any screen, because both are
// compared to one golden artefact rather than to each other.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { textSnapshot, styleFor } from "../dist/renderer.js";

const here = dirname(fileURLToPath(import.meta.url));
const casesDir = join(here, "..", "..", "..", "..", "tests", "conformance", "cases");

const cases = readdirSync(casesDir, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name);

test("conformance cases exist", () => {
  assert.ok(cases.length > 0, "no conformance cases found");
});

for (const name of cases) {
  const dir = join(casesDir, name);
  const treePath = join(dir, "golden.layout.json");
  const textPath = join(dir, "golden.txt");
  if (!existsSync(treePath) || !existsSync(textPath)) continue;

  test(`${name}: text snapshot matches the golden`, () => {
    const tree = JSON.parse(readFileSync(treePath, "utf8"));
    const expected = readFileSync(textPath, "utf8");
    assert.equal(textSnapshot(tree), expected);
  });

  test(`${name}: every cell's attributes map to styles`, () => {
    const tree = JSON.parse(readFileSync(treePath, "utf8"));
    for (const cell of tree.cells) {
      // An unmapped token would render unstyled and silently lose meaning.
      if (cell.attrs.length > 0) assert.ok(styleFor(cell.attrs).length > 0, `${cell.attrs}`);
    }
  });
}

test("stale styling wins over other colours (§6.3)", () => {
  const combined = styleFor(["positive", "stale"]);
  assert.ok(combined.endsWith("color:#767676"), combined);
});
