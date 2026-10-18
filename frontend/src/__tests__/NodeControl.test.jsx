import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import NodeControl from "../NodeControl";
import i18n from "../i18n";
import { json, mockFetch } from "./testUtils";

const session = { id: "s1", provider: "claude-code", node_id: null, node_name: null };
const nodes = [
  { id: 1, name: "reviewer", provider: "claude-code" },
  { id: 2, name: "builder", provider: "codex" },
];

describe("NodeControl", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("names a session and lists only nodes of the same app", async () => {
    const fetch = mockFetch([
      ["/api/nodes", (url, opts) => (opts.method === "POST" ? json({ id: 3 }, 201) : nodes)],
    ]);
    vi.stubGlobal("fetch", fetch);
    render(<NodeControl session={session} />);
    fireEvent.click(screen.getByTestId("node-chip"));
    const attach = await screen.findByTestId("node-attach");
    expect(attach).toHaveTextContent("reviewer");
    expect(attach).not.toHaveTextContent("builder");
    fireEvent.change(screen.getByTestId("node-name"), { target: { value: "a/b" } });
    expect(screen.getByTestId("node-save")).toBeDisabled();
    fireEvent.change(screen.getByTestId("node-name"), { target: { value: "lab main" } });
    fireEvent.click(screen.getByTestId("node-save"));
    await waitFor(() => expect(screen.queryByTestId("node-name")).toBeNull());
    const [, opts] = fetch.mock.calls.find(([, o]) => o?.method === "POST");
    expect(JSON.parse(opts.body)).toEqual({ name: "lab main", session_id: "s1" });
  });

  it("shows the node name and a taken name as an error", async () => {
    vi.stubGlobal("fetch", mockFetch([
      ["/api/nodes", []],
      ["/api/nodes/1", () => json({ code: "node.name_taken" }, 409)],
    ]));
    render(<NodeControl session={{ ...session, node_id: 1, node_name: "reviewer" }} />);
    expect(screen.getByTestId("node-chip")).toHaveTextContent("reviewer");
    fireEvent.click(screen.getByTestId("node-chip"));
    fireEvent.change(screen.getByTestId("node-name"), { target: { value: "builder" } });
    fireEvent.click(screen.getByTestId("node-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("errors.nodeNameTaken"));
  });
});
