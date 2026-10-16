import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import Composer from "../Composer";

const session = { id: "current", agent_id: "codex", reachable: true };
const sessions = [session,
  { id: "gemini-1", agent_id: "gemini", title: "ciao", cwd: "/projects/lab", reachable: true },
  { id: "claude-1", agent_id: "claude", title: "bello", cwd: "/projects/lab", reachable: true },
  { id: "closed", agent_id: "gemini", title: "chiusa", ended_at: "2026-01-01" },
];
function type(value) { fireEvent.change(screen.getByRole("textbox"), { target: { value, selectionStart: value.length } }); }

describe("session mentions", () => {
  it("selects multiple sessions and sends references to the current conversation", async () => {
    const send = vi.fn().mockResolvedValue(true);
    render(<Composer session={session} sessions={sessions} onSend={send} />);
    type("Chiedi a @gemini");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    fireEvent.click(screen.getByRole("option"));
    const first = screen.getByRole("textbox").value;
    type(first + "di fare una revisione e a @claude");
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    type(screen.getByRole("textbox").value + "di preparare i test rossi");
    fireEvent.click(screen.getByRole("button", { name: "Invia" }));
    await waitFor(() => expect(send).toHaveBeenCalledOnce());
    expect(send.mock.calls[0][1].map((m) => m.session_id)).toEqual(["gemini-1", "claude-1"]);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
  });
  it("keeps the draft on failure and removes deleted references", async () => {
    const send = vi.fn().mockResolvedValue(false);
    render(<Composer session={session} sessions={sessions} onSend={send} />);
    type("@gemini"); fireEvent.click(screen.getByRole("option"));
    fireEvent.click(screen.getByRole("button", { name: "Invia" }));
    await waitFor(() => expect(send).toHaveBeenCalledOnce());
    expect(screen.getByRole("textbox").value).toContain("ciao");
    type("Una domanda normale");
    fireEvent.click(screen.getByRole("button", { name: "Invia" }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1][1]).toEqual([]);
  });
  it("excludes current and closed sessions and closes the picker with Escape", () => {
    render(<Composer session={session} sessions={sessions} onSend={vi.fn()} />);
    type("@ ");
    expect(screen.getAllByRole("option")).toHaveLength(2);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
