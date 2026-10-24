import React from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SessionView from "../SessionView";
import i18n from "../i18n";
import { json, mockFetch, paramsOf, transcriptOf } from "./testUtils";

const session = {
  id: "s1", agent_id: "claude", provider: "claude-code", entrypoint: "claude-desktop", cwd: "/p/lab", title: "Lab",
  state: "idle", reachable: true, activity_at: "2026-10-16T10:00:00Z", started_at: "2026-10-16T09:00:00Z",
  last_seen_at: "2026-10-16T10:00:00Z",
};
const msg = (cursor, text) => ({ role: "assistant", text, at: null, cursor });
const first = { session_id: "s1", provider: "claude-code", before: null, end: 900, messages: [msg("500:0", "uno"), msg("600:0", "due")] };

// jsdom has no layout: the scroll box reports the sizes the test sets.
const layout = { scrollHeight: 2000, clientHeight: 500 };
const proto = HTMLElement.prototype;
const real = { sh: Object.getOwnPropertyDescriptor(proto, "scrollHeight"), ch: Object.getOwnPropertyDescriptor(proto, "clientHeight") };
const isScroll = (el) => el.dataset?.testid === "chat-scroll";
defineSizes();
function defineSizes() {
  Object.defineProperty(proto, "scrollHeight", { configurable: true, get() { return isScroll(this) ? layout.scrollHeight : (real.sh?.get?.call(this) ?? 0); } });
  Object.defineProperty(proto, "clientHeight", { configurable: true, get() { return isScroll(this) ? layout.clientHeight : (real.ch?.get?.call(this) ?? 0); } });
}

const box = () => screen.getByTestId("chat-scroll");
const scrollTo = (top) => { box().scrollTop = top; fireEvent.scroll(box()); };
const atDistance = (px) => scrollTo(layout.scrollHeight - layout.clientHeight - px);

let more;
function serve({ older } = {}) {
  more = { session_id: "s1", provider: "claude-code", before: null, end: 950, messages: [msg("900:0", "tre")] };
  return mockFetch([
    [(u) => transcriptOf("s1")(u) && paramsOf(u).get("before") !== null, () => older ?? json({ ...first, before: null, messages: [msg("100:0", "zero")] })],
    [(u) => transcriptOf("s1")(u) && paramsOf(u).get("after") !== null, () => json(more)],
    [transcriptOf("s1"), () => json(first)],
  ]);
}

function renderView(props = {}) {
  const view = render(<SessionView sessionId="s1" session={session} requests={[]} sessions={[session]} onBack={vi.fn()}
    onDecide={vi.fn()} onSend={vi.fn().mockResolvedValue(true)} {...props} />);
  const again = (next = {}) => view.rerender(<SessionView sessionId="s1" session={{ ...session, ...next.session }} requests={next.requests || []}
    sessions={[session]} onBack={vi.fn()} onDecide={vi.fn()} onSend={props.onSend || vi.fn().mockResolvedValue(true)} />);
  return { ...view, again };
}
let tick = 0;
const turn = () => ({ session: { state: "busy", activity_at: `2026-10-16T10:0${++tick}:00Z` } });

