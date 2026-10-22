import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import Home from "../Home";
import SessionView from "../SessionView";
import WakeNotice from "../WakeNotice";
import i18n from "../i18n";
import { sessionStatus } from "../sessionInfo";
import { mockFetch, transcriptOf } from "./testUtils";

const session = (over = {}) => ({
  id: "s1", agent_id: "claude", provider: "claude-code", entrypoint: "claude-desktop", cwd: "/p/lab",
  title: "Lab", state: "idle", reachable: false, pinned: true, activity_at: "2026-10-22T10:00:00Z",
  started_at: "2026-10-22T09:00:00Z", last_seen_at: "2026-10-22T10:00:00Z", desktop_archived: true, ...over,
});

describe("a conversation archived in Claude Desktop", () => {
  it("is shown as archived instead of waiting, unless it is closed or suspended", () => {
    expect(sessionStatus(session()).label).toBe(i18n.t("sessions.archivedInClaude"));
    expect(sessionStatus(session()).key).toBe("archived");
    expect(sessionStatus(session({ reachable: true })).key).toBe("archived");
    expect(sessionStatus(session({ desktop_archived: false })).label).toBe(i18n.t("sessions.unreachable"));
    expect(sessionStatus(session({ ended_at: "2026-10-22T11:00:00Z" })).label).toBe(i18n.t("sessions.closed"));
    expect(sessionStatus(session({ entrypoint: "cli" })).key).not.toBe("archived");    // only Claude Desktop
  });

  it("says so in the Home row", () => {
    render(<Home requests={[]} sessions={[session()]} usage={null} onDecide={vi.fn()} onOpenSession={vi.fn()} onTogglePin={vi.fn()} />);
    expect(screen.getByTestId("session-row-s1")).toHaveTextContent(i18n.t("sessions.archivedInClaude"));
  });

  it("says so in the session header and tells how to use it, the message staying put", async () => {
    mockFetch([[transcriptOf("s1"), { session_id: "s1", provider: "claude-code", before: 0, end: 0, messages: [] }]]);
    render(<SessionView sessionId="s1" session={session()} requests={[]} sessions={[session()]}
      onBack={vi.fn()} onDecide={vi.fn()} onSend={vi.fn()} />);
    expect(screen.getByTestId("session-view-state")).toHaveTextContent(i18n.t("sessions.archivedInClaude"));
    expect(screen.getByTestId("archived-note")).toHaveTextContent(i18n.t("archived.note"));
    expect(i18n.getFixedT("it")("archived.note")).toBe("Ripristinala nell'app Claude per usarla; il messaggio resterà in attesa");
  });

  it("shows no note for a session that is not archived", async () => {
    mockFetch([[transcriptOf("s1"), { session_id: "s1", provider: "claude-code", before: 0, end: 0, messages: [] }]]);
    render(<SessionView sessionId="s1" session={session({ desktop_archived: false })} requests={[]} sessions={[session()]}
      onBack={vi.fn()} onDecide={vi.fn()} onSend={vi.fn()} />);
    expect(screen.queryByTestId("archived-note")).toBeNull();
  });

  it("makes the wake notice use the archived text, without a retry button", () => {
    render(<WakeNotice session={session({ wake_state: "wake_unconfirmed", wake_at: "2026-10-22T10:05:00Z" })} />);
    const notice = screen.getByTestId("wake-notice");
    expect(notice).toHaveTextContent(i18n.t("archived.note"));
    expect(notice).not.toHaveTextContent(i18n.t("wake.unconfirmed"));
    expect(screen.queryByTestId("wake-retry")).toBeNull();
  });

  it("has both languages", () => {
    for (const lng of ["en", "it"]) {
      for (const key of ["sessions.archivedInClaude", "archived.note", "errors.desktopArchived"]) {
        expect(i18n.getFixedT(lng)(key)).not.toBe(key);
      }
    }
    expect(i18n.getFixedT("it")("sessions.archivedInClaude")).toBe("Archiviata in Claude");
  });
});
