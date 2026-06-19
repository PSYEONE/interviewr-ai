import { mount } from "svelte";

import "@fontsource-variable/geist"; // bundled locally; never fetched from Google Fonts
import "@fontsource-variable/geist-mono";
import App from "./App.svelte";
import "./app.css";

// Size the root font once for this display (bigger on a 4K panel) as a fixed px value. Doing it
// here instead of with a `vw` unit means resizing the window never recomputes it, so drags stay
// smooth. Clamped to a readable floor and a sane cap.
const rootPx = Math.min(22, Math.max(16, window.screen.width * 0.0075));
document.documentElement.style.fontSize = `${rootPx}px`;

export default mount(App, { target: document.getElementById("app")! });