describe("the conversation keeps the reader's place", () => {
  beforeEach(async () => { await i18n.changeLanguage("it"); layout.scrollHeight = 2000; layout.clientHeight = 500; tick = 0; });
  afterEach(() => vi.restoreAllMocks());

  it("stays at the bottom when the reader is at the bottom", async () => {
    serve();
    const { again } = renderView();
    await screen.findByText("due");
    atDistance(0);
    layout.scrollHeight = 2400;
    again(turn());
    await screen.findByText("tre");
    expect(box().scrollTop).toBe(2400);
    expect(screen.queryByTestId("new-messages")).toBeNull();
  });

  it("keeps the position of a reader who scrolled up, and offers a way down", async () => {
    serve();
    const { again } = renderView();
    await screen.findByText("due");
    scrollTo(300);
    layout.scrollHeight = 2400;
    again(turn());
    await screen.findByText("tre");
    expect(box().scrollTop).toBe(300);
    expect(screen.getByTestId("new-messages")).toHaveTextContent("Nuovi messaggi");
  });

  it("does not follow a reader who is more than 80 px from the bottom", async () => {
    serve();
    const { again } = renderView();
    await screen.findByText("due");
    atDistance(100);
    const where = box().scrollTop;
    layout.scrollHeight = 2400;
    again(turn());
    await screen.findByText("tre");
    expect(box().scrollTop).toBe(where);
  });

  it("follows a reader who is within 80 px of the bottom", async () => {
    serve();
    const { again } = renderView();
    await screen.findByText("due");
    atDistance(60);
    layout.scrollHeight = 2400;
    again(turn());
    await screen.findByText("tre");
    expect(box().scrollTop).toBe(2400);
  });

  it("does not move at all when a refresh brings nothing new", async () => {
    const fetchMock = serve();
    const { again } = renderView();
    await screen.findByText("due");
    atDistance(60);                                                      // close enough to be following
    const where = box().scrollTop;
    more = { ...first, end: 900, messages: first.messages };             // only what is already shown
    again(turn());
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => paramsOf(u).get("after") === "900")).toBe(true));
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(box().scrollTop).toBe(where);
    expect(screen.queryByTestId("new-messages")).toBeNull();
  });

  it("scrolls down for the reader's own message even from above", async () => {
    serve();
    const onSend = vi.fn().mockResolvedValue(true);
    const { again } = renderView({ onSend });
    await screen.findByText("due");
    scrollTo(300);
    fireEvent.change(screen.getByTestId("composer-input"), { target: { value: "ecco" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    layout.scrollHeight = 2300;
    again({ requests: [{ id: 9, question_type: "message", status: "APPROVED", recipient_session_id: "s1", sender_id: "human",
      recipient_id: "claude", prompt: "ecco", created_at: "2026-10-16T10:05:00Z", delivery: "waiting" }] });
    await waitFor(() => expect(box().scrollTop).toBe(2300));
  });

  it("goes down when the new-messages button is tapped, and the button goes away", async () => {
    serve();
    const { again } = renderView();
    await screen.findByText("due");
    scrollTo(300);
    layout.scrollHeight = 2400;
    again(turn());
    fireEvent.click(await screen.findByTestId("new-messages"));
    expect(box().scrollTop).toBe(2400);
    expect(screen.queryByTestId("new-messages")).toBeNull();
  });

  it("drops the button when the reader reaches the bottom by hand", async () => {
    serve();
    const { again } = renderView();
    await screen.findByText("due");
    scrollTo(300);
    layout.scrollHeight = 2400;
    again(turn());
    await screen.findByTestId("new-messages");
    atDistance(10);
    await waitFor(() => expect(screen.queryByTestId("new-messages")).toBeNull());
  });

  it("is in English too", async () => {
    await i18n.changeLanguage("en");
    serve();
    const { again } = renderView();
    await screen.findByText("due");
    scrollTo(300);
    layout.scrollHeight = 2400;
    again(turn());
    expect(await screen.findByTestId("new-messages")).toHaveTextContent("New messages");
    await i18n.changeLanguage("it");
  });

  it("restores the place after older messages are prepended, even if a refresh lands first", async () => {
    let releaseOlder;
    const older = new Promise((resolve) => { releaseOlder = resolve; });
    serve({ older: older.then(() => json({ session_id: "s1", provider: "claude-code", before: null, end: 900, messages: [msg("100:0", "zero")] })) });
    // The first page says there is an older one.
    first.before = 500;
    try {
      const { again } = renderView();
      await screen.findByText("due");
      scrollTo(100);                                                    // near the top: the older page is requested
      layout.scrollHeight = 2300;                                       // a refresh appends 300 px first
      again(turn());
      await screen.findByText("tre");
      expect(box().scrollTop).toBe(100);                                // the append did not move the reader
      layout.scrollHeight = 3000;                                       // then 700 px of older messages arrive above
      await act(async () => { releaseOlder(); await older; await new Promise((r) => setTimeout(r, 10)); });
      await screen.findByText("zero");
      expect(box().scrollTop).toBe(100 + 700);
    } finally {
      first.before = null;
    }
  });
});
