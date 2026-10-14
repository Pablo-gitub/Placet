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

    it("highlights a pending request in the inbox", async () => {
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

  it("stays on the inbox when the linked request gets answered", async () => {
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
    expect(screen.getByTestId("inbox-section")).toBeInTheDocument();
    expect(screen.getByTestId("request-card-31")).toBeInTheDocument();
  });
});
