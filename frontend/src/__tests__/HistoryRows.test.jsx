import React from "react";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import History from "../History";

const long = "Prima riga del messaggio.\n\nSeconda parte che nella riga compatta non deve comparire intera.";
const requests = [
  { id: 41, sender_id: "codex", recipient_id: "gemini", status: "COMPLETED", question_type: "prompt_review", prompt: long,
    created_at: "2026-10-22T20:00:00Z", delivery: "accepted", execution: "completed", reply: "explicit", allow_auto_return: true,
    response: { responder_id: "gemini", content: "Risposta di Gemini", implicit: false } },
  { id: 42, sender_id: "claude", recipient_id: "human", status: "REJECTED", question_type: "yes_no", prompt: "Procedo?",
    created_at: "2026-10-22T20:05:00Z", delivery: "waiting", execution: "unobserved", reply: "none" },
];

describe("history rows", () => {
  const original = window.matchMedia;
  beforeEach(async () => { await i18n.changeLanguage("it"); global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })); });
  afterEach(() => { window.matchMedia = original; });

  it("shows the request number and a one-line summary on every row", () => {
    render(<History requests={requests} sessions={[]} />);
    for (const req of requests) {
      const row = screen.getByTestId(`history-card-${req.id}`);
      expect(within(row).getByText(`#${req.id}`)).toBeInTheDocument();
      expect(row.querySelector("summary")).toHaveTextContent(`#${req.id}`);
      expect(row.querySelector("summary")).toHaveTextContent(req.sender_id);
      expect(row.querySelector("summary time")).not.toBeNull();
    }
    // The summary carries the first line only: the rest is behind the disclosure.
    const summary = screen.getByTestId("history-card-41").querySelector("summary");
    expect(summary.querySelector(".message-preview")).toHaveAttribute("data-preview", "Prima riga del messaggio.");
    expect(summary).not.toHaveTextContent("Seconda parte");
  });

  it("keeps the full message and the reply behind a disclosure", () => {
    render(<History requests={requests} sessions={[]} />);
    const row = screen.getByTestId("history-card-41");
    expect(row.tagName).toBe("DETAILS");
    expect(row).not.toHaveAttribute("open");
    fireEvent.click(row.querySelector("summary"));
    expect(row).toHaveAttribute("open");
    expect(within(row).getByText(/Seconda parte che nella riga compatta/)).toBeInTheDocument();
    expect(within(row).getByText("Risposta di Gemini", { exact: false })).toBeInTheDocument();
  });

  it("opens the row of the notification link", () => {
    render(<History requests={requests} sessions={[]} focusId={42} />);
    expect(screen.getByTestId("history-card-42")).toHaveAttribute("open");
    expect(screen.getByTestId("history-card-41")).not.toHaveAttribute("open");
  });

  it("on a wide screen a row opens the message in the right pane", () => {
    window.matchMedia = (query) => ({ matches: query.includes("min-width"), media: query, addEventListener() {}, removeEventListener() {} });
    const open = vi.fn();
    render(<History requests={requests} sessions={[]} onOpenMessage={open} />);
    const row = screen.getByTestId("history-card-41");
    expect(row.tagName).not.toBe("DETAILS");
    fireEvent.click(within(row).getByRole("button", { name: /#41/ }));
    expect(open).toHaveBeenCalledWith(41);
  });
});
