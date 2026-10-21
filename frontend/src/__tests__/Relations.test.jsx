import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { CreateRelation, NodeRelations, RelationRule } from "../Relations";
import { json, mockFetch } from "./testUtils";

const bodies = (fetch, method, url) =>
  fetch.mock.calls.filter(([u, o]) => u === url && o?.method === method).map(([, o]) => JSON.parse(o.body));

describe("Relations", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("creates a relation from a request, naming the sessions and the type", async () => {
    const fetch = mockFetch([
      ["/api/relation-types", (url, o) => (o.method === "POST" ? json({ id: 7 }, 201) : [])],
      ["/api/nodes", (url, o) => json({ id: JSON.parse(o.body).session_id === "cl" ? 1 : 2 }, 201)],
      ["/api/relations", () => json({ id: 9 }, 201)],
    ]);
    vi.stubGlobal("fetch", fetch);
    render(<CreateRelation sender={{ id: "cl", title: "Planner" }} recipient={{ id: "cx", node_id: 2, node_name: "builder" }} />);
    fireEvent.click(screen.getByTestId("create-relation"));
    expect(screen.getByTestId("relation-save")).toBeDisabled();
    fireEvent.change(screen.getByTestId("relation-from-name"), { target: { value: "planner" } });
    fireEvent.change(await screen.findByTestId("relation-type-name"), { target: { value: "review" } });
    fireEvent.change(screen.getByTestId("relation-type-description"), { target: { value: "Ask for a review" } });
    fireEvent.click(screen.getByTestId("relation-automatic"));
    fireEvent.click(screen.getByTestId("relation-save"));
    await screen.findByRole("status");
    expect(bodies(fetch, "POST", "/api/nodes")).toEqual([{ name: "planner", session_id: "cl" }]);
    expect(bodies(fetch, "POST", "/api/relation-types")).toEqual([{ name: "review", description: "Ask for a review" }]);
    expect(bodies(fetch, "POST", "/api/relations")).toEqual([{ from_node: 1, to_node: 2, type_id: 7, approval: "automatic", reply_approval: "automatic" }]);
  });

  it("lists a node's relations and changes their policy", async () => {
    const fetch = mockFetch([
      ["/api/relations?node_id=1", [{ id: 4, from_name: "planner", to_name: "builder", type_name: "review", approval: "manual" }]],
      ["/api/nodes", [{ id: 1, name: "planner" }, { id: 2, name: "builder" }]],
      ["/api/relations/4", () => json({ id: 4 })],
    ]);
    vi.stubGlobal("fetch", fetch);
    render(<NodeRelations node={{ id: 1, name: "planner" }} />);
    const select = await screen.findByTestId("relation-approval-4");
    fireEvent.change(select, { target: { value: "automatic" } });
    await waitFor(() => expect(bodies(fetch, "PATCH", "/api/relations/4")).toEqual([{ approval: "automatic" }]));
  });

  it("lets the reply policy differ from the outbound one (RFC-16)", async () => {
    const fetch = mockFetch([
      ["/api/relation-types", (url, o) => (o.method === "POST" ? json({ id: 7 }, 201) : [])],
      ["/api/nodes", (url, o) => json({ id: JSON.parse(o.body).session_id === "cl" ? 1 : 2 }, 201)],
      ["/api/relations", () => json({ id: 9 }, 201)],
    ]);
    vi.stubGlobal("fetch", fetch);
    render(<CreateRelation sender={{ id: "cl", title: "Planner" }} recipient={{ id: "cx", node_id: 2, node_name: "builder" }} />);
    fireEvent.click(screen.getByTestId("create-relation"));
    fireEvent.change(screen.getByTestId("relation-from-name"), { target: { value: "planner" } });
    fireEvent.change(await screen.findByTestId("relation-type-name"), { target: { value: "review" } });
    const reply = screen.getByTestId("relation-reply-toggle");
    expect(reply).not.toBeChecked();
    fireEvent.click(screen.getByTestId("relation-automatic"));
    // The reply follows the outbound policy until the user changes it.
    expect(reply).toBeChecked();
    fireEvent.click(reply);
    fireEvent.click(screen.getByTestId("relation-save"));
    await screen.findByRole("status");
    expect(bodies(fetch, "POST", "/api/relations")).toEqual([{ from_node: 1, to_node: 2, type_id: 7, approval: "automatic", reply_approval: "manual" }]);
  });

  it("changes the reply policy of an existing relation", async () => {
    const fetch = mockFetch([
      ["/api/relations?node_id=1", [{ id: 4, from_name: "planner", to_name: "builder", type_name: "review", approval: "automatic", reply_approval: "manual" }]],
      ["/api/nodes", [{ id: 1, name: "planner" }, { id: 2, name: "builder" }]],
      ["/api/relations/4", () => json({ id: 4 })],
    ]);
    vi.stubGlobal("fetch", fetch);
    render(<NodeRelations node={{ id: 1, name: "planner" }} />);
    const select = await screen.findByTestId("relation-reply-4");
    expect(select).toHaveValue("manual");
    fireEvent.change(select, { target: { value: "automatic" } });
    await waitFor(() => expect(bodies(fetch, "PATCH", "/api/relations/4")).toEqual([{ reply_approval: "automatic" }]));
  });

  it("edits the general rule for automatic relations", async () => {
    const fetch = mockFetch([
      ["/api/settings/relation-rule", (url, o) => (o.method === "PUT" ? json(JSON.parse(o.body)) : { rule: "" })],
    ]);
    vi.stubGlobal("fetch", fetch);
    render(<RelationRule />);
    fireEvent.click(screen.getByTestId("relation-rule-open"));
    const text = await screen.findByTestId("relation-rule-text");
    fireEvent.change(text, { target: { value: "Ask first" } });
    fireEvent.click(screen.getByTestId("relation-rule-save"));
    await waitFor(() => expect(bodies(fetch, "PUT", "/api/settings/relation-rule")).toEqual([{ rule: "Ask first" }]));
  });
});
