import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

export default defineConfig({
  plugins: [preact()],
  build: {
    // Single IIFE bundle — drop onto any page via <script src="widget.js">
    lib: {
      entry: "src/index.tsx",
      name: "SalesAgentWidget",
      fileName: () => "widget.js",
      formats: ["iife"],
    },
    rollupOptions: {
      // No externals — everything inlined for a self-contained widget
      output: {
        // CSP-compliant: no eval, no Function constructor
        generatedCode: { constBindings: true },
      },
    },
    // Target: <35kb gzipped with Preact
    // esbuild minify is bundled with Vite — no extra dep needed
    minify: "esbuild",
    // Single chunk — embeddable widgets must not code-split
    cssCodeSplit: false,
    // Inline CSS into JS to avoid a separate network request
    cssMinify: true,
  },
  define: {
    // Strip Preact devtools in production
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
});
