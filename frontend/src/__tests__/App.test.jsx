import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import App from "../App";
import { MockEventSource, mockFetch, transcriptOf } from "./testUtils";

global.EventSource = MockEventSource;

const now = Date.now();
const ago = (minutes) => new Date(now - minutes * 60000).toISOString();

const requests = [
  {
    id: 1, sender_id: "claude", recipient_id: "gemini", question_type: "prompt_review",
    prompt: "Original prompt proposal", context: "Refactoring test context", options: [],
    status: "PENDING", created_at: ago(1),
  },
  {
    id: 2, sender_id: "claude", recipient_id: "human", question_type: "single_choice",
    prompt: "Which choice?", options: ["Choice A", "Choice B"], status: "COMPLETED",
    selected_options: ["Choice A"], response: { responder_id: "human", content: "Choice A" },
    created_at: ago(5),
  },
  {
    id: 3, sender_id: "codex", recipient_id: "human", question_type: "confirmation",
    prompt: "Procedo con il deploy?", options: [], status: "PENDING", created_at: ago(1),
    sender_session_id: "cx1",
  },
];

const session = (id, extra) => ({
  id, agent_id: "claude", provider: "claude-code", entrypoint: "claude-desktop", state: "idle",
  reachable: true, started_at: ago(60), last_seen_at: ago(1), pinned: false, ...extra,
});

const sessions = [
  session("old", { title: "Vecchia", cwd: "/p/placet", pinned: true, activity_at: ago(30) }),
  session("new", { title: "Recente", cwd: "/p/lab", pinned: true, activity_at: ago(2), state: "busy" }),
  session("other", { title: "Non seguita", cwd: "/p/x", activity_at: ago(1) }),
  session("cx1", { agent_id: "codex", provider: "codex", entrypoint: "codex-desktop", title: "Deploy", cwd: "/p/app",
    activity_at: ago(3) }),
];

const page = {
  session_id: "cx1", provider: "codex", before: null, end: 100,
  messages: [
    { role: "user", text: "prepara il deploy", at: ago(4), cursor: "0:0" },
    { role: "assistant", text: "Pronto, ti chiedo conferma.", at: ago(3), cursor: "10:0" },
  ],
};

function setup(extraRoutes = []) {
  return mockFetch([
    ...extraRoutes,
    ["/api/requests", requests],
    ["/api/sessions?include_ended=true", sessions],
    [transcriptOf("cx1"), page],
    [transcriptOf("new"), { ...page, session_id: "new", messages: [] }],
    [(url) => url.includes("/decision"), { id: 1, status: "APPROVED" }],
    [(url, o) => url.endsWith("/pin") && o.method === "PUT",
      (url, o) => ({ ...sessions.find((s) => url.includes(`/${s.id}/`)), pinned: JSON.parse(o.body).pinned })],
  ]);
}

const decisionBody = (fetchMock, id) => {
  const call = fetchMock.mock.calls.find(([url]) => url === `/api/requests/${id}/decision`);
  return call ? JSON.parse(call[1].body) : undefined;
};

