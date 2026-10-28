import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SessionView from "../SessionView";
import App from "../App";
import i18n from "../i18n";
import { MockEventSource, json, mockFetch, transcriptOf } from "./testUtils";

global.EventSource = MockEventSource;

const session = {
  id: "s1", agent_id: "gemini", provider: "antigravity", entrypoint: "antigravity", cwd: "/p/lab", title: "sviluppo",
  state: "idle", reachable: true, activity_at: "2026-10-27T23:30:00Z", started_at: "2026-10-27T20:00:00Z",
  last_seen_at: "2026-10-27T23:30:00Z",
};
const entry = (cursor, role, text, at) => ({ role, text, at, cursor });
const page = {
  session_id: "s1", provider: "antigravity", before: null, end: 900,
  messages: [
    entry("100:0", "user", "primo", "2026-10-27T22:00:00Z"),
    entry("200:0", "assistant", "risposta uno", "2026-10-27T22:05:00Z"),
    { role: "action", kind: "read", detail: "/p/x", tool: "Read", cursor: "210:0", at: null },
    entry("300:0", "user", "secondo", "2026-10-27T23:20:00Z"),
    entry("400:0", "assistant", "risposta due", "2026-10-27T23:21:00Z"),
  ],
};
const message = (id, status, created_at, extra = {}) => ({
  id, question_type: "message", status, sender_id: "human", recipient_id: "gemini", recipient_session_id: "s1",
  prompt: `testo ${id}`, created_at, updated_at: created_at, delivery: "waiting", execution: "unobserved", hidden_at: null, ...extra,
});

function renderView(requests, props = {}) {
  mockFetch([[transcriptOf("s1"), page]]);
  return render(<SessionView sessionId="s1" session={session} requests={requests} sessions={[session]} onBack={vi.fn()}
    onDecide={vi.fn()} onSend={vi.fn().mockResolvedValue(true)} onDelivery={vi.fn().mockResolvedValue(true)} onHide={vi.fn()} {...props} />);
}
const order = () => [...document.querySelectorAll('[data-testid="chat-scroll"] [data-testid^="chat-"], [data-testid="chat-scroll"] [data-testid^="outgoing-"], [data-testid="chat-scroll"] [data-testid^="terminal-"]')]
  .map((el) => el.dataset.testid + (el.textContent.match(/primo|secondo|risposta (uno|due)|testo \d+/)?.[0] ? `:${el.textContent.match(/primo|secondo|risposta (uno|due)|testo \d+/)[0]}` : ""));

