import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import Sessions from "../Sessions";
import RequestCard from "../RequestCard";

const now = new Date().toISOString();
const sessions = [
  { id: "s1", agent_id: "claude", cwd: "/home/p/Projects/placet", title: null, entrypoint: "claude-desktop",
    state: "busy", reachable: true, last_seen_at: now, started_at: now },
  { id: "s2", agent_id: "claude", cwd: "/home/p/Projects/social", title: "Post Instagram", entrypoint: "claude-desktop",
    state: "idle", reachable: true, last_seen_at: now, started_at: now },
  { id: "s3", agent_id: "claude", cwd: "/home/p/x", entrypoint: "claude-desktop",
    state: "idle", reachable: false, last_seen_at: now, started_at: now },
];

describe("Sessions", () => {
  it("groups sessions by agent with their state", () => {
    render(<Sessions sessions={sessions} agents={{ claude: { name: "Claude Code" }, codex: { name: "Codex" } }} />);
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByTestId("session-s1")).toHaveTextContent("placet");
    expect(screen.getByTestId("session-s2")).toHaveTextContent("Post Instagram");
    expect(screen.getByTestId("session-state-s1")).toHaveTextContent("Al lavoro");
    expect(screen.getByTestId("session-state-s2")).toHaveTextContent("In attesa");
    expect(screen.getByTestId("session-state-s3")).toHaveTextContent("Non raggiungibile");
    expect(screen.getByText("Nessuna sessione aperta.")).toBeInTheDocument(); // codex
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