describe("Placet PWA", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("shows the pending questions in the home, with the prompt readable and editable", async () => {
    setup();
    render(<App />);
    expect(screen.getByText("Placet")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("request-card-1")).toBeInTheDocument());
    expect(screen.getByTestId("request-card-3")).toBeInTheDocument();
    expect(screen.queryByTestId("request-card-2")).toBeNull(); // settled: history only
    expect(screen.getByTestId("pending-badge")).toHaveTextContent("2");
    expect(screen.getByText("Refactoring test context")).toBeInTheDocument();
    expect(screen.getByTestId("prompt-preview-1")).toHaveTextContent("Original prompt proposal");
    fireEvent.click(screen.getByTestId("toggle-edit-1"));
    expect(screen.getByTestId("prompt-textarea-1").value).toBe("Original prompt proposal");
  });

  it("sends the edited prompt with the approval", async () => {
    const fetchMock = setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("toggle-edit-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("toggle-edit-1"));
    fireEvent.change(screen.getByTestId("prompt-textarea-1"), { target: { value: "Modified prompt by Paolo" } });
    fireEvent.click(screen.getByTestId("btn-approve-1"));
    await waitFor(() =>
      expect(decisionBody(fetchMock, 1)).toEqual({
        status: "APPROVED", edited_prompt: "Modified prompt by Paolo", selected_options: [],
      })
    );
  });

  it("lists only the followed sessions, most recently active first", async () => {
    setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("session-row-new")).toBeInTheDocument());
    const rows = screen.getAllByTestId(/^session-row-/).map((r) => r.dataset.testid);
    expect(rows).toEqual(["session-row-new", "session-row-old"]);
    expect(screen.getByTestId("session-row-new")).toHaveTextContent("Al lavoro");
    expect(screen.getByTestId("session-row-new")).toHaveTextContent("Claude");
  });

  it("adds a session from the dialog, provider by provider", async () => {
    const fetchMock = setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("open-add-sessions")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("open-add-sessions"));
    const dialog = screen.getByTestId("add-sessions-dialog");
    expect(within(dialog).getByTestId("pick-toggle-old")).toBeChecked();
    expect(within(dialog).getByTestId("pick-toggle-other")).not.toBeChecked();
    expect(within(dialog).queryByTestId("pick-cx1")).toBeNull(); // Claude tab first

    fireEvent.click(within(dialog).getByTestId("provider-tab-codex"));
    fireEvent.click(within(dialog).getByTestId("pick-toggle-cx1"));
    await waitFor(() => expect(screen.getByTestId("session-row-cx1")).toBeInTheDocument());
    const call = fetchMock.mock.calls.find(([url, o]) => url === "/api/sessions/cx1/pin" && o.method === "PUT");
    expect(JSON.parse(call[1].body)).toEqual({ pinned: true });
    expect(within(dialog).getByTestId("provider-tab-antigravity")).toHaveTextContent("0");
  });

  it("lets the user remove a closed followed session without changing another session with the same title", async () => {
    const closed = session("closed", { title: "Primo sviluppatore", pinned: true, ended_at: ago(10) });
    const open = session("open", { title: "Primo sviluppatore", pinned: true });
    const fetchMock = setup([
      ["/api/sessions?include_ended=true", [closed, open]],
      [(url, o) => url === "/api/sessions/closed/pin" && o.method === "PUT",
        { ...closed, pinned: false }],
    ]);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("session-row-closed")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("open-add-sessions"));
    const dialog = screen.getByTestId("add-sessions-dialog");
    expect(within(dialog).getByRole("heading", { name: "Sessioni da seguire" })).toBeInTheDocument();
    expect(within(dialog).getByTestId("pick-closed")).toHaveTextContent("Chiusa");
    expect(within(dialog).getByTestId("pick-toggle-closed")).toBeChecked();
    fireEvent.click(within(dialog).getByTestId("pick-toggle-closed"));
    await waitFor(() => expect(screen.queryByTestId("session-row-closed")).toBeNull());
    expect(screen.getByTestId("session-row-open")).toBeInTheDocument();
    expect(within(dialog).getByTestId("pick-toggle-open")).toBeChecked();
    expect(within(dialog).getByTestId("pick-toggle-closed")).not.toBeChecked();
    expect(fetchMock.mock.calls.filter(([url, o]) => url.endsWith("/pin") && o.method === "PUT")).toHaveLength(1);
  });

  it("opens a session's conversation with its question at the bottom", async () => {
    setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("open-session-3")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("open-session-3"));
    await waitFor(() => expect(screen.getByTestId("session-view")).toBeInTheDocument());
    expect(window.location.hash).toBe("#/sessione/cx1");
    expect(screen.getByTestId("session-view-name")).toHaveTextContent("Deploy");
    await waitFor(() => expect(screen.getByText("prepara il deploy")).toBeInTheDocument());
    const view = screen.getByTestId("chat-scroll");
    expect(within(view).getByTestId("request-card-3")).toBeInTheDocument();
    expect(within(view).queryByTestId("request-card-1")).toBeNull(); // not from this session
    expect(screen.queryByTestId("tab-home")).toBeNull();
    fireEvent.click(screen.getByLabelText("Indietro"));
    await waitFor(() => expect(screen.getByTestId("home-section")).toBeInTheDocument());
  });

  it("a notification link to a question opens the conversation it belongs to", async () => {
    window.location.hash = "#request-3";
    setup();
    render(<App />);
    await waitFor(() => expect(window.location.hash).toBe("#/sessione/cx1"));
    await waitFor(() => expect(screen.getByTestId("request-card-3")).toHaveClass("card-highlight"));
  });

  it("a notification link to a settled request opens the history", async () => {
    window.location.hash = "#request-2";
    setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("history-card-2")).toBeInTheDocument());
    expect(screen.getByTestId("history-card-2")).toHaveClass("card-highlight");
    expect(screen.getByTestId("history-answer-2")).toHaveTextContent("Choice A");
  });

  it("switches between home and history", async () => {
    setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("home-section")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("tab-history"));
    await waitFor(() => expect(screen.getByTestId("history-card-2")).toBeInTheDocument());
    expect(screen.getByText("Which choice?")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tab-home"));
    await waitFor(() => expect(screen.getByTestId("home-section")).toBeInTheDocument());
  });

  it("follows session updates from the server", async () => {
    setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("session-row-new")).toBeInTheDocument());
    MockEventSource.last.emit("session_update", { ...sessions[1], state: "idle", activity_at: ago(0) });
    await waitFor(() => expect(screen.getByTestId("session-row-new")).toHaveTextContent("In attesa"));
    // A closed session the user follows stays, marked closed.
    MockEventSource.last.emit("session_update", { ...sessions[0], ended_at: ago(0) });
    await waitFor(() => expect(screen.getByTestId("session-row-old")).toHaveTextContent("Chiusa"));
  });
});