describe("messages that never went out, in the conversation", () => {
  beforeEach(async () => { await i18n.changeLanguage("it"); });
  afterEach(() => vi.restoreAllMocks());

  it("sits where it was written, in the middle, not after the latest answer", async () => {
    renderView([message(576, "EXPIRED", "2026-10-27T23:13:00Z")]);
    await screen.findByText("risposta due");
    const seq = order();
    const at = (needle) => seq.findIndex((s) => s.includes(needle));
    expect(at("risposta uno")).toBeLessThan(at("testo 576"));
    expect(at("testo 576")).toBeLessThan(at("secondo"));
    expect(at("secondo")).toBeLessThan(at("risposta due"));
    expect(seq[seq.length - 1]).toContain("risposta due");                       // nothing dead is left at the bottom
    expect(screen.getByTestId("terminal-576")).toHaveTextContent("Non consegnato: scaduto");
  });

  it("goes before everything when it is older than the loaded conversation", async () => {
    renderView([message(570, "CANCELLED", "2026-10-27T21:00:00Z")]);
    await screen.findByText("risposta due");
    expect(order()[0]).toContain("testo 570");
  });

  it("keeps a failed delivery where it was (it may still be retried)", async () => {
    renderView([message(3, "DELIVERY_FAILED", "2026-10-27T22:12:00Z")]);
    await screen.findByText("risposta due");
    expect(screen.getByTestId("outgoing-3")).toHaveClass("failed");
    expect(screen.queryByTestId("terminal-3")).toBeNull();
  });

  it("leaves a live message at the bottom", async () => {
    renderView([message(577, "APPROVED", "2026-10-27T23:00:00Z")]);
    await screen.findByText("risposta due");
    const seq = order();
    expect(seq[seq.length - 1]).toContain("outgoing-577");
    expect(screen.queryByTestId("terminal-577")).toBeNull();
  });

  it("labels each way a message can end, and offers send-again and hide on each", async () => {
    renderView([message(1, "EXPIRED", "2026-10-27T22:10:00Z"), message(2, "CANCELLED", "2026-10-27T22:11:00Z"),
      message(4, "REJECTED", "2026-10-27T22:13:00Z")]);
    await screen.findByText("risposta due");
    expect(screen.getByTestId("terminal-1")).toHaveTextContent("Non consegnato: scaduto");
    expect(screen.getByTestId("terminal-2")).toHaveTextContent("annullato");
    expect(screen.getByTestId("terminal-4")).toHaveTextContent("rifiutato");
    for (const id of [1, 2, 4]) {
      const bubble = screen.getByTestId(`terminal-${id}`);
      expect(within(bubble).getByRole("button", { name: "Invia di nuovo" })).toBeInTheDocument();
      expect(within(bubble).getByRole("button", { name: "Nascondi" })).toBeInTheDocument();
    }
  });

  it("sends the same text again as a new message, never reopening the old request", async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    const onDelivery = vi.fn();
    renderView([message(576, "EXPIRED", "2026-10-27T23:13:00Z", { prompt: "Ciao" })], { onSend, onDelivery });
    fireEvent.click(await screen.findByRole("button", { name: "Invia di nuovo" }));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("s1", "Ciao"));
    expect(onDelivery).not.toHaveBeenCalled();
  });

  it("hides the bubble through the app, and a hidden message is no longer in the chat", async () => {
    const onHide = vi.fn();
    const { rerender } = renderView([message(576, "EXPIRED", "2026-10-27T23:13:00Z")], { onHide });
    fireEvent.click(await screen.findByRole("button", { name: "Nascondi" }));
    expect(onHide).toHaveBeenCalledWith(576);
    rerender(<SessionView sessionId="s1" session={session} sessions={[session]} onBack={vi.fn()} onDecide={vi.fn()}
      requests={[message(576, "EXPIRED", "2026-10-27T23:13:00Z", { hidden_at: "2026-10-28T08:00:00Z" })]} onHide={onHide} />);
    await waitFor(() => expect(screen.queryByTestId("terminal-576")).toBeNull());
  });

  it("is in English too", async () => {
    await i18n.changeLanguage("en");
    renderView([message(576, "EXPIRED", "2026-10-27T23:13:00Z")]);
    await screen.findByText("risposta due");
    const bubble = screen.getByTestId("terminal-576");
    expect(within(bubble).getByRole("button", { name: "Send again" })).toBeInTheDocument();
    expect(within(bubble).getByRole("button", { name: "Hide" })).toBeInTheDocument();
    await i18n.changeLanguage("it");
  });
});

describe("hiding from the app", () => {
  beforeEach(async () => { await i18n.changeLanguage("it"); window.location.hash = "#/sessione/s1"; });
  afterEach(() => { vi.restoreAllMocks(); window.location.hash = ""; });

  it("asks the backend and the bubble goes away", async () => {
    const expired = message(576, "EXPIRED", "2026-10-27T23:13:00Z");
    const fetchMock = mockFetch([
      ["/api/requests", [expired]],
      ["/api/sessions?include_ended=true", [session]],
      [transcriptOf("s1"), page],
      [(u, o) => u === "/api/requests/576/hide" && o.method === "POST", () => json({ ...expired, hidden_at: "2026-10-28T08:00:00Z" })],
      [(u) => u.startsWith("/api/"), {}],
    ]);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Nascondi" }));
    await waitFor(() => expect(screen.queryByTestId("terminal-576")).toBeNull());
    expect(fetchMock.mock.calls.some(([u, o]) => u === "/api/requests/576/hide" && o.method === "POST")).toBe(true);
  });
});
