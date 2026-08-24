// Render the markdown subset an answer actually contains (lists, emphasis, code) into real DOM.
//
// marked is used only as a lexer: this never builds an HTML string, and every leaf lands via
// textContent or createTextNode. So raw HTML in model output renders as visible characters and
// can never execute. That guarantee is structural rather than a sanitiser setting, which matters
// here because tauri.conf.json sets no CSP, the webview can reach `invoke`, and answers are
// influenced by the user's uploaded documents through RAG.
//
// Headings, tables, blockquotes, links and images are out of scope: they fall through to their
// source text, so nothing is lost and no navigable or loadable node is ever created.

import { marked, type Token, type Tokens } from "marked";

function textOf(t: Token): string {
  return "text" in t && typeof t.text === "string" ? t.text : t.raw;
}

function appendInline(into: Node, tokens: Token[] | undefined, fallback: string): void {
  if (!tokens || tokens.length === 0) {
    into.appendChild(document.createTextNode(fallback));
    return;
  }
  for (const t of tokens) {
    switch (t.type) {
      case "strong":
      case "em": {
        const el = document.createElement(t.type === "strong" ? "strong" : "em");
        appendInline(el, (t as Tokens.Strong).tokens, (t as Tokens.Strong).text);
        into.appendChild(el);
        break;
      }
      case "codespan": {
        const el = document.createElement("code");
        el.textContent = (t as Tokens.Codespan).text;
        into.appendChild(el);
        break;
      }
      case "br":
        into.appendChild(document.createElement("br"));
        break;
      case "text": {
        const tt = t as Tokens.Text;
        if (tt.tokens) appendInline(into, tt.tokens, tt.text);
        else into.appendChild(document.createTextNode(tt.text));
        break;
      }
      default:
        into.appendChild(document.createTextNode(textOf(t)));
    }
  }
}

function appendBlocks(into: Node, tokens: Token[]): void {
  for (const t of tokens) {
    switch (t.type) {
      case "space":
        break;
      case "list": {
        const list = t as Tokens.List;
        const el = document.createElement(list.ordered ? "ol" : "ul");
        if (list.ordered) {
          const start = Number(list.start);
          if (Number.isFinite(start) && start !== 1) (el as HTMLOListElement).start = start;
        }
        for (const item of list.items) {
          const li = document.createElement("li");
          // Items always hold block tokens; a tight list's paragraph margins are flattened in CSS
          // rather than by special-casing here, which keeps nested lists working.
          appendBlocks(li, item.tokens ?? []);
          el.appendChild(li);
        }
        into.appendChild(el);
        break;
      }
      case "code": {
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = (t as Tokens.Code).text;
        pre.appendChild(code);
        into.appendChild(pre);
        break;
      }
      case "paragraph":
      case "text": {
        const p = document.createElement("p");
        const tt = t as Tokens.Paragraph;
        appendInline(p, tt.tokens, tt.text);
        into.appendChild(p);
        break;
      }
      default: {
        const p = document.createElement("p");
        p.textContent = t.raw;
        into.appendChild(p);
      }
    }
  }
}

/** Svelte action: `<div use:markdown={text}></div>`. Rebuilds on every change, including each
 *  streamed token, so partially-written markdown simply renders as far as it parses. */
export function markdown(node: HTMLElement, text: string): { update: (t: string) => void } {
  const render = (value: string): void => {
    node.textContent = "";
    appendBlocks(node, marked.lexer(value ?? ""));
  };
  render(text);
  return { update: render };
}
