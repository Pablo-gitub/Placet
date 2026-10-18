import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import SessionView from "../SessionView";
import { splitPrompt } from "../draft";
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
      fail ? json({ code: "bridge.offline", detail: "The bridge is offline." }, 503) : json(latest)]]);
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

describe("writing to a session", () => {
  const message = (id, status) => ({
    id, sender_id: "human", recipient_id: "claude", question_type: "message", prompt: `messaggio ${id}`,
    options: [], status, created_at: "2026-10-16T10:00:00Z", recipient_session_id: "s1",
  });

  it("sends the text and shows the messages still on their way", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const onSend = vi.fn(() => Promise.resolve(true));
    renderView({
      onSend,
      requests: [message(1, "APPROVED"), message(2, "DELIVERING"), message(3, "EXECUTING"),
        message(4, "DELIVERY_FAILED"), { ...message(5, "APPROVED"), recipient_session_id: "s2" }],
    });
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    expect(screen.getByTestId("outgoing-1")).toHaveTextContent("In attesa che la sessione sia libera");
    expect(screen.getByTestId("outgoing-2")).toHaveTextContent("In consegna");
    expect(screen.queryByTestId("outgoing-3")).toBeNull(); // delivered: it is in the conversation
    expect(screen.getByTestId("outgoing-4")).toHaveClass("failed");
    expect(screen.queryByTestId("outgoing-5")).toBeNull(); // another session

    const input = screen.getByTestId("composer-input");
    expect(screen.getByTestId("composer-send")).toBeDisabled();
    fireEvent.change(input, { target: { value: "  a che punto sei?  " } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("s1", "a che punto sei?"));
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("keeps the text when sending fails", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    renderView({ onSend: () => Promise.resolve(false) });
    const input = screen.getByTestId("composer-input");
    fireEvent.change(input, { target: { value: "importante" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(screen.getByTestId("composer-send")).not.toBeDisabled());
    expect(input.value).toBe("importante");
  });

  it("explains when the session is closed, busy or unreachable", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const { rerender } = renderView({ onSend: vi.fn(), session: { ...session, ended_at: "2026-10-16T11:00:00Z" } });
    expect(screen.getByTestId("composer-input")).toBeDisabled();
    expect(screen.getByTestId("composer")).toHaveTextContent("chiusa");
    rerender(<SessionView sessionId="s1" session={{ ...session, reachable: false }} requests={[]} sessions={[]}
      onBack={vi.fn()} onDecide={vi.fn()} onSend={vi.fn()} />);
    expect(screen.getByTestId("composer-unreachable")).toBeInTheDocument();
    rerender(<SessionView sessionId="s1" session={{ ...session, state: "busy" }} requests={[]} sessions={[]}
      onBack={vi.fn()} onDecide={vi.fn()} onSend={vi.fn()} />);
    expect(screen.getByTestId("composer")).toHaveTextContent("a fine turno");
  });

  it("marks the user's messages that came through Placet", async () => {
    mockFetch([[transcriptOf("s1"), { ...latest, messages: [
      { role: "user", text: "dal telefono!", at: null, cursor: "1:0", via: "placet" }] }]]);
    renderView();
    await waitFor(() => expect(screen.getByTestId("chat-user")).toHaveTextContent("dal telefono"));
  });
});

describe("uncertain deliveries", () => {
  const message = (id, status, delivery) => ({
    id, sender_id: "human", recipient_id: "claude", question_type: "message", prompt: `messaggio ${id}`,
    options: [], status, delivery, created_at: "2026-10-16T10:00:00Z", recipient_session_id: "s1",
  });

  it("offers Riprova and Annulla on an uncertain delivery, Annulla on a waiting one", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const onDelivery = vi.fn();
    renderView({ onDelivery, requests: [message(1, "DELIVERING", "uncertain"), message(2, "APPROVED", "waiting"),
      message(3, "DELIVERING", "in_progress")] });
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    expect(screen.getByTestId("outgoing-1")).toHaveTextContent("Consegna incerta");
    expect(screen.getByTestId("outgoing-1")).toHaveClass("uncertain");
    fireEvent.click(screen.getByTestId("retry-1"));
    expect(onDelivery).toHaveBeenCalledWith(1, "retry");
    expect(screen.queryByTestId("retry-2")).toBeNull();
    fireEvent.click(screen.getByTestId("cancel-2"));
    expect(onDelivery).toHaveBeenCalledWith(2, "cancel");
    expect(screen.queryByTestId("cancel-3")).toBeNull();
  });
});

describe("a delivered request whose end Placet cannot see", () => {
  const delivered = {
    id: 20, sender_id: "human", recipient_id: "claude", question_type: "message", prompt: "lungo",
    options: [], status: "EXECUTING", delivery: "accepted", execution: "unobserved",
    start_unconfirmed_at: "2026-10-16T10:02:00Z", created_at: "2026-10-16T10:00:00Z", recipient_session_id: "s1",
  };
  const waiting = { ...delivered, id: 21, prompt: "dopo", status: "APPROVED", delivery: "waiting",
    start_unconfirmed_at: null };

  it("says the next messages wait, and lets the user move the queue on", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const onDelivery = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderView({ onDelivery, requests: [delivered, waiting] });
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    expect(screen.getByTestId("queue-hold-20")).toHaveTextContent("non riesce a verificare l'esecuzione");
    expect(screen.getByTestId("queue-hold-20")).toHaveTextContent("restano in attesa");
    fireEvent.click(screen.getByTestId("finished-20"));
    expect(onDelivery).toHaveBeenCalledWith(20, "finished");
    fireEvent.click(screen.getByTestId("queue-next-20"));
    expect(onDelivery).toHaveBeenCalledWith(20, "queue_next");
    fireEvent.click(screen.getByTestId("cancel-20"));
    expect(onDelivery).toHaveBeenCalledWith(20, "cancel");
  });

  it("stays quiet while the start is still within its time, or once released", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    renderView({ onDelivery: vi.fn(), requests: [
      { ...delivered, start_unconfirmed_at: null },
      { ...delivered, id: 22, queue_released_at: "2026-10-16T10:05:00Z" },
    ] });
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    expect(screen.queryByTestId(/^queue-hold-/)).toBeNull();
  });
});

