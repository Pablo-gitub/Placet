import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import { ProjectDetailView } from "../Projects";
import { json, mockFetch } from "./testUtils";

const node = (id, name, extra = {}) => ({ id, name, provider: "claude-code", session_id: null, instructions: "PRIVATE", public_role: null,
  coordinator_view: false, coordinator_supported: true, coordinator_session: null, ...extra });
const pending = { state: "pending_confirmation", session: { id: "pm-new", title: "Nuova sessione del PM", provider: "claude-code",
  started_at: "2026-10-23T08:30:00Z" } };
const project = {
  id: 1, name: "Optees", description: "", relation_rule: "", max_auto_hops: 3, max_auto_per_hour: 10, created_at: "2026-10-19T10:00:00Z",
  instructions: "", effective_rule: "", effective_max_hops: 3, effective_max_per_hour: 20, relations: [],
  nodes: [node(1, "PM", { public_role: "Plans and assigns the work", coordinator_view: true, coordinator_session: { state: "confirmed" } }),
    node(2, "Developer")],
};

function serve(extra = []) {
  return mockFetch([
    ...extra,
    ["/api/projects/1", () => project],
    [(u, o) => /\/api\/projects\/1\/nodes\/\d+\/(view-grant|public-role)$/.test(u) && o.method, (u, o) => json({ ok: true, text: o.body ? JSON.parse(o.body).text : undefined })],
    ["/api/nodes", []], ["/api/relations", []], ["/api/relation-types", []], ["/api/sessions", []],
  ]);
}
const calls = (fetchMock, method, suffix) => fetchMock.mock.calls.filter(([u, o]) => o?.method === method && u.endsWith(suffix));

