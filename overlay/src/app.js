// Overlay client. The data path is a plain WebSocket to the backend; Rust only
// supplies the connection details (host/port/token from the project .env).
const { invoke } = window.__TAURI__.core;
const { getCurrentWindow } = window.__TAURI__.window;

// data-tauri-drag-region does not fire on the WebKitGTK (Linux) backend, so drive the
// drag explicitly from the title bar's mousedown instead.
const bar = document.getElementById("bar");
bar.addEventListener("mousedown", (e) => {
  if (e.button !== 0 || e.target.closest("button")) return;
  getCurrentWindow().startDragging().catch(() => {});
});

const dot = document.getElementById("dot");
const done = document.getElementById("t-done");
const live = document.getElementById("t-live");
const transcript = document.getElementById("transcript");
const answer = document.getElementById("answer");

let ws = null;
let committed = "";
let curAnswer = null;
let backoff = 1000;

function setStatus(state) {
  dot.dataset.state = state;
  dot.title = state;
}

function atBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
}

async function connect() {
  let conn;
  try {
    conn = await invoke("get_connection");
  } catch {
    conn = { host: "127.0.0.1", port: 8765, token: "" };
  }
  const url = `ws://${conn.host}:${conn.port}/ws?token=${encodeURIComponent(conn.token)}`;
  setStatus("connecting");
  ws = new WebSocket(url);

  ws.onopen = () => {
    backoff = 1000;
    setStatus("listening");
  };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => {
    setStatus("offline");
    retry();
  };
  ws.onerror = () => {
    try { ws.close(); } catch {}
  };
}

function retry() {
  setTimeout(connect, backoff);
  backoff = Math.min(backoff * 1.6, 8000); // capped exponential backoff
}

function handle(ev) {
  switch (ev.type) {
    case "transcript": {
      const stick = atBottom(transcript);
      if (ev.final) {
        if (ev.text) committed += ev.text + " ";
        done.textContent = committed;
        live.textContent = ""; // cleared; next partial re-sets the tail
      } else {
        live.textContent = ev.text; // tentative tail, replaced in place
      }
      if (stick) transcript.scrollTop = transcript.scrollHeight;
      break;
    }
    case "answer.start":
      curAnswer = ev.answer_id;
      answer.textContent = "";
      break;
    case "answer.token":
      if (ev.answer_id === curAnswer) {
        const stick = atBottom(answer);
        answer.textContent += ev.token;
        if (stick) answer.scrollTop = answer.scrollHeight;
      }
      break;
    case "answer.done":
      if (ev.cancelled) answer.textContent += " [cancelled]";
      break;
    case "status":
      setStatus(ev.state);
      break;
    case "error":
      answer.textContent += `\n[error: ${ev.message}]`;
      break;
  }
}

function send(type) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type }));
}

document.getElementById("ans").onclick = () => send("answer");
document.getElementById("clr").onclick = () => send("clear");
document.getElementById("rst").onclick = () => {
  send("reset");
  committed = "";
  done.textContent = "";
  live.textContent = "";
  answer.textContent = "";
};
document.getElementById("quit").onclick = () => invoke("quit");

setInterval(() => send("ping"), 20000); // keepalive
connect();