describe("writing from the PWA", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("posts the message to the session", async () => {
    const created = {
      id: 50, sender_id: "human", recipient_id: "codex", question_type: "message", prompt: "procedi pure",
      options: [], status: "APPROVED", created_at: ago(0), recipient_session_id: "cx1",
    };
    const fetchMock = setup([[(url, o) => url === "/api/sessions/cx1/messages" && o.method === "POST", created]]);
    window.location.hash = "#/sessione/cx1";
    render(<App />);
    await waitFor(() => expect(screen.getByText("prepara il deploy")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("composer-input"), { target: { value: "procedi pure" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(screen.getByTestId("outgoing-50")).toBeInTheDocument());
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/sessions/cx1/messages");
    expect(JSON.parse(call[1].body)).toEqual({ text: "procedi pure" });
  });

  it("posts selected attachments through the session composer", async () => {
    const created = { id: 51, sender_id: "human", recipient_id: "codex", question_type: "message",
      prompt: "Allegati: test.pdf", status: "APPROVED", created_at: ago(0), recipient_session_id: "cx1" };
    const fetchMock = setup([[(url, o) => url === "/api/sessions/cx1/messages" && o.method === "POST", created]]);
    window.location.hash = "#/sessione/cx1";
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("composer-input")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Scegli allegati"), { target: { files: [new File(["%PDF-1.4"], "test.pdf", {type: "application/pdf"})] } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(screen.getByTestId("outgoing-51")).toBeInTheDocument());
    const call = fetchMock.mock.calls.find(([url]) => url === "/api/sessions/cx1/messages");
    expect(JSON.parse(call[1].body)).toEqual({ text: "", attachments: [{ name: "test.pdf", data: btoa("%PDF-1.4") }] });
  });

  it("says why a message was refused", async () => {
    setup([[(url, o) => url === "/api/sessions/cx1/messages" && o.method === "POST",
      () => Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({ detail: "La sessione è chiusa." }) })]]);
    window.location.hash = "#/sessione/cx1";
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("composer-input")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("composer-input"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent("La sessione è chiusa."));
  });
});