describe("coordinator view on the project page (RFC-17)", () => {
  const initialNodes = project.nodes.map((n) => ({ ...n }));
  beforeEach(async () => { await i18n.changeLanguage("it"); project.nodes = initialNodes.map((n) => ({ ...n })); });
  afterEach(() => vi.unstubAllGlobals());

  it("shows a switch per node, off unless the user turned it on, with what it requires", async () => {
    serve();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const pm = await screen.findByTestId("project-node-1");
    const dev = screen.getByTestId("project-node-2");
    expect(within(pm).getByRole("switch", { name: "Vista coordinatore" })).toBeChecked();
    expect(within(dev).getByRole("switch", { name: "Vista coordinatore" })).not.toBeChecked();
    const hint = within(dev).getByTestId("coordinator-hint-2");
    expect(hint).toHaveTextContent(/sessione verificata/i);
    expect(hint).toHaveTextContent(/non autorizza/i);                       // reading is not sending
  });

  it("grants and revokes through the API", async () => {
    const fetchMock = serve();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const dev = await screen.findByTestId("project-node-2");
    fireEvent.click(within(dev).getByRole("switch", { name: "Vista coordinatore" }));
    await waitFor(() => expect(calls(fetchMock, "PUT", "/nodes/2/view-grant")).toHaveLength(1));
    const pm = screen.getByTestId("project-node-1");
    fireEvent.click(within(pm).getByRole("switch", { name: "Vista coordinatore" }));
    await waitFor(() => expect(calls(fetchMock, "DELETE", "/nodes/1/view-grant")).toHaveLength(1));
  });

  it("edits the public role apart from the private instructions", async () => {
    const fetchMock = serve();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const pm = await screen.findByTestId("project-node-1");
    const role = within(pm).getByTestId("public-role-input-1");
    expect(role).toHaveValue("Plans and assigns the work");
    expect(role).toHaveAttribute("maxlength", "500");
    expect(within(pm).getByTestId("node-instructions-input-1")).toHaveValue("PRIVATE");
    const save = within(pm).getByTestId("save-public-role-btn-1");
    expect(save).toBeDisabled();
    fireEvent.change(role, { target: { value: "  Coordinates the team " } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(calls(fetchMock, "PUT", "/nodes/1/public-role")).toHaveLength(1));
    expect(JSON.parse(calls(fetchMock, "PUT", "/nodes/1/public-role")[0][1].body)).toEqual({ text: "Coordinates the team" });
  });

  it("says in the public role field that everyone with the view can read it", async () => {
    serve();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const dev = await screen.findByTestId("project-node-2");
    expect(within(dev).getByTestId("public-role-hint-2")).toHaveTextContent(/visibile ai nodi coordinatori/i);
  });

  it("is in English too", async () => {
    await i18n.changeLanguage("en");
    serve();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const dev = await screen.findByTestId("project-node-2");
    expect(within(dev).getByRole("switch", { name: "Coordinator view" })).toBeInTheDocument();
    expect(within(dev).getByTestId("coordinator-hint-2")).toHaveTextContent(/verified session/i);
    await i18n.changeLanguage("it");
  });

  it("reports a refused change and leaves the switch where it was", async () => {
    serve([[(u, o) => o?.method === "PUT" && u.endsWith("/nodes/2/view-grant"),
      Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ code: "project.node_not_in_project", detail: "x" }) })]]);
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const dev = await screen.findByTestId("project-node-2");
    const toggle = within(dev).getByRole("switch", { name: "Vista coordinatore" });
    fireEvent.click(toggle);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(toggle).not.toBeChecked();
  });

  it("asks the user to confirm a new session, showing which one and when it started, never a credential", async () => {
    project.nodes[0] = node(1, "PM", { coordinator_view: true, coordinator_session: pending });
    const fetchMock = serve([[(u, o) => o?.method === "POST" && u.endsWith("/nodes/1/view-grant/confirm"), json({ state: "confirmed" })]]);
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const pm = await screen.findByTestId("project-node-1");
    const box = within(pm).getByTestId("coordinator-pending-1");
    expect(box).toHaveTextContent("Nuova sessione del PM");
    expect(box).toHaveTextContent("claude-code");
    expect(box).toHaveTextContent(/avviata/i);
    expect(within(pm).getByRole("switch", { name: "Vista coordinatore" })).toBeChecked();      // still granted, but waiting
    fireEvent.click(within(box).getByRole("button", { name: "Conferma sessione" }));
    await waitFor(() => expect(calls(fetchMock, "POST", "/nodes/1/view-grant/confirm")).toHaveLength(1));
    expect(JSON.parse(calls(fetchMock, "POST", "/nodes/1/view-grant/confirm")[0][1].body)).toEqual({ session_id: "pm-new" });
  });

  it("shows nothing to confirm for a confirmed session, and says when no session serves the node", async () => {
    project.nodes[0] = node(1, "PM", { coordinator_view: true, coordinator_session: { state: "confirmed" } });
    project.nodes[1] = node(2, "Developer", { coordinator_view: true, coordinator_session: { state: "no_session" } });
    serve();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const pm = await screen.findByTestId("project-node-1");
    expect(within(pm).queryByTestId("coordinator-pending-1")).toBeNull();
    expect(within(screen.getByTestId("project-node-2")).getByTestId("coordinator-nosession-2")).toBeInTheDocument();
  });

  it("warns that a provider without a verified channel will be refused", async () => {
    project.nodes[1] = node(2, "Developer", { provider: "codex", coordinator_supported: false });
    serve();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const dev = await screen.findByTestId("project-node-2");
    expect(within(dev).getByTestId("coordinator-unsupported-2")).toHaveTextContent(/canale verificato/i);
    expect(within(screen.getByTestId("project-node-1")).queryByTestId("coordinator-unsupported-1")).toBeNull();
  });

  it("confirms in English too", async () => {
    await i18n.changeLanguage("en");
    project.nodes[0] = node(1, "PM", { coordinator_view: true, coordinator_session: pending });
    serve();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const pm = await screen.findByTestId("project-node-1");
    expect(within(pm).getByRole("button", { name: "Confirm session" })).toBeInTheDocument();
    await i18n.changeLanguage("it");
  });
});
