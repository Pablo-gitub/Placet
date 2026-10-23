import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import i18n from "../i18n";
import { routeFromHash } from "../decisions";
import { MockEventSource, mockFetch } from "./testUtils";

global.EventSource = MockEventSource;

const now = Date.now();
const ago = (minutes) => new Date(now - minutes * 60000).toISOString();
const requests = [
  { id: 1, sender_id: "claude", recipient_id: "gemini", question_type: "prompt_review", prompt: "Original prompt proposal",
    options: [], status: "PENDING", created_at: ago(1), sender_session_id: "cl1", recipient_session_id: "gm1" },
  { id: 2, sender_id: "claude", recipient_id: "human", question_type: "single_choice", prompt: "Which choice?\n\nWith a second paragraph",
    options: ["Choice A"], status: "COMPLETED", selected_options: ["Choice A"], response: { responder_id: "human", content: "Choice A" },
    created_at: ago(5) },
];
const sessions = [
  { id: "cl1", agent_id: "claude", provider: "claude-code", state: "busy", reachable: true, started_at: ago(60), activity_at: ago(2), title: "Mittente" },
  { id: "gm1", agent_id: "gemini", provider: "antigravity", state: "idle", reachable: true, started_at: ago(60), activity_at: ago(2), title: "Destinatario" },
  { id: "gm2", agent_id: "gemini", provider: "antigravity", state: "idle", reachable: true, started_at: ago(60), activity_at: ago(2), title: "Altra" },
];

function serve() {
  return mockFetch([
    ["/api/requests", requests],
    ["/api/sessions?include_ended=true", sessions],
    ["/api/requests/undelivered", []],
    ["/api/requests/98", {}],
    ["/api/requests/99", Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ detail: "Request not found" }) })],
    [(url) => url.startsWith("/api/"), {}],
  ]);
}

describe("a message in the right pane", () => {
  const original = window.matchMedia;
  const screenWide = (wide) => {
    window.matchMedia = (query) => ({ matches: wide, media: query, addEventListener: () => {}, removeEventListener: () => {} });
  };
  beforeEach(async () => { await i18n.changeLanguage("it"); window.location.hash = ""; });
  afterEach(() => { window.matchMedia = original; window.location.hash = ""; });

  it("has its own address", () => {
    expect(routeFromHash("#/messaggio/42")).toEqual({ view: "message", requestId: 42 });
    expect(routeFromHash("#/messaggio/x")).toEqual({ view: "home" });
  });

  it("opens from a History row on a wide screen, keeping the sidebar", async () => {
    screenWide(true);
    serve();
    render(<App />);
    fireEvent.click(await screen.findByTestId("tab-settings"));
    fireEvent.click(await within(screen.getByTestId("pane")).findByTestId("settings-history"));
    const row = await within(screen.getByTestId("pane")).findByTestId("history-card-2");
    fireEvent.click(within(row).getByRole("button", { name: /#2/ }));
    const detail = await within(screen.getByTestId("pane")).findByTestId("message-detail");
    expect(detail).toHaveTextContent("Messaggio #2");
    expect(detail).toHaveTextContent("With a second paragraph");
    expect(window.location.hash).toBe("#/messaggio/2");
    expect(within(screen.getByTestId("sidebar")).getByTestId("home-section")).toBeInTheDocument();
    expect(within(detail).queryByTestId("message-actions-2")).toBeNull();      // nothing to cancel: it is settled
    fireEvent.click(within(detail).getByTestId("message-back"));
    await waitFor(() => expect(within(screen.getByTestId("pane")).getByTestId("history-section")).toBeInTheDocument());
  });

  it("offers cancel and forward for a message that has not been delivered", async () => {
    screenWide(true);
    serve();
    window.location.hash = "#/messaggio/1";
    render(<App />);
    const detail = await within(await screen.findByTestId("pane")).findByTestId("message-detail");
    expect(detail).toHaveTextContent("Original prompt proposal");
    const actions = within(detail).getByTestId("message-actions-1");
    expect(within(actions).getByRole("button", { name: "Inoltra" })).toBeInTheDocument();
    expect(within(actions).getByRole("button", { name: "Annulla messaggio" })).toBeInTheDocument();
  });

  it("works on the phone as a page of its own", async () => {
    screenWide(false);
    serve();
    window.location.hash = "#/messaggio/2";
    render(<App />);
    expect(await screen.findByTestId("message-detail")).toHaveTextContent("Which choice?");
  });

  it("says so when the message does not exist", async () => {
    screenWide(false);
    serve();
    window.location.hash = "#/messaggio/99";
    render(<App />);
    expect(await screen.findByTestId("message-missing")).toBeInTheDocument();
  });

  it("does not break on an answer that is not a request", async () => {
    screenWide(false);
    serve();
    window.location.hash = "#/messaggio/98";
    render(<App />);
    expect(await screen.findByTestId("message-missing")).toBeInTheDocument();
  });
});
