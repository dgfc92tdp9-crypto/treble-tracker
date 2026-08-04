// Conformance bridge: the Python harness drives the TypeScript renderer
// through this, so "web" is a renderer under test in the same suite as
// "reference" and "tui" rather than a parallel test that could drift.
//
// Reads a layout tree on stdin, writes {tree, text} on stdout.
// With `canvas` as the first argument, reads a canvas layout tree (an array
// of placed components) and writes {tree, text} for the whole workspace.
import { textSnapshot, canvasTextSnapshot } from "./dist/renderer.js";

const mode = process.argv[2] === "canvas" ? "canvas" : "screen";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const tree = JSON.parse(input);
  // Echo the tree the renderer actually consumed: if the client mutated or
  // lost anything on the way in, the layout golden catches it.
  const text = mode === "canvas" ? canvasTextSnapshot(tree) : textSnapshot(tree);
  process.stdout.write(JSON.stringify({ tree, text }));
});