describe("images in the conversation", () => {
  const withImages = {
    ...latest, messages: [
      { role: "user", text: "guarda", at: null, cursor: "10:0", attachments: [{ id: "10:0:0", name: "immagine" }] },
      { role: "media", text: "Immagine generata", at: null, cursor: "20:0",
        attachments: [{ id: "20:0:0", name: "tramonto.jpg" }] },
    ],
  };

  it("shows thumbnails by id and opens them full screen with a download", async () => {
    mockFetch([[transcriptOf("s1"), withImages]]);
    renderView();
    await waitFor(() => expect(screen.getByTestId("attachment-10:0:0")).toBeInTheDocument());
    const img = screen.getByTestId("attachment-20:0:0").querySelector("img");
    expect(img.getAttribute("src")).toBe("/api/sessions/s1/attachments/20%3A0%3A0");
    expect(screen.getByTestId("chat-media")).toHaveTextContent("Immagine generata");
    fireEvent.click(screen.getByTestId("attachment-20:0:0"));
    expect(screen.getByTestId("attachment-viewer")).toBeInTheDocument();
    expect(screen.getByTestId("attachment-download")).toHaveAttribute("download", "tramonto.jpg");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("attachment-viewer")).toBeNull();
  });

  it("says when an image cannot be read", async () => {
    mockFetch([[transcriptOf("s1"), withImages]]);
    renderView();
    await waitFor(() => expect(screen.getByTestId("attachment-10:0:0")).toBeInTheDocument());
    fireEvent.error(screen.getByTestId("attachment-10:0:0").querySelector("img"));
    expect(screen.getByTestId("attachment-missing-10:0:0")).toHaveTextContent("PC non raggiungibile");
  });
});

