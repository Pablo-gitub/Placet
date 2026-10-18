import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import RequestCard from "../RequestCard";
import App from "../App";
import { requestIdFromHash } from "../decisions";

const base = {
  sender_id: "claude",
  recipient_id: "human",
  context: null,
  status: "PENDING",
  created_at: new Date().toISOString(),
};
const single = { ...base, id: 10, question_type: "single_choice", prompt: "Quale canale?", options: ["Instagram", "LinkedIn"] };
const multi = { ...base, id: 11, question_type: "multi_choice", prompt: "Quali tag?", options: ["ai", "dev"] };
const confirm = { ...base, id: 12, question_type: "confirmation", prompt: "Pubblico il post?", options: [] };

function renderCard(req) {
  const onDecide = vi.fn();
  render(<RequestCard req={req} submitting={false} highlighted={false} onDecide={onDecide} />);
  return onDecide;
}

describe("RequestCard", () => {
  it("shows a human question as read-only text, not an editable prompt", () => {
    renderCard(confirm);
    expect(screen.getByTestId("question-12")).toHaveTextContent("Pubblico il post?");
    expect(screen.queryByTestId("prompt-textarea-12")).toBeNull();
  });

  it("single choice: radios plus Altro, approve disabled until answered", () => {
    const onDecide = renderCard(single);
    expect(screen.getByTestId("option-10-Instagram")).toHaveAttribute("type", "radio");
    expect(screen.getByTestId("option-10-altro")).toBeInTheDocument();
    expect(screen.getByTestId("btn-approve-10")).toBeDisabled();

    fireEvent.click(screen.getByTestId("option-10-LinkedIn"));
    fireEvent.click(screen.getByTestId("btn-approve-10"));
    expect(onDecide).toHaveBeenCalledWith(10, { status: "APPROVED", selected_options: ["LinkedIn"] });
  });

  it("single choice: Altro replaces the options and requires text", () => {
    const onDecide = renderCard(single);
    fireEvent.click(screen.getByTestId("option-10-Instagram"));
    fireEvent.click(screen.getByTestId("option-10-altro"));
    expect(screen.getByTestId("btn-approve-10")).toBeDisabled();

    fireEvent.change(screen.getByTestId("free-text-10"), { target: { value: "Reddit" } });
    fireEvent.click(screen.getByTestId("btn-approve-10"));
    expect(onDecide).toHaveBeenCalledWith(10, {
      status: "APPROVED",
      selected_options: [],
      custom_text: "Reddit",
    });
  });

  it("multi choice: checkboxes, several options plus Altro", () => {
    const onDecide = renderCard(multi);
    expect(screen.getByTestId("option-11-ai")).toHaveAttribute("type", "checkbox");
    fireEvent.click(screen.getByTestId("option-11-ai"));
    fireEvent.click(screen.getByTestId("option-11-dev"));
    fireEvent.click(screen.getByTestId("option-11-dev"));
    fireEvent.click(screen.getByTestId("option-11-ai"));
    fireEvent.click(screen.getByTestId("option-11-dev"));
    fireEvent.click(screen.getByTestId("option-11-altro"));
    fireEvent.change(screen.getByTestId("free-text-11"), { target: { value: "opensource" } });
    fireEvent.click(screen.getByTestId("btn-approve-11"));
    expect(onDecide).toHaveBeenCalledWith(11, {
      status: "APPROVED",
      selected_options: ["dev"],
      custom_text: "opensource",
    });
  });

  it("rejecting a choice question sends no options", () => {
    const onDecide = renderCard(single);
    fireEvent.click(screen.getByTestId("option-10-Instagram"));
    fireEvent.click(screen.getByTestId("btn-reject-10"));
    expect(onDecide).toHaveBeenCalledWith(10, { status: "REJECTED", selected_options: [] });
  });

  it("confirmation: optional note sent as custom_text", () => {
    const onDecide = renderCard(confirm);
    expect(screen.getByTestId("btn-approve-12")).not.toBeDisabled();
    fireEvent.change(screen.getByTestId("free-text-12"), { target: { value: "Solo in bozza" } });
    fireEvent.click(screen.getByTestId("btn-approve-12"));
    expect(onDecide).toHaveBeenCalledWith(12, {
      status: "APPROVED",
      selected_options: [],
      custom_text: "Solo in bozza",
    });
  });

  it("prompt review: unchanged prompt is not sent as edited", () => {
    const onDecide = renderCard({ ...base, id: 13, recipient_id: "gemini", question_type: "prompt_review", prompt: "Analizza", options: [] });
    fireEvent.click(screen.getByTestId("btn-approve-13"));
    expect(onDecide).toHaveBeenCalledWith(13, { status: "APPROVED", selected_options: [] });
  });
});

