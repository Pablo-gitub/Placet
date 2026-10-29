import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SessionView from "../SessionView";
import i18n from "../i18n";
import { MockEventSource, json, mockFetch, paramsOf, transcriptOf } from "./testUtils";

const session = {
  id: "s1", agent_id: "claude", provider: "claude-code", entrypoint: "claude-desktop", cwd: "/p/lab", title: "Lab", state: "idle",
  reachable: true, activity_at: "2026-10-16T10:00:00Z", started_at: "2026-10-16T09:00:00Z", last_seen_at: "2026-10-16T10:00:00Z",
};
const other = { ...session, id: "s2", title: "Altra" };
const msg = (n, extra = {}) => ({ role: "assistant", text: `riga ${n}`, at: null, cursor: `${n}:0`, ...extra });
const range = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => msg(from + i));

// A tiny layout engine: jsdom has none. Every child of the conversation is 100 px tall unless told otherwise; the scroll box
// reports its content height and position of its children from their order and the box's scrollTop.
const layout = { clientHeight: 500, heights: new Map() };
const proto = Element.prototype;
const real = { rect: proto.getBoundingClientRect };
const isBox = (el) => el.dataset?.testid === "chat-scroll";
const content = (box) => box.firstElementChild;
const heightOf = (el) => layout.heights.get(el.textContent.trim()) ?? (el.matches(".load-older, .chat-status") ? 40 : 100);
const kids = (box) => [...(content(box)?.children || [])];
const total = (box) => kids(box).reduce((sum, el) => sum + heightOf(el), 0);
function installLayout() {
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get() { return isBox(this) ? total(this) : 0; } });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get() { return isBox(this) ? layout.clientHeight : 0; } });
  proto.getBoundingClientRect = function rect() {
    const box = this.closest?.('[data-testid="chat-scroll"]');
    if (!box) return real.rect.call(this);
    if (this === box) return { top: 0, bottom: layout.clientHeight, height: layout.clientHeight, left: 0, right: 0, width: 0 };
    let child = this;                                  // a row, or something inside one: the row's box
    while (child && child.parentElement !== content(box)) child = child.parentElement;
    const index = kids(box).indexOf(child);
    if (index < 0) return real.rect.call(this);
    const before = kids(box).slice(0, index).reduce((sum, el) => sum + heightOf(el), 0);
    const top = before - box.scrollTop;
    return { top, bottom: top + heightOf(child), height: heightOf(child), left: 0, right: 0, width: 0 };
  };
}
const observers = new Set();
class FakeResizeObserver {
  constructor(callback) { this.callback = callback; observers.add(this); }
  observe() {}
  unobserve() {}
  disconnect() { observers.delete(this); }
}
const resized = () => act(async () => { for (const o of [...observers]) o.callback([]); });
// A real box never scrolls past its end: clamp like the browser does, once per box.
const box = () => {
  const el = screen.getByTestId("chat-scroll");
  if (!el.__clamped) {
    let top = 0;
    Object.defineProperty(el, "scrollTop", { configurable: true, get: () => top,
      set: (value) => { top = Math.max(0, Math.min(value, total(el) - layout.clientHeight)); } });
    el.__clamped = true;
  }
  return el;
};
const userScrollTo = (top) => { box().scrollTop = top; fireEvent.scroll(box()); };
const rowTop = (text) => screen.getByText(text).getBoundingClientRect().top;

function serve({ older, pages } = {}) {
  return mockFetch([
    [(u) => transcriptOf("s1")(u) && paramsOf(u).get("before") !== null, () => older ?? json({ session_id: "s1", provider: "claude-code", before: null, end: 900, messages: range(1, 7) })],
    [(u) => transcriptOf("s1")(u) && paramsOf(u).get("after") !== null, () => json({ session_id: "s1", provider: "claude-code", before: null, end: 950, messages: [msg(90)] })],
    [transcriptOf("s1"), () => json({ session_id: "s1", provider: "claude-code", before: 8, end: 900, messages: pages || range(8, 20) })],
    [transcriptOf("s2"), () => json({ session_id: "s2", provider: "claude-code", before: null, end: 10, messages: [msg(500, { text: "altra sessione" })] })],
  ]);
}
function renderView(props = {}) {
  const view = render(<SessionView sessionId="s1" session={session} requests={[]} sessions={[session]} onBack={vi.fn()} onDecide={vi.fn()} {...props} />);
  const again = (next = {}) => view.rerender(<SessionView sessionId={next.sessionId || "s1"} session={next.session || session} requests={[]}
    sessions={[session]} onBack={vi.fn()} onDecide={vi.fn()} />);
  return { ...view, again };
}
let tick = 0;
const turn = () => ({ session: { ...session, state: "busy", activity_at: `2026-10-16T10:0${++tick}:00Z` } });

