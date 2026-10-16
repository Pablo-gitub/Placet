import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import SessionView from "../SessionView";
import { json, mockFetch, paramsOf, transcriptOf } from "./testUtils";

const session = {
  id: "s1", agent_id: "claude", provider: "claude-code", entrypoint: "claude-desktop", cwd: "/p/lab",
  title: "Lab", state: "idle", reachable: true, activity_at: "2026-10-16T10:00:00Z",
  started_at: "2026-10-16T09:00:00Z", last_seen_at: "2026-10-16T10:00:00Z",
};
const action = (cursor, kind, detail, tool = "Bash") => ({ role: "action", kind, detail, tool, cursor, at: null });

const latest = {
  session_id: "s1", provider: "claude-code", before: 500, end: 900,
  messages: [
    { role: "user", text: "ciao", at: "2026-10-16T09:58:00Z", cursor: "500:0" },
    { role: "assistant", text: "**Ciao!**", at: "2026-10-16T09:58:10Z", cursor: "600:0" },
    action("610:0", "read", "/p/lab/README.md", "Read"),
    action("620:0", "command", "git status"),
    action("630:0", "mcp", "placet", "placet_reply"),
    { role: "placet", text: "[Placet Request #7 da codex]: rivedi il file", at: null, cursor: "700:0" },
    action("710:0", "search", "TODO", "Grep"),
    { role: "compact", text: "", at: null, cursor: "800:0" },
    { role: "command", text: "/compact", at: null, cursor: "850:0" },
    { role: "notice", text: "Error: stream interrupted", at: null, cursor: "860:0" },
  ],
};

function renderView(props = {}) {
  return render(
    <SessionView sessionId="s1" session={session} requests={[]} sessions={[session]} onBack={vi.fn()}
      onDecide={vi.fn()} {...props} />
  );
}

describe("SessionView", () => {
  it("shows the conversation: messages, grouped actions, Placet messages and markers", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    renderView();
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    expect(screen.getByTestId("chat-user")).toHaveTextContent("ciao");
    expect(screen.getByTestId("chat-assistant").querySelector("strong")).toHaveTextContent("Ciao!");
    const groups = screen.getAllByTestId("chat-actions");
    // Three consecutive actions collapse; a single one stays visible.
    expect(groups[0]).toHaveTextContent("3 azioni");
    expect(groups[0]).toHaveTextContent("placet_reply (placet)");
    fireEvent.click(groups[0].querySelector("button"));
    expect(groups[0]).toHaveTextContent("Legge README.md");
    expect(groups[0]).toHaveTextContent("$ git status");
    expect(groups[1]).toHaveTextContent("Cerca TODO");
    expect(screen.getByTestId("chat-placet")).toHaveTextContent("[Placet Request #7 da codex]");
    expect(screen.getByTestId("chat-compact")).toHaveTextContent("Conversazione compattata");
    expect(screen.getByTestId("chat-command")).toHaveTextContent("/compact");
    expect(screen.getByTestId("chat-notice")).toHaveTextContent("stream interrupted");
    expect(screen.getByTestId("session-view-name")).toHaveTextContent("Lab");
    expect(screen.getByTestId("session-view-state")).toHaveTextContent("In attesa");
  });

  it("loads older messages above", async () => {
    const fetchMock = mockFetch([
      [(url) => transcriptOf("s1")(url) && paramsOf(url).get("before") === "500",
        { ...latest, before: null, messages: [{ role: "user", text: "primo messaggio", at: null, cursor: "0:0" }] }],
      [transcriptOf("s1"), latest],
    ]);
    renderView();
    await waitFor(() => expect(screen.getByTestId("load-older")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("load-older"));
    await waitFor(() => expect(screen.getByText("primo messaggio")).toBeInTheDocument());
    const users = screen.getAllByTestId("chat-user").map((e) => e.textContent);
    expect(users[0]).toContain("primo messaggio");
    expect(screen.queryByTestId("load-older")).toBeNull();
    expect(screen.getByText("Inizio della conversazione")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => paramsOf(url).get("before") === "500")).toBe(true);
  });

  it("fetches only the new entries when the session moves", async () => {
    const fetchMock = mockFetch([
      [(url) => transcriptOf("s1")(url) && paramsOf(url).get("after") === "900",
        { ...latest, before: null, end: 950,
          messages: [{ role: "assistant", text: "nuova risposta", at: null, cursor: "900:0" }] }],
      [transcriptOf("s1"), latest],
    ]);
    const { rerender } = renderView();
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    rerender(
      <SessionView sessionId="s1" session={{ ...session, activity_at: "2026-10-16T10:05:00Z" }} requests={[]}
        sessions={[session]} onBack={vi.fn()} onDecide={vi.fn()} />
    );
    await waitFor(() => expect(screen.getByText("nuova risposta")).toBeInTheDocument());
    expect(screen.getAllByText("ciao")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => !paramsOf(url).has("after"))).toHaveLength(1);
  });

  it("says when the PC does not answer, and retries", async () => {
    let fail = true;
    mockFetch([[transcriptOf("s1"), () =>
      fail ? json({ detail: "Il PC non risponde: il bridge non è attivo." }, 503) : json(latest)]]);
    renderView();
    await waitFor(() => expect(screen.getByTestId("chat-error")).toHaveTextContent("Il PC non risponde"));
    fail = false;
    fireEvent.click(screen.getByText("Riprova"));
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
  });

  it("puts the session's open questions at the bottom of the conversation", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const onDecide = vi.fn();
    const question = {
      id: 9, sender_id: "claude", recipient_id: "human", question_type: "confirmation",
      prompt: "Procedo?", options: [], status: "PENDING", created_at: "2026-10-16T10:00:00Z", sender_session_id: "s1",
    };
    const elsewhere = { ...question, id: 10, sender_session_id: "s2" };
    renderView({ requests: [question, elsewhere], onDecide });
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    expect(screen.getByTestId("request-card-9")).toBeInTheDocument();
    expect(screen.queryByTestId("request-card-10")).toBeNull();
    fireEvent.click(screen.getByTestId("btn-approve-9"));
    expect(onDecide).toHaveBeenCalledWith(9, { status: "APPROVED", selected_options: [] });
  });
});
