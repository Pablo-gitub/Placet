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

it("sends a file without text and keeps it after a failed transfer", async () => {
  const send = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  render(<Composer session={session} onSend={send} />);
  fireEvent.change(screen.getByLabelText("Scegli allegati"), { target: { files: [new File(["%PDF-1.4"], "test.pdf", { type: "application/pdf" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Invia" }));
  await waitFor(() => expect(send).toHaveBeenCalledOnce());
  expect(send.mock.calls[0][2]).toEqual([{ name: "test.pdf", data: btoa("%PDF-1.4") }]);
  await waitFor(() => expect(screen.getByRole("button", { name: "Invia" })).toBeEnabled());
  expect(screen.getByRole("button", { name: "Rimuovi test.pdf" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Invia" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Rimuovi test.pdf" })).not.toBeInTheDocument());
});

it("rejects unsupported files and allows removal", () => {
  render(<Composer session={session} onSend={vi.fn()} />);
  const choose = (files) => fireEvent.change(screen.getByLabelText("Scegli allegati"), { target: { files } });
  choose([new File(["x"], "x.html", { type: "text/html" })]);
  expect(screen.getByRole("alert")).toHaveTextContent("JPEG");
  choose([new File(["x"], "ok.pdf", { type: "application/pdf" })]);
  fireEvent.click(screen.getByRole("button", { name: "Rimuovi ok.pdf" }));
  expect(screen.getByRole("button", { name: "Invia" })).toBeDisabled();
});
