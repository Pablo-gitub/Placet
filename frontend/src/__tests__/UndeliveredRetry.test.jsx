import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import Undelivered from "../Undelivered";
import MessageDetail from "../MessageDetail";
import { mockFetch } from "./testUtils";

const base = {
  question_type: "prompt_review", status: "APPROVED", sender_id: "codex", recipient_id: "gemini",
  sender_session_id: "cx-1", recipient_session_id: "gm-1", created_at: "2026-10-22T20:00:00Z",
  updated_at: "2026-10-22T20:01:00Z", delivery: "waiting", scheduled_at: null,
};
const item = (id, cause, extra = {}, request = {}) => ({
  request: { ...base, id, prompt: `Messaggio ${id}`, ...request }, cause, due_at: null, sender_node: null, recipient_node: null,
  can_cancel: true, can_forward: true, can_retry: false, ...extra,
});
const sessions = [{ id: "gm-2", agent_id: "gemini", provider: "antigravity", state: "idle", started_at: "2026-10-22T19:00:00Z" }];
const rows = [
  item(10, "blocked", { can_retry: true }),
  item(11, "blocked", { can_retry: true, can_cancel: false, can_forward: false }, { status: "DELIVERY_FAILED", delivery: "failed" }),
  item(13, "awaiting_approval", {}, { status: "PENDING" }),
];

function serve() {
  return mockFetch([
    ["/api/requests/undelivered", () => rows],
    [(u, o) => /\/undelivered\/retry$/.test(u) && o.method === "POST", { id: 10, status: "APPROVED" }],
  ]);
}
const posts = (fetchMock) => fetchMock.mock.calls.filter(([, o]) => o?.method === "POST");

describe("retry delivery to the same session", () => {
  beforeEach(async () => { await i18n.changeLanguage("it"); });
  afterEach(() => vi.restoreAllMocks());

  it("is offered on a blocked message and sends it back to the same session", async () => {
    const fetchMock = serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-10");
    fireEvent.click(within(row).getByText("#10"));
    fireEvent.click(within(row).getByRole("button", { name: "Riprova consegna" }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(posts(fetchMock)[0][0]).toBe("/api/requests/10/undelivered/retry");
    expect(posts(fetchMock)[0][1].body).toBeUndefined();                // no session to name: it stays where it was
  });

  it("is the only action of a failed delivery", async () => {
    serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-11");
    fireEvent.click(within(row).getByText("#11"));
    expect(within(row).getByRole("button", { name: "Riprova consegna" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Inoltra" })).toBeNull();
    expect(within(row).queryByRole("button", { name: "Annulla messaggio" })).toBeNull();
  });

  it("is not offered while a message waits for approval", async () => {
    serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-13");
    fireEvent.click(within(row).getByText("#13"));
    expect(within(row).queryByRole("button", { name: "Riprova consegna" })).toBeNull();
    expect(within(row).getByRole("button", { name: "Inoltra" })).toBeInTheDocument();
  });

  it("reports a refusal in place", async () => {
    mockFetch([
      ["/api/requests/undelivered", () => rows],
      [(u, o) => o.method === "POST", Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({ code: "undelivered.not_available", detail: "x" }) })],
    ]);
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-10");
    fireEvent.click(within(row).getByText("#10"));
    fireEvent.click(within(row).getByRole("button", { name: "Riprova consegna" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("già iniziato a essere consegnato");
  });

  it("is in English too", async () => {
    await i18n.changeLanguage("en");
    serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-10");
    fireEvent.click(within(row).getByText("#10"));
    expect(within(row).getByRole("button", { name: "Retry delivery" })).toBeInTheDocument();
    await i18n.changeLanguage("it");
  });

  it("is also offered where the message is read in full (right pane)", async () => {
    const fetchMock = serve();
    render(<MessageDetail requestId={10} requests={[rows[0].request]} sessions={sessions} onBack={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Riprova consegna" }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(posts(fetchMock)[0][0]).toBe("/api/requests/10/undelivered/retry");
  });
});