describe("deep links", () => {
  it("parses #request-N", () => {
    expect(requestIdFromHash("#request-42")).toBe(42);
    expect(requestIdFromHash("#request-x")).toBeNull();
    expect(requestIdFromHash("")).toBeNull();
  });

  describe("in the app", () => {
    class MockEventSource {
      addEventListener() {}
      close() {}
    }

    beforeEach(() => {
      global.EventSource = MockEventSource;
      const requests = [
        { ...single, status: "PENDING" },
        { ...confirm, id: 20, status: "APPROVED", custom_text: "ok ma piano", selected_options: [] },
      ];
      global.fetch = vi.fn((url) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(url === "/api/agents" ? { agents: {}, rules: {} } : requests),
        })
      );
    });

    it("opens the history tab and highlights a decided request", async () => {
      window.location.hash = "#request-20";
      render(<App />);
      await waitFor(() => {
        expect(screen.getByTestId("history-card-20")).toHaveClass("card-highlight");
      });
      expect(screen.getByTestId("history-answer-20")).toHaveTextContent("Nota: ok ma piano");
    });

    it("highlights a pending request in the home", async () => {
      window.location.hash = "#request-10";
      render(<App />);
      await waitFor(() => {
        expect(screen.getByTestId("request-card-10")).toHaveClass("card-highlight");
      });
    });
  });
});

describe("deep link after answering", () => {
  class MockEventSource {
    addEventListener(_, cb) {
      MockEventSource.emit = cb;
    }
    close() {}
  }

  it("stays on the home when the linked request gets answered", async () => {
    global.EventSource = MockEventSource;
    const pending = { ...base, id: 30, question_type: "confirmation", prompt: "Ok?", options: [] };
    const other = { ...base, id: 31, question_type: "confirmation", prompt: "Altra?", options: [] };
    global.fetch = vi.fn((url) =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(url === "/api/agents" ? { agents: {}, rules: {} } : [pending, other]),
      })
    );
    window.location.hash = "#request-30";
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("request-card-30")).toBeInTheDocument());

    // The request is answered: an SSE update moves it out of PENDING.
    MockEventSource.emit({ data: JSON.stringify({ ...pending, status: "APPROVED" }) });
    await waitFor(() => expect(screen.queryByTestId("request-card-30")).toBeNull());
    expect(screen.getByTestId("home-section")).toBeInTheDocument();
    expect(screen.getByTestId("request-card-31")).toBeInTheDocument();
  });
});

describe("history timeline", () => {
  it("loads and shows the audit events of a request on demand", async () => {
    global.EventSource = class {
      addEventListener() {}
      close() {}
    };
    const done = { ...base, id: 40, question_type: "confirmation", prompt: "Ok?", options: [], status: "APPROVED" };
    const events = [
      { id: 1, request_id: 40, at: "2026-10-14T10:00:00+00:00", actor: "agent:claude", event: "created", details: {} },
      { id: 2, request_id: 40, at: "2026-10-14T10:01:00+00:00", actor: "human:paolo@test", event: "decided", details: { status: "APPROVED", custom_text: "va bene" } },
      { id: 3, request_id: 40, at: "2026-10-14T10:05:00+00:00", actor: "bridge", event: "delivered", details: { via: "tmux" } },
    ];
    global.fetch = vi.fn((url) =>
      Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve(
            url === "/api/agents" ? { agents: {}, rules: {} } : url.endsWith("/events") ? events : [done]
          ),
      })
    );
    window.location.hash = "";
    render(<App />);
    fireEvent.click(await screen.findByTestId("tab-history"));
    fireEvent.click(await screen.findByTestId("timeline-toggle-40"));

    const list = await screen.findByTestId("timeline-40");
    await waitFor(() => expect(list).toHaveTextContent("Esito consegnato al mittente"));
    expect(list).toHaveTextContent("tu (paolo@test)");
    expect(list).toHaveTextContent("APPROVED · “va bene”");
    expect(list).toHaveTextContent("via tmux");
    expect(global.fetch).toHaveBeenCalledWith("/api/requests/40/events");
  });
});

describe("priority badge", () => {
  it("shows urgent and high, hides normal", () => {
    const { unmount } = render(<RequestCard req={{ ...confirm, priority: "urgent" }} onDecide={vi.fn()} />);
    expect(screen.getByTestId("priority-12")).toHaveTextContent("Urgente");
    unmount();
    render(<RequestCard req={{ ...confirm, priority: "normal" }} onDecide={vi.fn()} />);
    expect(screen.queryByTestId("priority-12")).toBeNull();
  });
});

