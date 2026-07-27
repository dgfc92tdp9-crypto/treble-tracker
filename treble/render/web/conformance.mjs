// Conformance bridge: the Python harness drives the TypeScript renderer
// through this, so "web" is a renderer under test in the same suite as
// "reference" and "tui" rather than a parallel test that could drift.
//
// Reads a layout tree on stdin, writes {tree, text} on stdout.
import { textSnapshot } from "./dist/renderer.js";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const tree = JSON.parse(input);
  // Echo the tree the renderer actually consumed: if the client mutated or
  // lost anything on the way in, the layout golden catches it.
  process.stdout.write(JSON.stringify({ tree, text: textSnapshot(tree) }));
});
