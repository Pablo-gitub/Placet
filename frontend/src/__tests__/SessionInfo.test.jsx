import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import RequestCard from "../RequestCard";
import { actionLabel, groupEntries, mergeEntries } from "../chatEntries";
import { byActivity, providerOf, sessionLabels, sessionStatus } from "../sessionInfo";

const now = new Date().toISOString();
const sessions = [
  { id: "s1", agent_id: "claude", cwd: "/home/p/Projects/placet", title: null, entrypoint: "claude-desktop",
    state: "busy", reachable: true, last_seen_at: now, started_at: now },
  { id: "s2", agent_id: "claude", cwd: "/home/p/Projects/social", title: "Post Instagram", entrypoint: "claude-desktop",
    state: "idle", reachable: true, last_seen_at: now, started_at: now },
  { id: "s3", agent_id: "claude", cwd: "/home/p/x", entrypoint: "claude-desktop",
    state: "idle", reachable: false, last_seen_at: now, started_at: now },
];

describe("session helpers", () => {
  it("names the provider, also for old sessions without one", () => {
    expect(providerOf({ provider: "codex", agent_id: "codex" }).label).toBe("Codex");
    expect(providerOf({ agent_id: "gemini" }).label).toBe("Antigravity");
    expect(providerOf({ provider: "ollama", agent_id: "ollama" }).label).toBe("ollama");
  });

  it("tells the state of a session", () => {
    expect(sessionStatus(sessions[0]).label).toBe("Al lavoro");
    expect(sessionStatus(sessions[1]).label).toBe("In attesa");
    expect(sessionStatus(sessions[2]).label).toBe("Non raggiungibile");
    expect(sessionStatus({ ...sessions[1], ended_at: now }).label).toBe("Chiusa");
  });

  it("orders by activity", () => {
    const a = { activity_at: "2026-10-16T10:00:00Z" };
    const b = { activity_at: "2026-10-16T11:00:00Z" };
    expect([a, b].sort(byActivity)).toEqual([b, a]);
  });
});

describe("conversation entries", () => {
  it("describes tool calls in one line", () => {
    expect(actionLabel({ kind: "command", detail: "ls -la" })).toBe("$ ls -la");
    expect(actionLabel({ kind: "read", detail: "/a/b/c.py" })).toBe("Legge c.py");
    expect(actionLabel({ kind: "mcp", tool: "placet_reply", detail: "placet" })).toBe("placet_reply (placet)");
    expect(actionLabel({ kind: "other", tool: "schedule", detail: "Wait" })).toBe("schedule: Wait");
    expect(actionLabel({ kind: "other", tool: "Foo" })).toBe("Foo");
  });

  it("groups consecutive actions and merges pages without duplicates", () => {
    const e = (cursor, role) => ({ cursor, role });
    const items = groupEntries([e("1", "user"), e("2", "action"), e("3", "action"), e("4", "assistant"), e("5", "action")]);
    expect(items.map((i) => (i.type === "actions" ? i.entries.length : i.entry.role))).toEqual(["user", 2, "assistant", 1]);
    expect(mergeEntries([e("1"), e("2")], [e("2"), e("3")]).map((x) => x.cursor)).toEqual(["1", "2", "3"]);
  });
});

describe("choosing the recipient session", () => {
  const req = {
    id: 60, sender_id: "codex", recipient_id: "claude", question_type: "prompt_review",
    prompt: "Rivedi", options: [], status: "PENDING", created_at: now,
  };

  it("sends the chosen session with the approval", () => {
    const onDecide = vi.fn();
    render(<RequestCard req={req} onDecide={onDecide} sessions={sessions} />);
    fireEvent.change(screen.getByTestId("target-session-60"), { target: { value: "s2" } });
    fireEvent.click(screen.getByTestId("btn-approve-60"));
    expect(onDecide).toHaveBeenCalledWith(60, {
      status: "APPROVED", selected_options: [], recipient_session_id: "s2",
    });
  });

  it("leaves the choice to Placet by default and hides the selector without sessions", () => {
    const onDecide = vi.fn();
    const { unmount } = render(<RequestCard req={req} onDecide={onDecide} sessions={sessions} />);
    fireEvent.click(screen.getByTestId("btn-approve-60"));
    expect(onDecide).toHaveBeenCalledWith(60, { status: "APPROVED", selected_options: [] });
    unmount();
    render(<RequestCard req={req} onDecide={vi.fn()} sessions={[]} />);
    expect(screen.queryByTestId("target-session-60")).toBeNull();
  });
});

describe("request origin", () => {
  it("shows the project and client of the sender session", () => {
    const req = {
      id: 70, sender_id: "claude", recipient_id: "human", question_type: "confirmation",
      prompt: "Procedo?", options: [], status: "PENDING", created_at: now, sender_session_id: "s2",
    };
    render(<RequestCard req={req} onDecide={vi.fn()} sessions={sessions} />);
    expect(screen.getByTestId("origin-70")).toHaveTextContent("da claude · social · app desktop · Post Instagram");
  });
});

describe("sessionLabels disambiguation", () => {
  it("disambiguates sessions that share the same title", () => {
    const list = [
      { id: "11111111-aaaa-bbbb-cccc-dddddddddddd", title: "Primo sviluppatore", cwd: "/p/placet" },
      { id: "22222222-aaaa-bbbb-cccc-dddddddddddd", title: "Primo sviluppatore", cwd: "/p/placet" },
      { id: "33333333-aaaa-bbbb-cccc-dddddddddddd", title: "Secondo sviluppatore", cwd: "/p/other" },
    ];
    const labels = sessionLabels(list);
    expect(labels["11111111-aaaa-bbbb-cccc-dddddddddddd"]).toBe("Primo sviluppatore [11111111]");
    expect(labels["22222222-aaaa-bbbb-cccc-dddddddddddd"]).toBe("Primo sviluppatore [22222222]");
    expect(labels["33333333-aaaa-bbbb-cccc-dddddddddddd"]).toBe("Secondo sviluppatore");
  });
});


describe("project of a session working in a worktree", () => {
  it("is the repository, not the worktree folder", async () => {
    const { sessionProject } = await import("../sessionInfo");
    expect(sessionProject({ cwd: "/home/p/placet/.worktrees/rf29d", project_root: "/home/p/placet" })).toBe("placet");
    expect(sessionProject({ cwd: "/home/p/placet" })).toBe("placet");        // older sessions: the cwd
    expect(sessionProject({})).toBeNull();
  });
});