describe("excluded projects", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("hides a session and its questions as soon as its project is excluded", async () => {
    setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("request-card-3")).toBeInTheDocument());
    MockEventSource.last.emit("session_update", { ...sessions[3], excluded: true });
    await waitFor(() => expect(screen.queryByTestId("request-card-3")).toBeNull());
    expect(screen.getByTestId("request-card-1")).toBeInTheDocument();
  });
});

describe("followed sessions by provider", () => {
  beforeEach(() => {
    window.location.hash = "";
    localStorage.clear();
  });

  it("groups them under headers that open and close, latest provider first", async () => {
    setup();
    render(<App />);
    const pinnedCodex = { ...sessions[3], pinned: true, activity_at: ago(0) };
    await waitFor(() => expect(screen.getByTestId("provider-group-claude-code")).toBeInTheDocument());
    MockEventSource.last.emit("session_update", pinnedCodex);
    await waitFor(() => expect(screen.getByTestId("provider-group-codex")).toBeInTheDocument());
    const order = screen.getAllByTestId(/^provider-group-/).map((g) => g.dataset.testid);
    expect(order).toEqual(["provider-group-codex", "provider-group-claude-code"]);
    expect(screen.getByTestId("provider-group-claude-code")).toHaveTextContent("2 · 1 al lavoro");
    expect(screen.getByTestId("session-row-new")).toHaveClass("prov-claude-code");

    fireEvent.click(screen.getByTestId("provider-toggle-claude-code"));
    expect(screen.queryByTestId("session-row-new")).toBeNull();
    expect(screen.getByTestId("provider-toggle-claude-code")).toHaveAttribute("aria-expanded", "false");
    expect(JSON.parse(localStorage.getItem("placet.collapsedProviders"))).toEqual(["claude-code"]);
    fireEvent.click(screen.getByTestId("provider-toggle-claude-code"));
    expect(screen.getByTestId("session-row-new")).toBeInTheDocument();
  });
});

describe("adaptive layout", () => {
  const original = window.matchMedia;
  const screenWide = (wide) => {
    window.matchMedia = (query) => ({
      matches: wide, media: query, addEventListener: () => {}, removeEventListener: () => {},
    });
  };
  beforeEach(() => {
    window.location.hash = "";
  });
  afterEach(() => {
    window.matchMedia = original;
  });

  it("on a wide screen keeps the sessions on the left and opens the conversation on the right", async () => {
    screenWide(true);
    setup();
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("pane-empty")).toBeInTheDocument());
    const sidebar = screen.getByTestId("sidebar");
    await waitFor(() => expect(within(sidebar).getByTestId("session-row-new")).toBeInTheDocument());
    fireEvent.click(within(sidebar).getByTestId("session-row-new"));
    await waitFor(() => expect(within(screen.getByTestId("pane")).getByTestId("session-view")).toBeInTheDocument());
    expect(within(sidebar).getByTestId("session-row-new")).toHaveClass("active");
    // The navigation stays available next to the conversation.
    expect(screen.getByTestId("tab-history")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tab-history"));
    await waitFor(() => expect(within(screen.getByTestId("pane")).getByTestId("history-section")).toBeInTheDocument());
    expect(within(sidebar).getByTestId("home-section")).toBeInTheDocument();
  });

  it("on the phone puts the navigation in a bottom bar, hidden inside a conversation", async () => {
    screenWide(false);
    setup();
    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByTestId("nav-tabs")).toBeInTheDocument());
    expect(container.firstChild).toHaveClass("with-bottom-bar");
    window.location.hash = "#/sessione/cx1";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    await waitFor(() => expect(screen.getByTestId("session-view")).toBeInTheDocument());
    expect(screen.queryByTestId("nav-tabs")).toBeNull();
    expect(container.firstChild).toHaveClass("in-session");
  });
});
