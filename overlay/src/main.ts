import { mount } from "svelte";

import "@fontsource-variable/geist"; // bundled locally; never fetched from Google Fonts
import "@fontsource-variable/geist-mono";
import App from "./App.svelte";
import "./app.css";

// Sets the root font size once for this display (larger on high-resolution screens), so resizing the window stays smooth.
const rootPx = Math.min(22, Math.max(16, window.screen.width * 0.0075));
document.documentElement.style.fontSize = `${rootPx}px`;

export default mount(App, { target: document.getElementById("app")! });
