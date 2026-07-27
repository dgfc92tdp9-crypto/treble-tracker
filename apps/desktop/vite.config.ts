import { defineConfig } from "vite";

export default defineConfig({
  // Tauri serves the built assets from a file:// origin, so asset URLs
  // must be relative rather than root-absolute.
  base: "./",
  build: { outDir: "dist", emptyOutDir: true, target: "safari15" },
  server: { port: 5173, strictPort: true },
});