describe("delivery states in history", () => {
  it("labels new statuses and marks implicit replies", async () => {
    global.EventSource = class {
      addEventListener() {}
      close() {}
    };
    const stalled = { ...base, id: 50, recipient_id: "gemini", question_type: "prompt_review", prompt: "x", options: [], status: "STALLED" };
    const implicit = {
      ...base, id: 51, recipient_id: "gemini", question_type: "prompt_review", prompt: "y", options: [], status: "COMPLETED",
      response: { responder_id: "gemini", content: "Fa 4.", implicit: true },
    };
    global.fetch = vi.fn((url) =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(url === "/api/agents" ? { agents: {}, rules: {} } : [stalled, implicit]),
      })
    );
    window.location.hash = "";
    render(<App />);
    fireEvent.click(await screen.findByTestId("tab-history"));
    expect(await screen.findByTestId("history-card-50")).toHaveTextContent("Bloccata");
    expect(screen.getByTestId("implicit-51")).toHaveTextContent("implicita");
  });
});

describe("delivery timing for agent approvals", () => {
  const req = { ...base, id: 80, recipient_id: "gemini", question_type: "prompt_review",
    prompt: "Feedback per il lavoro in corso", recipient_session_id: "g1" };
  const sessions = [
    { id: "g1", agent_id: "gemini", provider: "antigravity", title: "Sviluppo", state: "busy" },
    { id: "g2", agent_id: "gemini", provider: "antigravity", title: "Altra sessione", state: "idle" },
  ];
  it("defaults to queue and sends now only when explicitly chosen", () => {
    const onDecide = vi.fn();
    render(<RequestCard req={req} sessions={sessions} onDecide={onDecide} />);
    expect(screen.getByRole("radio", { name: /In coda/ })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /Subito/ }));
    fireEvent.click(screen.getByTestId("btn-approve-80"));
    expect(onDecide).toHaveBeenCalledWith(80, { status: "APPROVED", selected_options: [],
      recipient_session_id: "g1", delivery_mode: "now" });
  });
  it("resets to queue when the selected recipient changes", () => {
    const onDecide = vi.fn();
    render(<RequestCard req={req} sessions={sessions} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("radio", { name: /Subito/ }));
    fireEvent.change(screen.getByTestId("target-session-80"), { target: { value: "g2" } });
    expect(screen.queryByTestId("delivery-mode-80")).toBeNull();
    fireEvent.click(screen.getByTestId("btn-approve-80"));
    expect(onDecide).toHaveBeenCalledWith(80, { status: "APPROVED", selected_options: [], recipient_session_id: "g2" });
  });
  it("sends the prompt to another agent's session when the user chooses it", () => {
    const onDecide = vi.fn();
    const all = [...sessions, { id: "c9", agent_id: "codex", provider: "codex", title: "Review", state: "idle" }];
    render(<RequestCard req={req} sessions={all} onDecide={onDecide} />);
    fireEvent.change(screen.getByTestId("target-session-80"), { target: { value: "c9" } });
    expect(screen.getByTestId("redirected-80")).toHaveTextContent("codex");
    fireEvent.click(screen.getByTestId("btn-approve-80"));
    expect(onDecide).toHaveBeenCalledWith(80, { status: "APPROVED", selected_options: [], recipient_session_id: "c9" });
  });
  it("does not offer now for Codex or an automatic recipient", () => {
    const { rerender } = render(<RequestCard req={{ ...req, recipient_id: "codex" }}
      sessions={[{ ...sessions[0], agent_id: "codex", provider: "codex" }]} onDecide={vi.fn()} />);
    expect(screen.queryByTestId("delivery-mode-80")).toBeNull();
    rerender(<RequestCard req={req} sessions={sessions} onDecide={vi.fn()} />);
    fireEvent.change(screen.getByTestId("target-session-80"), { target: { value: "" } });
    expect(screen.queryByTestId("delivery-mode-80")).toBeNull();
  });
});

it("holds completed answers for human editing and forwarding", () => {
  const req = { ...confirm, sender_id: "claude", recipient_id: "gemini", status: "COMPLETED", response: { content: "original" } };
  const decide = renderCard(req);
  expect(screen.getByText(/La risposta attende il tuo consenso/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Risposta da inoltrare"), { target: { value: "edited" } });
  fireEvent.click(screen.getByText("Inoltra risposta"));
  expect(decide).toHaveBeenCalledWith(12, { return_decision: true, approve: true, edited_response: "edited" });
});

it("can withhold a completed answer", () => {
  const decide = renderCard({ ...confirm, sender_id: "claude", recipient_id: "gemini", status: "COMPLETED", response: { content: "answer" } });
  fireEvent.click(screen.getByText("Non inoltrare"));
  expect(decide).toHaveBeenCalledWith(12, { return_decision: true, approve: false });
});

it("requires explicit opt-in for automatic return", () => {
  const decide = renderCard({ ...confirm, sender_id: "claude", recipient_id: "gemini", question_type: "prompt_review" });
  const checkbox = screen.getByLabelText("Consenti anche il ritorno automatico della risposta");
  expect(checkbox).not.toBeChecked();
  fireEvent.click(checkbox);
  fireEvent.click(screen.getByTestId("btn-approve-12"));
  expect(decide.mock.calls[0][1].allow_auto_return).toBe(true);
});
