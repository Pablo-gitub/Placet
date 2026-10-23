import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import Undelivered from "../Undelivered";
import Settings from "../Settings";
import { mockFetch } from "./testUtils";

const base = {
  question_type: "prompt_review", status: "APPROVED", sender_id: "codex", recipient_id: "gemini",
  sender_session_id: "cx-1", recipient_session_id: "gm-1", created_at: "2026-10-22T20:00:00Z",
  updated_at: "2026-10-22T20:01:00Z", delivery: "waiting", project_name: null, scheduled_at: null,
};
const item = (id, cause, extra = {}, request = {}) => ({
  request: { ...base, id, prompt: `Messaggio numero ${id}\n\nsecondo paragrafo`, ...request },
  cause, due_at: null, sender_node: null, recipient_node: null, can_cancel: true, can_forward: true, ...extra,
});
const sessions = [
  { id: "cx-1", agent_id: "codex", provider: "codex", state: "busy", started_at: "2026-10-22T19:00:00Z", activity_at: "2026-10-22T20:00:00Z", node_name: "planner" },
  { id: "gm-1", agent_id: "gemini", provider: "antigravity", state: "idle", started_at: "2026-10-22T19:00:00Z", activity_at: "2026-10-22T20:00:00Z" },
  { id: "gm-2", agent_id: "gemini", provider: "antigravity", state: "idle", title: "Altra Gemini", started_at: "2026-10-22T19:00:00Z", activity_at: "2026-10-22T20:00:00Z" },
  { id: "old", agent_id: "gemini", provider: "antigravity", state: "idle", ended_at: "2026-10-22T19:30:00Z", started_at: "2026-10-22T19:00:00Z", activity_at: "2026-10-22T19:30:00Z" },
];
const rows = [
  item(10, "blocked", {}, { status: "APPROVED" }),
  item(314, "awaiting_refresh", { due_at: "2026-10-23T09:00:00Z", recipient_node: "reviewer", sender_node: "planner" },
    { scheduled_at: "2026-10-23T09:00:00Z", project_name: "Optees Finance" }),
  item(12, "scheduled", { due_at: "2026-10-23T07:30:00Z" }, { scheduled_at: "2026-10-23T07:30:00Z" }),
  item(13, "awaiting_approval", {}, { status: "PENDING" }),
];

function serve(list = rows) {
  return mockFetch([
    ["/api/requests/undelivered", () => list],
    [(url, o) => /\/api\/requests\/\d+\/undelivered\/(cancel|forward)$/.test(url) && o.method === "POST", { ok: true }],
  ]);
}
const posts = (fetchMock) => fetchMock.mock.calls.filter(([, o]) => o?.method === "POST");

