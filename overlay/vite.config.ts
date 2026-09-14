import tailwindcss from "@tailwindcss/vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

// Tauri serves the built ../dist and drives a fixed-port dev server (devUrl in tauri.conf.json).
// Target esnext: the only consumer is the bundled WebKitGTK/WebView2/WKWebView, all modern.
export default defineConfig({
  plugins: [tailwindcss(), svelte()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: { target: "esnext", outDir: "dist", emptyOutDir: true },
  // Makes Vitest use Svelte's browser build, where mount() works; the app build already uses it.
  resolve: { conditions: ["browser"] },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