describe("one owner of the scroll: an anchor that follows the first message the reader sees", () => {
  const originalRO = globalThis.ResizeObserver;
  beforeEach(async () => {
    await i18n.changeLanguage("it");
    installLayout();
    globalThis.ResizeObserver = FakeResizeObserver;
    layout.clientHeight = 500;
    layout.heights = new Map();
    tick = 0;
  });
  afterEach(() => { globalThis.ResizeObserver = originalRO; proto.getBoundingClientRect = real.rect; vi.restoreAllMocks(); });

  it("keeps the same message under the reader's eye after a slow prepend, even if the reader kept scrolling meanwhile", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    serve({ older: gate.then(() => json({ session_id: "s1", provider: "claude-code", before: null, end: 900, messages: range(1, 7) })) });
    renderView();
    await screen.findByText("riga 20");
    userScrollTo(100);                                   // near the top: the older page is requested
    userScrollTo(400);                                   // the reader goes on while it loads
    const before = rowTop("riga 12");
    await act(async () => { release(); await gate; await new Promise((r) => setTimeout(r, 10)); });
    await screen.findByText("riga 1");
    expect(rowTop("riga 12")).toBeCloseTo(before, 0);    // 700 px came in above: the message did not move on screen
  });

  it("keeps the place when something above the anchor grows after it was drawn (an image, a font)", async () => {
    serve();
    renderView();
    await screen.findByText("riga 20");
    userScrollTo(600);
    const before = rowTop("riga 14");
    layout.heights.set("riga 9", 400);                   // an image above finishes loading: +300
    await resized();
    expect(rowTop("riga 14")).toBeCloseTo(before, 0);
  });

  it("keeps the place when the keyboard or the composer change the box", async () => {
    serve();
    renderView();
    await screen.findByText("riga 20");
    userScrollTo(600);
    const before = rowTop("riga 14");
    layout.clientHeight = 250;
    await resized();
    expect(rowTop("riga 14")).toBeCloseTo(before, 0);
  });

  it("a scroll the code made itself never decides whether the reader is following", async () => {
    serve();
    const { again } = renderView();
    await screen.findByText("riga 20");
    userScrollTo(total(box()) - layout.clientHeight);    // the reader is at the bottom
    layout.heights.set("riga 20", 700);                  // the last message grows at once: the box pins to the bottom
    await resized();
    const end = box().scrollTop;
    layout.heights.set("riga 20", 1000);                 // and again before the browser reports the first scroll
    fireEvent.scroll(box());                             // the event of the code's own scroll arrives, now far from the bottom
    await resized();
    expect(box().scrollTop).toBeGreaterThan(end);        // still following: the second growth was followed too
    expect(box().scrollTop).toBe(total(box()) - layout.clientHeight);
    expect(screen.queryByTestId("new-messages")).toBeNull();
    again(turn());
  });

  it("an append while the reader scrolls leaves what they read where it was and offers the way down", async () => {
    serve();
    const { again } = renderView();
    await screen.findByText("riga 20");
    userScrollTo(300);
    const before = rowTop("riga 11");
    again(turn());
    await screen.findByText("riga 90");
    expect(rowTop("riga 11")).toBeCloseTo(before, 0);
    expect(screen.getByTestId("new-messages")).toBeInTheDocument();
  });

  it("drops what an older page brought if the session changed in the meantime", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    serve({ older: gate.then(() => json({ session_id: "s1", provider: "claude-code", before: null, end: 900, messages: range(1, 7) })) });
    const { again } = renderView();
    await screen.findByText("riga 20");
    userScrollTo(100);                                   // requests the older page of s1
    again({ sessionId: "s2", session: other });
    await screen.findByText("altra sessione");
    await act(async () => { release(); await gate; await new Promise((r) => setTimeout(r, 10)); });
    expect(screen.queryByText("riga 1")).toBeNull();     // the old answer did not land in the new conversation
    expect(screen.getByText("altra sessione")).toBeInTheDocument();
  });

  it("leaves the browser's own anchoring off, since the app anchors by itself", () => {
    const css = require("node:fs").readFileSync(require("node:path").resolve(__dirname, "../index.css"), "utf8");
    expect(css).toMatch(/\.chat-scroll\s*\{[^}]*overflow-anchor:\s*none/);
  });
});

describe("a jump to a notification's card", () => {
  const card = { id: 7, sender_id: "claude", recipient_id: "human", question_type: "confirmation", prompt: "Procedo?", options: [],
    status: "PENDING", created_at: "2026-10-16T10:00:00Z" };

  async function visit(later) {
    const App = (await import("../App")).default;
    globalThis.EventSource = MockEventSource;
    let answer = [];
    mockFetch([["/api/requests", () => answer], ["/api/sessions?include_ended=true", []], [(u) => u.startsWith("/api/"), {}]]);
    const jumped = vi.fn();
    Element.prototype.scrollIntoView = jumped;
    window.location.hash = "#request-7";
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(1_000_000);
    const { unmount } = render(<App />);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    answer = [card];
    now.mockReturnValue(1_000_000 + later);
    await act(async () => { MockEventSource.last.emit("request_update", card); await new Promise((r) => setTimeout(r, 50)); });
    unmount();
    window.location.hash = "";
    return jumped;
  }

  it("goes to the card when it shows up right after the tap", async () => {
    expect(await visit(500)).toHaveBeenCalled();
  });

  it("does not pull the page when the card shows up long after the tap", async () => {
    expect(await visit(10_000)).not.toHaveBeenCalled();
  });
});