describe("undelivered messages section", () => {
  const original = window.matchMedia;
  beforeEach(async () => { await i18n.changeLanguage("it"); });
  afterEach(() => { window.matchMedia = original; vi.restoreAllMocks(); });

  it("groups the messages by cause, in the order of urgency, and says when a held one leaves", async () => {
    serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    const section = await screen.findByTestId("undelivered-section");
    expect(within(section).getByRole("heading", { name: "Messaggi non consegnati" })).toBeInTheDocument();
    const groups = await within(section).findAllByTestId(/^undelivered-group-/);
    expect(groups.map((g) => g.dataset.testid)).toEqual([
      "undelivered-group-blocked", "undelivered-group-awaiting_refresh",
      "undelivered-group-scheduled", "undelivered-group-awaiting_approval"]);
    expect(within(groups[0]).getByText("Bloccati")).toBeInTheDocument();
    expect(within(groups[1]).getByText("In attesa del refresh")).toBeInTheDocument();
    expect(within(groups[2]).getByText("Programmati")).toBeInTheDocument();
    expect(within(groups[3]).getByText("In attesa di approvazione")).toBeInTheDocument();
    const held = screen.getByTestId("undelivered-row-314");
    expect(held).toHaveTextContent("#314");
    expect(held).toHaveTextContent("planner");
    expect(held).toHaveTextContent("reviewer");
    expect(held).toHaveTextContent("Optees Finance");
    expect(held.querySelector("time")).toHaveAttribute("datetime", "2026-10-23T09:00:00Z");
  });

  it("shows an empty state, and none of the groups, when nothing waits", async () => {
    serve([]);
    render(<Undelivered requests={[]} sessions={sessions} />);
    expect(await screen.findByTestId("undelivered-empty")).toHaveTextContent("Nessun messaggio in attesa di consegna");
    expect(screen.queryByTestId("undelivered-group-blocked")).toBeNull();
  });

  it("is in English too", async () => {
    await i18n.changeLanguage("en");
    serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    expect(await screen.findByText("Undelivered messages")).toBeInTheDocument();
    expect(screen.getByText("Waiting for the refresh")).toBeInTheDocument();
    await i18n.changeLanguage("it");
  });

  it("opens the full text in a disclosure and keeps the actions inside it", async () => {
    serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-12");
    expect(row.tagName).toBe("DETAILS");
    expect(row).not.toHaveAttribute("open");
    fireEvent.click(within(row).getByText("#12"));
    expect(row).toHaveAttribute("open");
    expect(within(row).getByText("secondo paragrafo")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Annulla messaggio" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Inoltra" })).toBeInTheDocument();
  });

  it("asks before cancelling, and sends nothing when the user steps back", async () => {
    const fetchMock = serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-12");
    fireEvent.click(within(row).getByText("#12"));
    fireEvent.click(within(row).getByRole("button", { name: "Annulla messaggio" }));
    expect(within(row).getByText(/Annullare il messaggio #12\?/)).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Indietro" }));
    expect(posts(fetchMock)).toHaveLength(0);
    fireEvent.click(within(row).getByRole("button", { name: "Annulla messaggio" }));
    fireEvent.click(within(row).getByRole("button", { name: "Sì, annulla" }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(posts(fetchMock)[0][0]).toBe("/api/requests/12/undelivered/cancel");
  });

  it("forwards to another open session, never to the sender's or the current one", async () => {
    const fetchMock = serve();
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-12");
    fireEvent.click(within(row).getByText("#12"));
    fireEvent.click(within(row).getByRole("button", { name: "Inoltra" }));
    const picker = within(row).getByRole("combobox", { name: "Inoltra a…" });
    const offered = within(picker).getAllByRole("option").map((o) => o.value).filter(Boolean);
    expect(offered).toEqual(["gm-2"]);                                  // not cx-1 (sender), gm-1 (current), old (ended)
    const go = within(row).getByRole("button", { name: "Inoltra a questa sessione" });
    expect(go).toBeDisabled();
    fireEvent.change(picker, { target: { value: "gm-2" } });
    fireEvent.click(go);
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(posts(fetchMock)[0][0]).toBe("/api/requests/12/undelivered/forward");
    expect(JSON.parse(posts(fetchMock)[0][1].body)).toEqual({ session_id: "gm-2" });
  });

  it("refreshes by itself when a request changes (live through the existing SSE feed)", async () => {
    const fetchMock = serve();
    const { rerender } = render(<Undelivered requests={[]} sessions={sessions} />);
    await screen.findByTestId("undelivered-row-12");
    const before = fetchMock.mock.calls.filter(([url]) => url === "/api/requests/undelivered").length;
    rerender(<Undelivered requests={[{ id: 12, status: "CANCELLED", updated_at: "2026-10-22T21:00:00Z" }]} sessions={sessions} />);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === "/api/requests/undelivered").length).toBeGreaterThan(before));
  });

  it("reports a refused action instead of failing silently", async () => {
    mockFetch([
      ["/api/requests/undelivered", () => rows],
      [(url, o) => o.method === "POST", Promise.resolve({ ok: false, status: 409, json: () => Promise.resolve({ code: "undelivered.not_available", detail: "x" }) })],
    ]);
    render(<Undelivered requests={[]} sessions={sessions} />);
    const row = await screen.findByTestId("undelivered-row-12");
    fireEvent.click(within(row).getByText("#12"));
    fireEvent.click(within(row).getByRole("button", { name: "Annulla messaggio" }));
    fireEvent.click(within(row).getByRole("button", { name: "Sì, annulla" }));
    expect(await within(row).findByRole("alert")).toHaveTextContent("già iniziato a essere consegnato");
  });

  it("on a wide screen a click opens the message in the right pane instead of a disclosure", async () => {
    window.matchMedia = (query) => ({ matches: query.includes("min-width"), media: query, addEventListener() {}, removeEventListener() {} });
    serve();
    const open = vi.fn();
    render(<Undelivered requests={[]} sessions={sessions} onOpenMessage={open} />);
    const row = await screen.findByTestId("undelivered-row-12");
    expect(row.tagName).not.toBe("DETAILS");
    fireEvent.click(within(row).getByRole("button", { name: /#12/ }));
    expect(open).toHaveBeenCalledWith(12);
  });

  it("is part of Settings", async () => {
    serve();
    render(<Settings requests={[]} sessions={sessions} />);
    expect(await screen.findByTestId("undelivered-section")).toBeInTheDocument();
  });
});