describe("files an agent shares", () => {
  it("shows a PDF as a card that opens in Placet's viewer", async () => {
    mockFetch([[transcriptOf("s1"), { ...latest, messages: [
      { role: "media", text: "File condiviso", at: null, cursor: "30:0",
        attachments: [{ id: "30:0:0", name: "Rapporto.pdf", kind: "pdf" }] }] }]]);
    renderView();
    await waitFor(() => expect(screen.getByTestId("attachment-30:0:0")).toHaveTextContent("Rapporto.pdf"));
    expect(screen.getByTestId("chat-media")).toHaveTextContent("File condiviso");
    fireEvent.click(screen.getByTestId("attachment-open-30:0:0"));
    // Placet's own viewer (white pages), loaded on demand.
    await waitFor(() => expect(screen.getByTestId("pdf-viewer")).toBeInTheDocument());
    expect(within(screen.getByTestId("pdf-viewer")).getByLabelText("Scarica")).toHaveAttribute("href", "/api/sessions/s1/attachments/30%3A0%3A0");
  });
});

describe("Subito", () => {
  it("lets the user put a message into the running turn, and remembers the choice", async () => {
    localStorage.clear();
    mockFetch([[transcriptOf("s1"), latest]]);
    const onSend = vi.fn(() => Promise.resolve(true));
    renderView({ onSend, session: { ...session, state: "busy" } });
    expect(screen.getByTestId("mode-queue")).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByTestId("mode-now"));
    expect(screen.getByTestId("send-mode")).toHaveTextContent("dopo la prossima azione");
    fireEvent.change(screen.getByTestId("composer-input"), { target: { value: "fermati" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("s1", "fermati", [], [], "now"));
    expect(localStorage.getItem("placet.sendMode")).toBe("now");
  });

  it("is not offered for Codex yet, nor when the session is idle", async () => {
    localStorage.setItem("placet.sendMode", "now");
    mockFetch([[transcriptOf("s1"), latest]]);
    const onSend = vi.fn(() => Promise.resolve(true));
    const { rerender } = renderView({ onSend, session: { ...session, provider: "codex", state: "busy" } });
    expect(screen.getByTestId("mode-now")).toBeDisabled();
    expect(screen.getByTestId("send-mode")).toHaveTextContent("non ancora disponibile per Codex");
    fireEvent.change(screen.getByTestId("composer-input"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(onSend).toHaveBeenCalledWith("s1", "x"));
    rerender(<SessionView sessionId="s1" session={session} requests={[]} sessions={[session]} onBack={vi.fn()}
      onDecide={vi.fn()} onSend={onSend} />);
    expect(screen.queryByTestId("send-mode")).toBeNull();
    localStorage.clear();
  });

  it("shows a now-message as entering after the next action", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    renderView({ requests: [{ id: 70, sender_id: "human", recipient_id: "claude", question_type: "message",
      prompt: "fermati", options: [], status: "DELIVERING", delivery: "in_progress", delivery_mode: "now",
      created_at: "2026-10-16T10:00:00Z", recipient_session_id: "s1" }] });
    await waitFor(() => expect(screen.getByTestId("outgoing-70")).toHaveTextContent("dopo la prossima azione"));
  });
});

describe("suspended sessions and messages still in Placet", () => {
  const codex = { ...session, id: "s1", agent_id: "codex", provider: "codex", reachable: false,
    suspended_at: "2026-10-17T02:24:42Z" };
  const message = (id, status, extra = {}) => ({
    id, sender_id: "human", recipient_id: "codex", question_type: "message", prompt: `messaggio ${id}`,
    options: [], status, created_at: "2026-10-16T10:00:00Z", recipient_session_id: "s1", ...extra,
  });

  it("says a message to a suspended session waits for the app, and one queued in Codex cannot be withdrawn", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    renderView({ session: codex, onSend: vi.fn(), onDelivery: vi.fn(),
      requests: [message(1, "APPROVED"), message(2, "EXECUTING", { execution: "unobserved" }),
        message(3, "EXECUTING", { execution: "started" })] });
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    expect(screen.getByTestId("composer-suspended")).toBeInTheDocument();
    expect(screen.getByTestId("outgoing-1")).toHaveTextContent("riapertura in Codex");
    expect(screen.getByTestId("outgoing-2")).toHaveTextContent("Accodato in Codex");
    expect(screen.queryByTestId("cancel-2")).toBeNull();
    expect(screen.queryByTestId("outgoing-3")).toBeNull(); // its turn started: it is in the conversation
  });

  it("allows writing to a suspended Claude desktop conversation", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    renderView({ session: { ...codex, provider: "claude-code", agent_id: "claude",
      entrypoint: "claude-desktop" }, onSend: vi.fn(),
      requests: [message(1, "APPROVED")] });
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    expect(screen.getByTestId("composer-suspended")).toHaveTextContent("Claude ha sospeso");
    expect(screen.getByTestId("outgoing-1")).toHaveTextContent("riaprire la conversazione in Claude");
    expect(screen.getByPlaceholderText(/Scrivi alla sessione/)).not.toBeDisabled();
  });

  it("Modifica withdraws the message and puts it back in the composer", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const onDelivery = vi.fn(() => Promise.resolve(true));
    const other = { ...session, id: "abcdef12-0000", title: "Altra" };
    const token = "@Claude · lab · Altra [abcdef12]";
    renderView({ onSend: vi.fn(), onDelivery, sessions: [session, other],
      requests: [message(4, "APPROVED", { prompt: `chiedi a ${token} di rivedere\n\nAllegati: foto.png, nota.pdf` })] });
    await waitFor(() => expect(screen.getByText("ciao")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("edit-4"));
    expect(onDelivery).toHaveBeenCalledWith(4, "cancel");
    await waitFor(() => expect(screen.getByTestId("composer-input")).toHaveValue(`chiedi a ${token} di rivedere`));
    expect(screen.getByTestId("composer-refiles")).toHaveTextContent("foto.png, nota.pdf");
  });

  it("splitPrompt keeps text, mentions and attachment names apart", () => {
    const sessions = [{ id: "abcdef12-0000" }];
    expect(splitPrompt("Leggi gli allegati che ti ho inviato.\n\nAllegati: a.png", sessions))
      .toEqual({ text: "", mentions: [], files: ["a.png"] });
    expect(splitPrompt("vedi @X · p · T [abcdef12] ora", sessions).mentions)
      .toEqual([{ session_id: "abcdef12-0000", token: "@X · p · T [abcdef12]" }]);
  });
});

