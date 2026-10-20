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

describe("project hash mentions (#)", () => {
  it("opens # mention picker for session's node projects and removes token on selection", async () => {
    const fetch = vi.fn((url) => {
      if (url === "/api/projects?node_id=101") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { id: 1, name: "Placet Core" },
            { id: 2, name: "Design System" },
          ]),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetch);

    const nodeSession = { ...session, node_id: 101 };
    const onSelectProject = vi.fn();
    const send = vi.fn().mockResolvedValue(true);

    render(<Composer session={nodeSession} sessions={sessions} onSend={send} onSelectProject={onSelectProject} />);

    type("Lavoriamo su #");
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    expect(screen.getByText("Placet Core")).toBeInTheDocument();
    expect(screen.getByText("Design System")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Lavoriamo su #Placet", selectionStart: 20 } });
    expect(screen.queryByText("Design System")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Placet Core"));

    expect(onSelectProject).toHaveBeenCalledWith(1);
    expect(screen.getByRole("textbox").value).toBe("Lavoriamo su ");
  });

  it("shows note when session has no node or no projects on # mention", async () => {
    const sessionWithoutNode = { ...session, node_id: null };
    render(<Composer session={sessionWithoutNode} sessions={sessions} onSend={vi.fn()} />);

    type("Test #");
    expect(await screen.findByText("Nessun progetto per questa sessione")).toBeInTheDocument();
  });
});

describe("unsent drafts", () => {
  it("resumes the unsent text after leaving the chat and forgets it once sent", async () => {
    localStorage.clear();
    const send = vi.fn().mockResolvedValue(true);
    const first = render(<Composer session={session} sessions={sessions} onSend={send} draftKey="chat:current" />);
    type("Prompt a metà");
    first.unmount();
    render(<Composer session={session} sessions={sessions} onSend={send} draftKey="chat:current" />);
    expect(screen.getByRole("textbox")).toHaveValue("Prompt a metà");
    fireEvent.click(screen.getByRole("button", { name: "Invia" }));
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
    expect(localStorage.getItem("placet.draft.chat:current")).toBeNull();
  });
  it("does not leak a draft into another chat", () => {
    localStorage.clear();
    const first = render(<Composer session={session} sessions={sessions} onSend={vi.fn()} draftKey="chat:current" />);
    type("Solo per questa chat");
    first.unmount();
    render(<Composer session={session} sessions={sessions} onSend={vi.fn()} draftKey="chat:other" />);
    expect(screen.getByRole("textbox")).toHaveValue("");
  });
});
