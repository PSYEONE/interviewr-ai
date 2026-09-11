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
  // Svelte 5 ships separate browser and server entry points. Vitest resolves the server one by
  // default, where mount() throws lifecycle_function_unavailable. The app build already resolves
  // browser, so stating it here changes only the test run.
  resolve: { conditions: ["browser"] },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
