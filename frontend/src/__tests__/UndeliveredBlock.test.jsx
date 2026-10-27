import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import Undelivered from "../Undelivered";
import { mockFetch } from "./testUtils";

const base = {
  question_type: "message", status: "APPROVED", sender_id: "human", recipient_id: "gemini", sender_session_id: null,
  recipient_session_id: "ag1", created_at: "2026-10-27T08:00:00Z", updated_at: "2026-10-27T08:01:00Z", delivery: "waiting", scheduled_at: null,
};
const block = (extra = {}) => ({ session_id: "ag1", code: "missing_user_config", failed_attempts: 2,
  last_checked_at: "2026-10-27T08:30:00Z", next_retry_at: "2026-10-27T08:35:00Z", ...extra });
const item = (id, extra = {}) => ({ request: { ...base, id, prompt: `Messaggio ${id}` }, cause: "delivery_blocked", due_at: null,
  sender_node: null, recipient_node: null, can_cancel: true, can_forward: true, can_retry: false, block: block(), ...extra });
const sessions = [{ id: "gm-2", agent_id: "gemini", provider: "antigravity", state: "idle", started_at: "2026-10-27T07:00:00Z" }];

function serve(rows) {
  return mockFetch([
    ["/api/requests/undelivered", () => rows],
    [(u, o) => /\/api\/sessions\/[^/]+\/delivery-block\/retry$/.test(u) && o.method === "POST", { retry: "requested" }],
  ]);
}

describe("deliveries blocked by the channel", () => {
  beforeEach(async () => { await i18n.changeLanguage("it"); });
  afterEach(() => vi.restoreAllMocks());

  it("says why, once for the session, with the number of failed attempts, in the first group", async () => {
    serve([item(1), item(2), item(3), { ...item(9), cause: "scheduled", block: null }]);
    render(<Undelivered requests={[]} sessions={sessions} />);
    const groups = await screen.findAllByTestId(/^undelivered-group-/);
    expect(groups[0].dataset.testid).toBe("undelivered-group-delivery_blocked");
    expect(within(groups[0]).getByText("Consegna bloccata", { selector: "h4" })).toBeInTheDocument();
    const notes = within(groups[0]).getAllByTestId("delivery-block-ag1");
    expect(notes).toHaveLength(1);                                             // three requests, one explanation
    expect(notes[0]).toHaveTextContent("Antigravity non espone la configurazione di questa conversazione");
    expect(notes[0]).toHaveTextContent("Apri la conversazione, verifica modello e impostazioni, invia un messaggio, poi Riprova");
    expect(notes[0]).toHaveTextContent("2 tentativi falliti");
    for (const id of [1, 2, 3]) expect(within(groups[0]).getByTestId(`undelivered-row-${id}`)).toBeInTheDocument();
  });

  it("keeps cancel and forward on each held request", async () => {
    serve([item(1)]);
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-1");
    fireEvent.click(within(row).getByText("#1"));
    expect(within(row).getByRole("button", { name: "Inoltra" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Annulla messaggio" })).toBeInTheDocument();
  });

  it("retries through the session and reloads", async () => {
    const fetchMock = serve([item(1), item(2)]);
    render(<Undelivered requests={[]} sessions={sessions} />);
    fireEvent.click(await screen.findByRole("button", { name: "Riprova consegna" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u, o]) => u === "/api/sessions/ag1/delivery-block/retry" && o.method === "POST")).toBe(true));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([u]) => u === "/api/requests/undelivered").length).toBeGreaterThan(1));
  });

  it("uses a general text for a failure it has no special words for", async () => {
    serve([item(1, { block: block({ code: "conversation_unavailable", failed_attempts: 1 }) })]);
    render(<Undelivered requests={[]} sessions={sessions} />);
    const note = await screen.findByTestId("delivery-block-ag1");
    expect(note).toHaveTextContent("Consegna bloccata");
    expect(note).toHaveTextContent("1 tentativo fallito");
    expect(note).not.toHaveTextContent("configurazione");
  });

  it("is in English too", async () => {
    await i18n.changeLanguage("en");
    serve([item(1)]);
    render(<Undelivered requests={[]} sessions={sessions} />);
    const note = await screen.findByTestId("delivery-block-ag1");
    expect(note).toHaveTextContent("Delivery blocked: Antigravity does not expose the configuration of this conversation");
    expect(note).toHaveTextContent("2 failed attempts");
    expect(within(note).getByRole("button", { name: "Retry delivery" })).toBeInTheDocument();
    await i18n.changeLanguage("it");
  });
});