describe("provider at its usage limit", () => {
  it("says the message waits until the reset", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const codex = { ...session, agent_id: "codex", provider: "codex" };
    const reset = new Date(Date.now() + 30 * 60000).toISOString();
    const usage = [{ provider: "codex", buckets: [{ id: "5h", group: null, window: "5h", used_percent: 100, resets_at: reset }] }];
    renderView({ session: codex, usage, onSend: vi.fn(), onDelivery: vi.fn(), requests: [{
      id: 9, sender_id: "human", recipient_id: "codex", question_type: "message", prompt: "ciao",
      options: [], status: "APPROVED", created_at: "2026-10-16T10:00:00Z", recipient_session_id: "s1" }] });
    await waitFor(() => expect(screen.getByTestId("outgoing-9")).toHaveTextContent(/usage limit|limite d'uso/));
    expect(screen.getByTestId("composer-limited")).toBeInTheDocument();
  });
});

describe("model and context", () => {
  it("shows the model, effort and context of the session", async () => {
    mockFetch([[transcriptOf("s1"), { ...latest, meta: {
      model: "gpt-6.1-sol", effort: "low", context_tokens: 123558, context_window: 475000 } }]]);
    renderView();
    await waitFor(() => expect(screen.getByTestId("session-model")).toHaveTextContent("gpt-6.1-sol"));
    expect(screen.getByTestId("session-model")).toHaveTextContent(/effort low/);
    expect(screen.getByTestId("session-model")).toHaveTextContent(/26%/);
  });

  it("uses the model the hooks reported when the conversation has none", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    renderView({ session: { ...session, provider: "antigravity", model: "gemini-3.8-flash-medium" } });
    await waitFor(() => expect(screen.getByTestId("session-model")).toHaveTextContent("gemini-3.8-flash-medium"));
  });
});
