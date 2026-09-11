// The answer renderer's guarantee, stated so it can be run rather than reasoned about: model
// output never becomes markup. It matters here because tauri.conf.json sets no CSP, the webview
// can reach invoke, and answers are shaped by the user's own uploaded documents through RAG.

import { describe, expect, it } from "vitest";

import { markdown } from "./markdown";

function render(text: string): HTMLElement {
  const node = document.createElement("div");
  markdown(node, text);
  return node;
}

describe("model output never becomes markup", () => {
  it("shows a block-level tag as characters", () => {
    const node = render("<img src=x onerror=alert(1)>");
    expect(node.querySelector("img")).toBeNull();
    expect(node.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("shows a script tag as characters", () => {
    const node = render("<script>alert(1)</script>");
    expect(node.querySelector("script")).toBeNull();
    expect(node.textContent).toContain("<script>");
  });

  it("shows inline tags as characters, keeping the words around them", () => {
    const node = render("text <b>bold</b> more");
    expect(node.querySelector("b")).toBeNull();
    expect(node.textContent).toBe("text <b>bold</b> more");
  });

  it("does not create an anchor for a markdown link", () => {
    // No navigable node is ever built, so a javascript: target has nothing to attach to.
    const node = render("[click](javascript:alert(1))");
    expect(node.querySelector("a")).toBeNull();
  });

  it("does not create an image for markdown image syntax", () => {
    // An <img> would be a loadable node, which is a request the answer should not be able to make.
    const node = render("![alt](http://example.invalid/x.png)");
    expect(node.querySelector("img")).toBeNull();
  });

  it("does not smuggle a tag through emphasis or a list", () => {
    const node = render("- **<iframe src=evil>** item");
    expect(node.querySelector("iframe")).toBeNull();
    expect(node.textContent).toContain("<iframe src=evil>");
  });

  it("keeps a fenced block's contents inert", () => {
    const node = render("```html\n<script>alert(1)</script>\n```");
    expect(node.querySelector("script")).toBeNull();
    expect(node.querySelector("pre code")?.textContent).toBe("<script>alert(1)</script>");
  });
});

describe("the formatting answers actually use", () => {
  it("renders bold and italic", () => {
    const node = render("a **b** and _c_");
    expect(node.querySelector("strong")?.textContent).toBe("b");
    expect(node.querySelector("em")?.textContent).toBe("c");
  });

  it("renders inline code", () => {
    expect(render("use `npm test` here").querySelector("code")?.textContent).toBe("npm test");
  });

  it("renders a bullet list as list items", () => {
    // The reported regression: bullets arriving as plain newlines collapsed onto one line.
    const node = render("- first\n- second\n- third");
    expect(node.querySelectorAll("ul > li")).toHaveLength(3);
    expect(node.querySelectorAll("li")[1]?.textContent?.trim()).toBe("second");
  });

  it("keeps an ordered list's starting number", () => {
    const ol = render("3. three\n4. four").querySelector("ol");
    expect(ol?.start).toBe(3);
    expect(ol?.querySelectorAll("li")).toHaveLength(2);
  });

  it("nests a sub-list inside its item", () => {
    const node = render("- outer\n    - inner");
    expect(node.querySelector("ul > li ul > li")?.textContent?.trim()).toBe("inner");
  });

  it("renders a fenced block as preformatted code", () => {
    const node = render("```js\nconst x = 1;\n```");
    expect(node.querySelector("pre > code")?.textContent).toBe("const x = 1;");
  });
});

describe("rendering while the answer streams", () => {
  it("replaces the previous render instead of appending to it", () => {
    // update() runs on every streamed token, so appending would duplicate the whole answer.
    const node = document.createElement("div");
    const { update } = markdown(node, "- one");
    update("- one\n- two");
    expect(node.querySelectorAll("li")).toHaveLength(2);
  });

  it("renders half-written markdown as far as it parses", () => {
    expect(() => render("**bold")).not.toThrow();
    expect(render("**bold").textContent).toContain("bold");
  });

  it("handles empty text", () => {
    expect(render("").textContent).toBe("");
  });
});
